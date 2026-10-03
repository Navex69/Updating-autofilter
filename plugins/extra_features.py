"""
Extra admin features — drop-in plugin (auto-loaded, no other file needs editing).

  1. /send         reply to a message + user ids -> bot copies it to those users
  2. /id           user info (clickable name, mono id, joined group(s))
  3. /ban /unban /showban   bot-level ban system (works in PM and in groups)
  4. /delete <link>, /deleteall   remove indexed files from MongoDB / channel
  5. /checklimit /resetlimit /resetlimitall   daily free-file limit control
  6. /show_groups /leave_groups   manage groups the bot is admin in
  7. /broadcast    any media type, optional pin, live progress, cancel + undo
  8. /stats        full dashboard (channels, users, groups, DB storage, server)
  9. Link guard    non-admins can't post links (deleted + warning, never searched)
 10. Autofilter ON/OFF (set in /settings) + /filterwords /set_filterword /remove_filterword
 11. /trending    most searched titles (correct spellings, 10 per page)

plugins/search.py calls prepare_query() and record_trending() from this file.

Helper commands:  /extra (command list)   /syncusers (import old users)

New MongoDB collections (this file creates them itself):
  bot_users, bot_groups, banned_users
"""
import asyncio
import functools
import hashlib
import html
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne
from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, RPCError, MessageNotModified, UserIsBlocked, InputUserDeactivated,
    UserDeactivated, PeerIdInvalid, UserNotParticipant, ChannelPrivate, ChannelInvalid,
)
from pyrogram.types import InlineKeyboardMarkup as Markup, InlineKeyboardButton as Btn

from config import ADMINS, ENABLE_PM_SEARCH
from database.client import db
from database.filters_db import files, get_file_by_id, display_name, count_by_channel, clean_title, search_files
from database.premium_db import premium_col
from database.limit_db import limit_col
from database.settings_db import get_settings, update_settings
from utils import IST, Throttle, readable_time

logger = logging.getLogger(__name__)

users_col = db["bot_users"]
groups_col = db["bot_groups"]
banned_col = db["banned_users"]

ADMIN = filters.user(ADMINS)
PRIVATE_ADMIN = filters.private & ADMIN

esc = html.escape


# ══════════════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p")


def _name_of(u) -> str:
    return " ".join(p for p in (u.first_name, u.last_name) if p) or "User"


def user_link(uid: int, name: str | None = None, username: str | None = None) -> str:
    """Clickable name (opens profile) + clickable @username."""
    out = f'<a href="tg://user?id={uid}">{esc(name or str(uid))}</a>'
    if username:
        out += f' (<a href="https://t.me/{esc(username)}">@{esc(username)}</a>)'
    return out


def group_link(doc: dict) -> str:
    title = esc(doc.get("title") or str(doc["_id"]))
    href = None
    if doc.get("username"):
        href = f"https://t.me/{doc['username']}"
    elif doc.get("invite_link"):
        href = doc["invite_link"]
    return f'<a href="{href}">{title}</a>' if href else f"<b>{title}</b>"


def adder_text(doc: dict) -> str:
    a = doc.get("added_by")
    if not a:
        return "Unknown <i>(bot was added before tracking started)</i>"
    return f"{user_link(a['id'], a.get('name'), a.get('username'))} — <code>{a['id']}</code>"


def _purge(store: dict, ttl: int = 900):
    cutoff = time.time() - ttl
    for k in [k for k, v in store.items() if v.get("ts", 0) < cutoff]:
        store.pop(k, None)


async def _safe_edit(msg, text, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(min(e.value, 5))
    except RPCError:
        logger.debug("edit failed", exc_info=True)


async def get_user_info(bot, uid: int) -> dict:
    doc = await users_col.find_one({"_id": uid})
    if doc and doc.get("name"):
        return {"id": uid, "name": doc["name"], "username": doc.get("username")}
    try:
        u = await bot.get_users(uid)
        return {"id": uid, "name": _name_of(u), "username": u.username}
    except Exception:
        return {"id": uid, "name": None, "username": None}


async def resolve_target(bot, message, arg: str | None = None) -> int | None:
    """User id from an explicit arg (id / @username) or from the replied message."""
    if arg:
        arg = arg.strip()
        if re.fullmatch(r"\d+", arg):
            return int(arg)
        uname = arg.lstrip("@")
        doc = await users_col.find_one({"username": re.compile(f"^{re.escape(uname)}$", re.I)})
        if doc:
            return doc["_id"]
        try:
            return (await bot.get_users(uname)).id
        except Exception:
            return None
    r = message.reply_to_message
    if r:
        if message.chat.type == enums.ChatType.PRIVATE and r.forward_from:
            return r.forward_from.id
        if r.from_user:
            return r.from_user.id
    return None


async def _copy_to(bot, src_chat: int, src_msg: int, uid: int, media_group: bool = False) -> list:
    """Copy one message (any media type, or a whole album) to `uid`. Returns sent messages."""
    for attempt in (1, 2):
        try:
            if media_group:
                return await bot.copy_media_group(uid, src_chat, src_msg)
            return [await bot.copy_message(uid, src_chat, src_msg)]
        except FloodWait as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.value + 1)


# ══════════════════════════════════════════════════════════════════════════════
# TRACKING — users, groups, who added the bot (needed by /broadcast, /id,
# /show_groups, /ban). Cheap: one DB write per user per 10 min at most.
# ══════════════════════════════════════════════════════════════════════════════

_seen: dict = {}
_SEEN_TTL = 600


@Client.on_message((filters.private | filters.group) & ~filters.service, group=-3)
async def track_activity(bot, message):
    try:
        u = message.from_user
        if not u or u.is_bot:
            return
        is_group = message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
        key = (u.id, message.chat.id if is_group else 0)
        now = time.time()
        if now - _seen.get(key, 0) < _SEEN_TTL:
            return
        _seen[key] = now

        update = {
            "$set": {"name": _name_of(u), "username": u.username, "active": True, "last_seen": _now()},
            "$setOnInsert": {"joined_at": _now()},
        }
        if is_group:
            update["$addToSet"] = {"groups": message.chat.id}
        await users_col.update_one({"_id": u.id}, update, upsert=True)

        if is_group:
            await groups_col.update_one(
                {"_id": message.chat.id},
                {"$set": {"title": message.chat.title, "username": message.chat.username},
                 "$setOnInsert": {"added_by": None, "added_at": _now(), "left": False}},
                upsert=True,
            )
    except Exception:
        logger.debug("track_activity failed", exc_info=True)


async def _record_bot_added(bot, chat, adder):
    info = None
    if adder and not adder.is_bot:
        info = {"id": adder.id, "name": _name_of(adder), "username": adder.username}
    await groups_col.update_one(
        {"_id": chat.id},
        {"$set": {"title": chat.title, "username": chat.username, "left": False,
                  "added_by": info, "added_at": _now()}},
        upsert=True,
    )


@Client.on_message(filters.new_chat_members, group=-3)
async def bot_added_service_msg(bot, message):
    try:
        if any(u.id == bot.me.id for u in message.new_chat_members):
            await _record_bot_added(bot, message.chat, message.from_user)
    except Exception:
        logger.debug("bot_added_service_msg failed", exc_info=True)


@Client.on_chat_member_updated(group=-3)
async def bot_membership_changed(bot, update):
    """Fallback for 'who added the bot' + marks groups the bot was removed from."""
    try:
        new = update.new_chat_member
        if not new or new.user.id != bot.me.id:
            return
        if update.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            return
        gone = (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED)
        if new.status in gone:
            await groups_col.update_one({"_id": update.chat.id}, {"$set": {"left": True}})
            return
        old = update.old_chat_member
        if old is None or old.status in gone:
            await _record_bot_added(bot, update.chat, update.from_user)
    except Exception:
        logger.debug("bot_membership_changed failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# BAN GATE — runs before every other plugin (group -2) and blocks banned users.
# Admins can never be banned.
# ══════════════════════════════════════════════════════════════════════════════

_banned: set | None = None
_ban_notified: dict = {}


async def _load_banned():
    global _banned
    if _banned is None:
        _banned = {d["_id"] async for d in banned_col.find({}, {"_id": 1})}
    return _banned


async def _is_banned_update(_, __, update):
    u = getattr(update, "from_user", None)
    if not u or u.id in ADMINS:
        return False
    return u.id in await _load_banned()


banned_filter = filters.create(_is_banned_update)


@Client.on_message(banned_filter, group=-2)
async def banned_message_gate(bot, message):
    # PM: tell the user once in a while. Groups: stay silent (no spam).
    if message.chat.type == enums.ChatType.PRIVATE:
        uid = message.from_user.id
        if time.time() - _ban_notified.get(uid, 0) > 600:
            _ban_notified[uid] = time.time()
            doc = await banned_col.find_one({"_id": uid}) or {}
            try:
                await message.reply_text(_ban_text(doc.get("reason")))
            except RPCError:
                pass
    message.stop_propagation()


@Client.on_callback_query(banned_filter, group=-2)
async def banned_callback_gate(bot, query):
    await query.answer("🚫 You are banned from using this bot.", show_alert=True)
    query.stop_propagation()


def _ban_text(reason: str | None) -> str:
    text = "🚫 <b>You have been banned from using this bot.</b>"
    if reason:
        text += f"\n\n<b>Reason:</b> {esc(reason)}"
    return text


# ══════════════════════════════════════════════════════════════════════════════
# 3. /ban  /unban  /showban
# ══════════════════════════════════════════════════════════════════════════════

_pending_ban: dict = {}   # admin_id -> {uid, chat_id, prompt_id, ts}
_BAN_WAIT = 300


def _ban_prompt_markup(uid: int):
    return Markup([[
        Btn("🔨 Ban without reason", callback_data=f"xban#go#{uid}"),
        Btn("✖️ Cancel", callback_data=f"xban#cancel#{uid}"),
    ]])


async def do_ban(bot, uid: int, reason: str | None, by: int) -> tuple[dict, bool]:
    info = await get_user_info(bot, uid)
    await banned_col.update_one(
        {"_id": uid},
        {"$set": {"name": info["name"], "username": info["username"], "reason": reason,
                  "banned_by": by, "banned_at": _now()}},
        upsert=True,
    )
    (await _load_banned()).add(uid)
    notified = True
    try:
        await bot.send_message(uid, _ban_text(reason))
    except RPCError:
        notified = False
    return info, notified


async def _ban_result(bot, uid, reason, by):
    info, notified = await do_ban(bot, uid, reason, by)
    text = (
        f"✅ <b>Banned</b> {user_link(uid, info['name'], info['username'])}\n"
        f"🆔 <code>{uid}</code>\n"
        f"📝 <b>Reason:</b> {esc(reason) if reason else '<i>none</i>'}"
    )
    if not notified:
        text += "\n\n⚠️ Couldn't DM the user (they haven't started the bot or blocked it)."
    return text, Markup([[Btn("🔓 Unban", callback_data=f"xban#unban#{uid}")]])


@Client.on_message(filters.command("ban") & ADMIN)
async def ban_cmd(bot, message):
    args = message.command[1:]
    uid = await resolve_target(bot, message, args[0] if args else None)
    if uid is None:
        await message.reply_text(
            "<b>Usage:</b>\n<code>/ban user_id</code>  or  reply to a user with <code>/ban</code>\n"
            "You can also use <code>/ban @username</code> for users the bot has seen."
        )
        return
    if uid in ADMINS or uid == bot.me.id:
        await message.reply_text("⛔ You can't ban an admin or the bot itself.")
        return

    existing = await banned_col.find_one({"_id": uid})
    if existing:
        await message.reply_text(
            f"⚠️ {user_link(uid, existing.get('name'), existing.get('username'))} "
            f"(<code>{uid}</code>) is already banned.\n"
            f"📝 <b>Reason:</b> {esc(existing.get('reason') or '—')}",
            reply_markup=Markup([[Btn("🔓 Unban", callback_data=f"xban#unban#{uid}")]]),
            disable_web_page_preview=True,
        )
        return

    info = await get_user_info(bot, uid)
    prompt = await message.reply_text(
        f"🔨 <b>Ban user</b>\n\n{user_link(uid, info['name'], info['username'])}\n"
        f"🆔 <code>{uid}</code>\n\n"
        f"✍️ Send the <b>ban reason</b> now — it will be sent to the user.\n"
        f"Or tap <b>Ban without reason</b>.",
        reply_markup=_ban_prompt_markup(uid),
        disable_web_page_preview=True,
    )
    _purge(_pending_ban, _BAN_WAIT)
    _pending_ban[message.from_user.id] = {
        "uid": uid, "chat_id": message.chat.id, "prompt_id": prompt.id, "ts": time.time(),
    }


async def _awaiting_reason(_, __, message):
    u = message.from_user
    if not u or not message.text or message.text.startswith("/"):
        return False
    p = _pending_ban.get(u.id)
    return bool(p and p["chat_id"] == message.chat.id and time.time() - p["ts"] < _BAN_WAIT)


@Client.on_message(filters.create(_awaiting_reason) & ADMIN, group=-1)
async def ban_reason_received(bot, message):
    p = _pending_ban.pop(message.from_user.id)
    reason = message.text.strip()[:500]
    text, markup = await _ban_result(bot, p["uid"], reason, message.from_user.id)
    try:
        prompt = await bot.get_messages(p["chat_id"], p["prompt_id"])
        await _safe_edit(prompt, text, markup)
    except RPCError:
        await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
    message.stop_propagation()   # don't let the search plugin treat the reason as a query


@Client.on_callback_query(filters.regex(r"^xban#") & ADMIN)
async def ban_callbacks(bot, query):
    _, action, raw = query.data.split("#")
    uid = int(raw)
    admin_id = query.from_user.id

    if action == "cancel":
        _pending_ban.pop(admin_id, None)
        await query.message.edit_text("✖️ Ban cancelled.")
        return

    if action == "go":
        p = _pending_ban.pop(admin_id, None)
        if not p or p["uid"] != uid:
            await query.answer("This ban request expired — send /ban again.", show_alert=True)
            return
        text, markup = await _ban_result(bot, uid, None, admin_id)
        await query.answer()
        await _safe_edit(query.message, text, markup)
        return

    if action == "unban":
        info = await _do_unban(bot, uid)
        await query.answer()
        await _safe_edit(query.message, info)


async def _do_unban(bot, uid: int) -> str:
    doc = await banned_col.find_one_and_delete({"_id": uid})
    (await _load_banned()).discard(uid)
    if not doc:
        return f"ℹ️ <code>{uid}</code> is not banned."
    _ban_notified.pop(uid, None)
    note = ""
    try:
        await bot.send_message(uid, "✅ <b>You have been unbanned.</b> You can use the bot again.")
    except RPCError:
        note = "\n⚠️ Couldn't DM the user."
    return f"✅ <b>Unbanned</b> {user_link(uid, doc.get('name'), doc.get('username'))} (<code>{uid}</code>){note}"


@Client.on_message(filters.command("unban") & ADMIN)
async def unban_cmd(bot, message):
    args = message.command[1:]
    uid = await resolve_target(bot, message, args[0] if args else None)
    if uid is None:
        await message.reply_text(
            "<b>Usage:</b> <code>/unban user_id</code> or reply to a user with <code>/unban</code>"
        )
        return
    await message.reply_text(await _do_unban(bot, uid), disable_web_page_preview=True)


_BAN_PAGE = 8


async def _render_banlist(page: int):
    total = await banned_col.count_documents({})
    if not total:
        return "✅ No banned users.", Markup([[Btn("✖️ Close", callback_data="sb#close")]])
    pages = (total + _BAN_PAGE - 1) // _BAN_PAGE
    page = max(0, min(page, pages - 1))
    docs = await banned_col.find({}).sort("banned_at", -1).skip(page * _BAN_PAGE).limit(_BAN_PAGE).to_list(_BAN_PAGE)

    lines = [f"🚫 <b>Banned users</b> — {total}\n"]
    for i, d in enumerate(docs, start=page * _BAN_PAGE + 1):
        lines.append(
            f"<b>{i}.</b> {user_link(d['_id'], d.get('name'), d.get('username'))}\n"
            f"    🆔 <code>{d['_id']}</code>\n"
            f"    📝 {esc(d.get('reason') or 'No reason')}\n"
            f"    🕒 {_fmt_dt(d.get('banned_at'))}"
        )
    nav = []
    if page > 0:
        nav.append(Btn("◀️ Prev", callback_data=f"sb#p#{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn("Next ▶️", callback_data=f"sb#p#{page + 1}"))
    return "\n".join(lines), Markup([nav, [Btn("✖️ Close", callback_data="sb#close")]])


@Client.on_message(filters.command("showban") & ADMIN)
async def showban_cmd(_, message):
    text, markup = await _render_banlist(0)
    await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^sb#") & ADMIN)
async def showban_callbacks(_, query):
    parts = query.data.split("#")
    if parts[1] == "close":
        await query.message.delete()
        return
    text, markup = await _render_banlist(int(parts[2]))
    await query.answer()
    await _safe_edit(query.message, text, markup)


# ══════════════════════════════════════════════════════════════════════════════
# 1. /send — reply to a message, give one or more user ids
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("send") & ADMIN)
async def send_cmd(bot, message):
    reply = message.reply_to_message
    tokens = message.command[1:]
    if not reply or not tokens:
        await message.reply_text(
            "<b>Usage:</b> reply to the message you want to send with\n"
            "<code>/send user_id [user_id2 user_id3 …]</code>\n\n"
            "Works with text, photos, videos, files — anything."
        )
        return

    targets, bad = [], []
    for t in tokens:
        uid = await resolve_target(bot, message, t)
        (targets if uid is not None else bad).append(uid if uid is not None else t)
    targets = list(dict.fromkeys(targets))
    if not targets:
        await message.reply_text("❌ None of those look like valid users.")
        return

    status = await message.reply_text(f"📤 Sending to <code>{len(targets)}</code> user(s)…")
    ok, failed = [], []
    for uid in targets:
        try:
            await _copy_to(bot, reply.chat.id, reply.id, uid, bool(reply.media_group_id))
            ok.append(uid)
        except UserIsBlocked:
            failed.append((uid, "blocked the bot"))
        except (InputUserDeactivated, UserDeactivated):
            failed.append((uid, "account deleted"))
        except PeerIdInvalid:
            failed.append((uid, "never started the bot"))
        except RPCError as e:
            failed.append((uid, e.__class__.__name__))
        await asyncio.sleep(0.05)

    lines = [f"📤 <b>Send report</b>\n\n✅ Sent: <code>{len(ok)}</code>   ❌ Failed: <code>{len(failed) + len(bad)}</code>"]
    if ok:
        lines.append("\n<b>Delivered to:</b>\n" + "\n".join(f"• <code>{u}</code>" for u in ok))
    if failed or bad:
        lines.append("\n<b>Failed:</b>")
        lines += [f"• <code>{u}</code> — {esc(why)}" for u, why in failed]
        lines += [f"• <code>{esc(str(b))}</code> — invalid user" for b in bad]
    await _safe_edit(status, "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# 2. /id
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("id") & ADMIN)
async def id_cmd(bot, message):
    args = message.command[1:]
    r = message.reply_to_message
    uid = None

    if args:
        uid = await resolve_target(bot, message, args[0])
        if uid is None:
            await message.reply_text("❌ Couldn't find that user. Use a numeric id or reply to their message.")
            return
    elif r:
        if message.chat.type == enums.ChatType.PRIVATE and r.forward_sender_name and not r.forward_from:
            await message.reply_text("🔒 That user hides their account on forwards, so their id isn't available.")
            return
        uid = await resolve_target(bot, message)
    else:
        uid = message.from_user.id   # no reply, no arg -> your own info

    if uid is None:
        await message.reply_text("❌ Couldn't work out which user you mean.")
        return

    info = await get_user_info(bot, uid)
    doc = await users_col.find_one({"_id": uid}) or {}
    lines = [
        "🆔 <b>User info</b>\n",
        f"👤 <b>Name:</b> {user_link(uid, info['name'])}",
        "🔖 <b>Username:</b> " + (f'<a href="https://t.me/{esc(info["username"])}">@{esc(info["username"])}</a>' if info["username"] else "—"),
        f"🆔 <b>User ID:</b> <code>{uid}</code>",
    ]
    if await banned_col.find_one({"_id": uid}, {"_id": 1}):
        lines.append("🚫 <b>Status:</b> banned from this bot")

    in_group = message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
    if in_group:
        g = await groups_col.find_one({"_id": message.chat.id}) or {"_id": message.chat.id, "title": message.chat.title, "username": message.chat.username}
        line = f"👥 <b>Group:</b> {group_link(g)} (<code>{message.chat.id}</code>)"
        try:
            member = await bot.get_chat_member(message.chat.id, uid)
            if getattr(member, "joined_date", None):
                line += f"\n📅 <b>Joined group:</b> {_fmt_dt(member.joined_date)}"
        except RPCError:
            pass
        lines.append(line)

    other_ids = [g for g in doc.get("groups", []) if not (in_group and g == message.chat.id)]
    if other_ids:
        gdocs = await groups_col.find({"_id": {"$in": other_ids}, "left": {"$ne": True}}).to_list(15)
        if gdocs:
            label = "Other groups" if in_group else "Groups joined"
            lines.append(f"\n👥 <b>{label}</b> <i>(seen by this bot)</i>:")
            lines += [f"• {group_link(g)} — <code>{g['_id']}</code>" for g in gdocs[:10]]
    elif not in_group:
        lines.append("👥 <b>Groups joined:</b> none seen yet")

    await message.reply_text("\n".join(lines), disable_web_page_preview=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4. /delete <file link>   /deleteall
# ══════════════════════════════════════════════════════════════════════════════

_pending_del: dict = {}
_CHANNEL_LINK = re.compile(r"t\.me/(?:c/(\d+)|([A-Za-z][A-Za-z0-9_]{3,}))/(?:\d+/)?(\d+)")
_BOT_FILE_LINK = re.compile(r"[?&]start=file_([0-9a-fA-F]{24})")


@Client.on_message(filters.command("delete") & ADMIN)
async def delete_cmd(bot, message):
    link = message.command[1] if len(message.command) > 1 else ""
    if not link:
        await message.reply_text(
            "<b>Usage:</b> <code>/delete file_link</code>\n\n"
            "• Channel post link: <code>https://t.me/c/1234567890/55</code> — can delete from DB and channel\n"
            "• Bot file link: <code>https://t.me/bot?start=file_…</code> — database only"
        )
        return

    entry = {"admin": message.from_user.id, "ts": time.time(), "chat_id": None, "msg_id": None, "doc": None}

    m = _BOT_FILE_LINK.search(link)
    if m:
        doc = await get_file_by_id(m.group(1))
        if not doc:
            await message.reply_text("❌ That file isn't in the database (already deleted?).")
            return
        entry["doc"] = doc
    else:
        m = _CHANNEL_LINK.search(link)
        if not m:
            await message.reply_text("❌ That doesn't look like a channel post link or a bot file link.")
            return
        chat_id = int("-100" + m.group(1)) if m.group(1) else m.group(2)
        msg_id = int(m.group(3))
        try:
            msg = await bot.get_messages(chat_id, msg_id)
        except RPCError as e:
            await message.reply_text(f"❌ Couldn't open that message: <code>{esc(str(e))}</code>")
            return
        media = None if (not msg or msg.empty) else (msg.document or msg.video)
        if not media:
            await message.reply_text("❌ No file found at that link (message deleted, or it isn't a document/video).")
            return
        entry.update(chat_id=chat_id, msg_id=msg_id)
        entry["doc"] = await files.find_one({"file_unique_id": media.file_unique_id})
        entry["unique_id"] = media.file_unique_id
        entry["file_name"] = getattr(media, "file_name", None) or "file"

    doc = entry["doc"]
    name = display_name(doc) if doc else entry.get("file_name", "file")
    tok = secrets.token_hex(4)
    _purge(_pending_del, 600)
    _pending_del[tok] = entry

    rows = []
    if doc and entry["chat_id"] is not None:
        rows.append([Btn("🗄 Delete from database", callback_data=f"dl#db#{tok}")])
        rows.append([Btn("🗄📢 Delete from database & channel", callback_data=f"dl#both#{tok}")])
        note = ""
    elif doc:
        rows.append([Btn("🗄 Delete from database", callback_data=f"dl#db#{tok}")])
        note = "\n<i>Channel delete needs a channel post link (bot file links don't contain the message id).</i>"
    else:
        rows.append([Btn("📢 Delete from channel only", callback_data=f"dl#ch#{tok}")])
        note = "\n⚠️ <i>This file is <b>not in the database</b>.</i>"
    rows.append([Btn("✖️ Cancel", callback_data=f"dl#cancel#{tok}")])

    await message.reply_text(
        f"🗑 <b>Delete this file?</b>\n\n📄 <code>{esc(name[:200])}</code>{note}",
        reply_markup=Markup(rows),
    )


@Client.on_callback_query(filters.regex(r"^dl#") & ADMIN)
async def delete_callbacks(bot, query):
    _, action, tok = query.data.split("#")
    entry = _pending_del.get(tok)
    if action == "cancel":
        _pending_del.pop(tok, None)
        await query.message.edit_text("✖️ Cancelled — nothing was deleted.")
        return
    if not entry:
        await query.answer("Expired — send /delete again.", show_alert=True)
        return
    _pending_del.pop(tok, None)

    doc = entry["doc"]
    db_ok = ch_ok = None
    errors = []

    if action in ("both", "ch"):
        try:
            await bot.delete_messages(entry["chat_id"], entry["msg_id"])
            ch_ok = True
        except RPCError as e:
            ch_ok = False
            errors.append(f"Channel: {e.__class__.__name__} — does the bot have <i>delete messages</i> permission?")
    if action in ("db", "both") and (action == "db" or ch_ok):
        res = await files.delete_one({"_id": doc["_id"]} if doc else {"file_unique_id": entry["unique_id"]})
        db_ok = res.deleted_count > 0

    lines = ["<b>Result</b>\n"]
    if db_ok is not None:
        lines.append("🗄 Database: " + ("✅ deleted" if db_ok else "⚠️ not found"))
    if ch_ok is not None:
        lines.append("📢 Channel: " + ("✅ deleted" if ch_ok else "❌ failed"))
    if action == "both" and not ch_ok:
        lines.append("🗄 Database: ⏸ left untouched (channel delete failed)")
    lines += [f"\n{e}" for e in errors]
    await query.answer()
    await _safe_edit(query.message, "\n".join(lines))


@Client.on_message(filters.command("deleteall") & PRIVATE_ADMIN)
async def deleteall_cmd(_, message):
    count = await files.count_documents({})
    if not count:
        await message.reply_text("ℹ️ The database has no indexed files.")
        return
    await message.reply_text(
        f"⚠️ <b>Delete ALL indexed files?</b>\n\nThis removes <code>{count}</code> file records from MongoDB.\n"
        f"✅ Channel posts are <b>not</b> touched.\n✅ User data (premium, limits, bans…) is <b>not</b> touched.",
        reply_markup=Markup([[Btn("⚠️ Yes, continue", callback_data="dla#ask")], [Btn("✖️ Cancel", callback_data="dla#cancel")]]),
    )


@Client.on_callback_query(filters.regex(r"^dla#") & ADMIN)
async def deleteall_callbacks(_, query):
    action = query.data.split("#")[1]
    if action == "cancel":
        await query.message.edit_text("✖️ Cancelled — nothing was deleted.")
    elif action == "back":
        count = await files.count_documents({})
        await query.message.edit_text(
            f"⚠️ <b>Delete ALL indexed files?</b>\n\nThis removes <code>{count}</code> file records from MongoDB.",
            reply_markup=Markup([[Btn("⚠️ Yes, continue", callback_data="dla#ask")], [Btn("✖️ Cancel", callback_data="dla#cancel")]]),
        )
    elif action == "ask":
        count = await files.count_documents({})
        await query.message.edit_text(
            f"🛑 <b>Final confirmation</b>\n\n<code>{count}</code> files will be erased from the database. This can't be undone "
            f"(you'd have to re-index the channels).",
            reply_markup=Markup([
                [Btn("🗑 Yes, delete everything", callback_data="dla#go")],
                [Btn("⬅️ Back", callback_data="dla#back"), Btn("✖️ Cancel", callback_data="dla#cancel")],
            ]),
        )
    elif action == "go":
        res = await files.delete_many({})
        await query.message.edit_text(f"✅ Deleted <code>{res.deleted_count}</code> file records from the database.")


# ══════════════════════════════════════════════════════════════════════════════
# 5. /checklimit  /resetlimit  /resetlimitall
# ══════════════════════════════════════════════════════════════════════════════

def _today_ist() -> str:
    return _now().astimezone(IST).strftime("%Y-%m-%d")


async def _usage(uid: int) -> int:
    doc = await limit_col.find_one({"_id": uid})
    return doc.get("count", 0) if doc and doc.get("date") == _today_ist() else 0


@Client.on_message(filters.command("checklimit") & ADMIN)
async def checklimit_cmd(bot, message):
    args = message.command[1:]
    uid = await resolve_target(bot, message, args[0] if args else None)
    if uid is None:
        await message.reply_text("<b>Usage:</b> <code>/checklimit user_id</code> or reply to a user with <code>/checklimit</code>")
        return
    info = await get_user_info(bot, uid)
    s = await get_settings()
    used, limit = await _usage(uid), s["file_limit_count"]
    active = s["file_limit_enabled"] and s["verify_enabled"]
    text = (
        f"📊 <b>File limit</b>\n\n{user_link(uid, info['name'], info['username'])}\n🆔 <code>{uid}</code>\n\n"
        f"📁 Used today: <code>{used}</code> / <code>{limit}</code>\n"
        f"➕ Remaining: <code>{max(limit - used, 0)}</code>\n"
        f"🔁 Resets daily at 12:00 AM IST\n\n"
        f"⚙️ Limit feature: {'🟢 active' if active else '🔴 not enforcing'}"
    )
    if not active:
        text += "\n<i>(needs both File Limit and Verification enabled in /settings)</i>"
    await message.reply_text(
        text,
        reply_markup=Markup([[Btn("♻️ Reset this user", callback_data=f"rl#one#{uid}"), Btn("✖️ Close", callback_data="rl#close")]]),
        disable_web_page_preview=True,
    )


async def _reset_one(uid: int) -> int:
    used = await _usage(uid)
    await limit_col.delete_one({"_id": uid})
    return used


@Client.on_message(filters.command("resetlimit") & ADMIN)
async def resetlimit_cmd(bot, message):
    args = message.command[1:]
    uid = await resolve_target(bot, message, args[0] if args else None)
    if uid is None:
        await message.reply_text("<b>Usage:</b> <code>/resetlimit user_id</code> or reply to a user with <code>/resetlimit</code>")
        return
    used = await _reset_one(uid)
    info = await get_user_info(bot, uid)
    await message.reply_text(
        f"✅ Limit reset for {user_link(uid, info['name'], info['username'])} (<code>{uid}</code>)\nWas: <code>{used}</code> used today → now <code>0</code>.",
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("resetlimitall") & PRIVATE_ADMIN)
async def resetlimitall_cmd(_, message):
    today_users = await limit_col.count_documents({"date": _today_ist()})
    await message.reply_text(
        f"⚠️ <b>Reset the daily limit for EVERY user?</b>\n\n👥 Users with usage today: <code>{today_users}</code>",
        reply_markup=Markup([[Btn("✅ Yes, reset all", callback_data="rl#all")], [Btn("✖️ Cancel", callback_data="rl#close")]]),
    )


@Client.on_callback_query(filters.regex(r"^rl#") & ADMIN)
async def limit_callbacks(bot, query):
    parts = query.data.split("#")
    if parts[1] == "close":
        await query.message.edit_text("✖️ Cancelled.")
    elif parts[1] == "one":
        uid = int(parts[2])
        used = await _reset_one(uid)
        await query.answer(f"Reset — was {used}, now 0", show_alert=True)
    elif parts[1] == "all":
        res = await limit_col.delete_many({})
        await query.message.edit_text(f"✅ Daily limit reset for everyone (<code>{res.deleted_count}</code> records cleared).")


# ══════════════════════════════════════════════════════════════════════════════
# 6. /show_groups  /leave_groups
# ══════════════════════════════════════════════════════════════════════════════

_group_cache: dict = {}
_GROUP_PAGE = 5
_GONE = (UserNotParticipant, ChannelPrivate, ChannelInvalid, PeerIdInvalid)


async def _bot_admin_groups(bot) -> list:
    """Stored groups where the bot is *currently* an admin (verified live)."""
    docs = await groups_col.find({"left": {"$ne": True}}).to_list(None)
    sem = asyncio.Semaphore(8)
    admin_states = (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)

    async def check(doc):
        async with sem:
            try:
                member = await bot.get_chat_member(doc["_id"], bot.me.id)
                if member.status not in admin_states:
                    return None
                chat = await bot.get_chat(doc["_id"])
                doc["title"], doc["username"] = chat.title, chat.username
                doc["invite_link"] = getattr(chat, "invite_link", None) or doc.get("invite_link")
                await groups_col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"title": doc["title"], "username": doc["username"], "invite_link": doc["invite_link"]}},
                )
                return doc
            except _GONE:
                await groups_col.update_one({"_id": doc["_id"]}, {"$set": {"left": True}})
            except RPCError:
                pass
            return None

    return [d for d in await asyncio.gather(*(check(d) for d in docs)) if d]


def _render_groups(groups: list, page: int):
    pages = max(1, (len(groups) + _GROUP_PAGE - 1) // _GROUP_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = groups[page * _GROUP_PAGE:(page + 1) * _GROUP_PAGE]
    lines = [f"👥 <b>Groups where I'm admin</b> — {len(groups)}\n"]
    leave_row = []
    for i, g in enumerate(chunk, start=page * _GROUP_PAGE + 1):
        lines.append(
            f"<b>{i}.</b> {group_link(g)}\n"
            f"    🆔 <code>{g['_id']}</code>\n"
            f"    👤 Added by: {adder_text(g)}"
        )
        leave_row.append(Btn(f"🚪 Leave #{i}", callback_data=f"lg#ask#{g['_id']}"))
    rows = [leave_row[j:j + 3] for j in range(0, len(leave_row), 3)]
    nav = []
    if page > 0:
        nav.append(Btn("◀️ Prev", callback_data=f"sg#p#{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn("Next ▶️", callback_data=f"sg#p#{page + 1}"))
    rows += [nav, [Btn("🔄 Refresh", callback_data="sg#refresh"), Btn("✖️ Close", callback_data="sg#close")]]
    return "\n".join(lines), Markup(rows)


@Client.on_message(filters.command("show_groups") & PRIVATE_ADMIN)
async def show_groups_cmd(bot, message):
    wait = await message.reply_text("🔎 Checking groups…")
    groups = await _bot_admin_groups(bot)
    if not groups:
        await wait.edit_text("ℹ️ I'm not an admin in any tracked group yet.")
        return
    _group_cache[message.from_user.id] = {"groups": groups, "ts": time.time()}
    text, markup = _render_groups(groups, 0)
    await _safe_edit(wait, text, markup)


@Client.on_callback_query(filters.regex(r"^sg#") & ADMIN)
async def show_groups_callbacks(bot, query):
    parts = query.data.split("#")
    action, admin_id = parts[1], query.from_user.id
    if action == "close":
        await query.message.delete()
        return
    cached = _group_cache.get(admin_id)
    if action == "refresh" or not cached or time.time() - cached["ts"] > 900:
        await query.answer("Refreshing…")
        groups = await _bot_admin_groups(bot)
        _group_cache[admin_id] = cached = {"groups": groups, "ts": time.time()}
    else:
        await query.answer()
    if not cached["groups"]:
        await query.message.edit_text("ℹ️ I'm not an admin in any tracked group.")
        return
    page = int(parts[2]) if action == "p" else 0
    text, markup = _render_groups(cached["groups"], page)
    await _safe_edit(query.message, text, markup)


async def _leave_confirm_text(bot, gid: int):
    doc = await groups_col.find_one({"_id": gid})
    if not doc:
        try:
            chat = await bot.get_chat(gid)
        except RPCError:
            return None, None
        if chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            return None, None
        doc = {"_id": gid, "title": chat.title, "username": chat.username, "added_by": None}
    return doc, (
        f"🚪 <b>Leave this group?</b>\n\n{group_link(doc)}\n🆔 <code>{gid}</code>\n"
        f"👤 Added by: {adder_text(doc)}\n\n"
        f"I'll post a goodbye message in the group and notify the person who added me."
    )


@Client.on_message(filters.command("leave_groups") & ADMIN)
async def leave_groups_cmd(bot, message):
    args = message.command[1:]
    if not args or not re.fullmatch(r"-?\d+", args[0]):
        await message.reply_text("<b>Usage:</b> <code>/leave_groups group_id</code>\nSee ids with /show_groups")
        return
    gid = int(args[0])
    doc, text = await _leave_confirm_text(bot, gid)
    if not doc:
        await message.reply_text("❌ I can't find that group (wrong id, or I'm not in it).")
        return
    await message.reply_text(
        text,
        reply_markup=Markup([[Btn("✅ Leave", callback_data=f"lg#go#{gid}"), Btn("✖️ Cancel", callback_data="lg#cancel#0")]]),
        disable_web_page_preview=True,
    )


@Client.on_callback_query(filters.regex(r"^lg#") & ADMIN)
async def leave_groups_callbacks(bot, query):
    _, action, raw = query.data.split("#")
    gid = int(raw)
    if action == "cancel":
        await query.message.edit_text("✖️ Cancelled — I'm staying.")
        return
    if action == "ask":
        doc, text = await _leave_confirm_text(bot, gid)
        if not doc:
            await query.answer("Can't find that group any more.", show_alert=True)
            return
        await query.answer()
        await query.message.reply_text(
            text,
            reply_markup=Markup([[Btn("✅ Leave", callback_data=f"lg#go#{gid}"), Btn("✖️ Cancel", callback_data="lg#cancel#0")]]),
            disable_web_page_preview=True,
        )
        return

    # action == "go"
    doc = await groups_col.find_one({"_id": gid}) or {"_id": gid, "title": str(gid)}
    title = esc(doc.get("title") or str(gid))
    report = []

    try:
        await bot.send_message(gid, "👋 <b>Goodbye!</b>\n\nThis bot is leaving the group on the owner's decision.")
        report.append("💬 Goodbye message posted in the group")
    except RPCError:
        report.append("⚠️ Couldn't post in the group")

    adder = doc.get("added_by")
    if adder:
        try:
            await bot.send_message(
                adder["id"],
                f"ℹ️ I've left <b>{title}</b> (<code>{gid}</code>) — the group where you added me — "
                f"on the bot admin's decision.",
            )
            report.append(f"📨 Notified {user_link(adder['id'], adder.get('name'), adder.get('username'))}")
        except RPCError:
            report.append("⚠️ Couldn't DM the user who added me")
    else:
        report.append("ℹ️ Adder unknown — nobody to notify")

    try:
        await bot.leave_chat(gid)
        await groups_col.update_one({"_id": gid}, {"$set": {"left": True}}, upsert=True)
        report.append("✅ Left the group")
    except RPCError as e:
        report.append(f"❌ Couldn't leave: <code>{esc(str(e))}</code>")

    _group_cache.pop(query.from_user.id, None)
    await query.answer()
    await _safe_edit(query.message, f"🚪 <b>{title}</b>\n\n" + "\n".join(report))


# ══════════════════════════════════════════════════════════════════════════════
# 7. /broadcast
#    draft -> pin? (yes/no) -> confirm -> live progress -> done
#    Cancel is available at every step; cancelling a running broadcast stops it
#    AND deletes the messages already delivered. After it finishes there's an
#    "undo" button that deletes it from every DM.
#    NOTE: progress / undo state lives in memory — a bot restart forgets it.
# ══════════════════════════════════════════════════════════════════════════════

_bc_drafts: dict = {}
_bc_active: dict | None = None
_bc_last: dict | None = None


def _bar(done: int, total: int, width: int = 12) -> str:
    filled = int(width * done / total) if total else width
    return "█" * filled + "░" * (width - filled)


def _bc_progress_text(bc: dict, title: str) -> str:
    done = bc["sent"] + bc["blocked"] + bc["failed"]
    total = bc["total"]
    elapsed = max(time.time() - bc["start"], 0.001)
    rate = done / elapsed
    eta = (total - done) / rate if rate else 0
    pct = int(100 * done / total) if total else 100
    return (
        f"{title}\n\n<code>{_bar(done, total)}</code> <b>{pct}%</b>\n\n"
        f"👥 Total: <code>{total}</code>\n✅ Sent: <code>{bc['sent']}</code>\n"
        f"🚫 Blocked/inactive: <code>{bc['blocked']}</code>\n⚠️ Failed: <code>{bc['failed']}</code>\n"
        f"⏳ Remaining: <code>{total - done}</code>\n\n"
        f"⚡ Speed: <code>{rate:.1f}</code>/s\n🕒 Elapsed: <code>{readable_time(elapsed)}</code>   "
        f"⏱ ETA: <code>{readable_time(eta)}</code>"
    )


def _draft_markup(tok: str, stage: str, pin: bool = False):
    if stage == "pin":
        return Markup([
            [Btn("📌 Yes, pin", callback_data=f"bc#pin1#{tok}"), Btn("🚫 No, don't pin", callback_data=f"bc#pin0#{tok}")],
            [Btn("✖️ Cancel", callback_data=f"bc#cancel#{tok}")],
        ])
    return Markup([
        [Btn("🚀 Start broadcast", callback_data=f"bc#start#{tok}")],
        [Btn("⬅️ Back", callback_data=f"bc#back#{tok}"), Btn("✖️ Cancel", callback_data=f"bc#cancel#{tok}")],
    ])


async def _active_user_count() -> int:
    return await users_col.count_documents({"active": {"$ne": False}})


@Client.on_message(filters.command("broadcast") & PRIVATE_ADMIN)
async def broadcast_cmd(_, message):
    r = message.reply_to_message
    if not r:
        await message.reply_text(
            "<b>Usage:</b> reply to the message you want to broadcast with <code>/broadcast</code>\n"
            "Text, photo, video, file, voice, sticker, album… everything is supported."
        )
        return
    if _bc_active:
        await message.reply_text(
            "⏳ A broadcast is already running.",
            reply_markup=Markup([[Btn("⛔ Cancel it", callback_data=f"bc#stop#{_bc_active['id']}")]]),
        )
        return
    total = await _active_user_count()
    if not total:
        await message.reply_text("❌ No users in the broadcast list yet. Run /syncusers to import existing users.")
        return

    tok = secrets.token_hex(4)
    _purge(_bc_drafts, 900)
    _bc_drafts[tok] = {
        "admin": message.from_user.id, "src_chat": r.chat.id, "src_msg": r.id,
        "album": bool(r.media_group_id), "pin": False, "ts": time.time(),
    }
    await message.reply_text(
        f"📢 <b>Broadcast</b> → <code>{total}</code> users\n\n📌 Pin this message in each user's DM?",
        reply_markup=_draft_markup(tok, "pin"),
    )


@Client.on_callback_query(filters.regex(r"^bc#") & ADMIN)
async def broadcast_callbacks(bot, query):
    global _bc_active
    _, action, tok = query.data.split("#")

    # ── controls on a running / finished broadcast ───────────────────────────
    if action == "stop":
        if _bc_active and _bc_active["id"] == tok:
            _bc_active["cancel"] = True
            await query.answer("Cancelling… deleting sent messages.", show_alert=False)
        else:
            await query.answer("That broadcast isn't running.", show_alert=True)
        return
    if action == "undo":
        if not _bc_last or _bc_last["id"] != tok or not _bc_last["msgs"]:
            await query.answer("Nothing left to delete (or the bot restarted).", show_alert=True)
            return
        if _bc_active:
            await query.answer("Another broadcast is running.", show_alert=True)
            return
        await query.answer()
        bc = _bc_last
        bc["status_msg"] = query.message
        asyncio.create_task(_delete_broadcast(bot, bc, final_title="🗑 <b>Broadcast deleted from all users.</b>"))
        return

    # ── draft stage buttons ───────────────────────────────────────────────────
    draft = _bc_drafts.get(tok)
    if action == "cancel":
        _bc_drafts.pop(tok, None)
        await query.message.edit_text("✖️ Broadcast cancelled.")
        return
    if not draft:
        await query.answer("Expired — send /broadcast again.", show_alert=True)
        return

    total = await _active_user_count()
    if action in ("pin1", "pin0"):
        draft["pin"] = action == "pin1"
        await query.message.edit_text(
            f"📢 <b>Ready to broadcast</b>\n\n👥 Users: <code>{total}</code>\n📌 Pin in DM: <b>{'Yes' if draft['pin'] else 'No'}</b>\n\nStart now?",
            reply_markup=_draft_markup(tok, "confirm"),
        )
    elif action == "back":
        await query.message.edit_text(
            f"📢 <b>Broadcast</b> → <code>{total}</code> users\n\n📌 Pin this message in each user's DM?",
            reply_markup=_draft_markup(tok, "pin"),
        )
    elif action == "start":
        if _bc_active:
            await query.answer("Another broadcast is already running.", show_alert=True)
            return
        _bc_drafts.pop(tok, None)
        await query.answer()
        bc = {
            "id": tok, "src_chat": draft["src_chat"], "src_msg": draft["src_msg"], "album": draft["album"],
            "pin": draft["pin"], "status_msg": query.message, "cancel": False, "msgs": [],
            "sent": 0, "blocked": 0, "failed": 0, "total": 0, "start": time.time(),
        }
        _bc_active = bc
        asyncio.create_task(_run_broadcast(bot, bc))


async def _send_one(bot, bc: dict, uid: int) -> str:
    try:
        sent = await _copy_to(bot, bc["src_chat"], bc["src_msg"], uid, bc["album"])
        mids = [m.id for m in sent]
        bc["msgs"].append((uid, mids))
        if bc["pin"] and mids:
            try:
                await bot.pin_chat_message(uid, mids[0], disable_notification=True, both_sides=True)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except RPCError:
                pass
        return "sent"
    except (UserIsBlocked, InputUserDeactivated, UserDeactivated, PeerIdInvalid):
        await users_col.update_one({"_id": uid}, {"$set": {"active": False}})
        return "blocked"
    except Exception:
        logger.debug("broadcast to %s failed", uid, exc_info=True)
        return "failed"


async def _run_broadcast(bot, bc: dict):
    global _bc_active, _bc_last
    try:
        ids = [d["_id"] async for d in users_col.find({"active": {"$ne": False}}, {"_id": 1})]
        bc["total"] = len(ids)
        stop_btn = Markup([[Btn("⛔ Cancel & delete sent", callback_data=f"bc#stop#{bc['id']}")]])
        throttle = Throttle(2.5)
        batch = 15 if bc["pin"] else 20
        min_gap = 1.6 if bc["pin"] else 1.0     # stay well under Telegram's ~30 msg/s

        await _safe_edit(bc["status_msg"], _bc_progress_text(bc, "📢 <b>Broadcasting…</b>"), stop_btn)
        for i in range(0, len(ids), batch):
            if bc["cancel"]:
                break
            t0 = time.time()
            results = await asyncio.gather(*(_send_one(bot, bc, uid) for uid in ids[i:i + batch]))
            for r in results:
                bc[{"sent": "sent", "blocked": "blocked"}.get(r, "failed")] += 1
            if throttle.ready():
                await _safe_edit(bc["status_msg"], _bc_progress_text(bc, "📢 <b>Broadcasting…</b>"), stop_btn)
            await asyncio.sleep(max(0, min_gap - (time.time() - t0)))

        if bc["cancel"]:
            await _delete_broadcast(bot, bc, final_title="⛔ <b>Broadcast cancelled</b> — delivered messages were deleted.")
            return

        _bc_last = bc
        undo = Markup([[Btn("🗑 Delete from all users", callback_data=f"bc#undo#{bc['id']}")]])
        await _safe_edit(bc["status_msg"], _bc_progress_text(bc, "✅ <b>Broadcast complete</b>") +
                         f"\n\n📌 Pinned: <b>{'Yes' if bc['pin'] else 'No'}</b>", undo)
    except Exception:
        logger.exception("broadcast crashed")
        await _safe_edit(bc["status_msg"], "❌ Broadcast stopped because of an internal error — see the logs.")
    finally:
        _bc_active = None


async def _delete_broadcast(bot, bc: dict, final_title: str):
    """Delete every delivered copy (used by Cancel and by the post-run Undo)."""
    global _bc_active, _bc_last
    _bc_active = bc
    try:
        msgs = list(bc["msgs"])
        total, done, deleted = len(msgs), 0, 0
        throttle = Throttle(2.5)
        start = time.time()
        for i in range(0, total, 20):
            t0 = time.time()
            for uid, mids in msgs[i:i + 20]:
                try:
                    await bot.delete_messages(uid, mids)
                    deleted += 1
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
                except RPCError:
                    pass
                done += 1
            if throttle.ready():
                pct = int(100 * done / total) if total else 100
                await _safe_edit(
                    bc["status_msg"],
                    f"🗑 <b>Deleting sent messages…</b>\n\n<code>{_bar(done, total)}</code> <b>{pct}%</b>\n"
                    f"Deleted: <code>{deleted}</code> / <code>{total}</code>\n🕒 <code>{readable_time(time.time() - start)}</code>",
                )
            await asyncio.sleep(max(0, 1.0 - (time.time() - t0)))
        bc["msgs"] = []
        _bc_last = None
        await _safe_edit(
            bc["status_msg"],
            f"{final_title}\n\n✅ Sent before stop: <code>{bc['sent']}</code>\n🗑 Deleted: <code>{deleted}</code>",
        )
    finally:
        _bc_active = None


# ══════════════════════════════════════════════════════════════════════════════
# 8. /stats — full dashboard (replaces the old one-line /stats in start.py)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import psutil
except ImportError:           # bot still runs; server section just shows less
    psutil = None

_PROC_START = time.time()
# Atlas free (M0) clusters hold 512 MB. Change with MONGO_STORAGE_LIMIT_MB if yours differs.
_DB_LIMIT_MB = float(os.environ.get("MONGO_STORAGE_LIMIT_MB", "512"))


def _mb(n: float) -> str:
    n /= 1024 * 1024
    return f"{n / 1024:.2f} GB" if n >= 1024 else f"{n:.1f} MB"


def _status_dot(pct: float) -> str:
    return "🟢" if pct < 70 else ("🟡" if pct < 90 else "🔴")


async def _channel_lines(bot) -> tuple[list, int]:
    settings = await get_settings()
    chans = list(settings["index_channels"])

    async def one(cid):
        try:
            title = (await bot.get_chat(cid)).title
        except Exception:
            title = None
        return cid, title, await count_by_channel(cid)

    rows = await asyncio.gather(*(one(c) for c in chans))
    lines = []
    for i, (cid, title, n) in enumerate(rows[:12], 1):
        name = esc(title) if title else "<i>unreachable</i>"
        lines.append(f"  {i}. {name}\n      <code>{cid}</code> — <b>{n}</b> files")
    if len(rows) > 12:
        lines.append(f"  … and {len(rows) - 12} more channels")
    return lines, sum(r[2] for r in rows)


async def _db_lines() -> list:
    try:
        st = await db.command("dbStats", scale=1)
    except Exception:
        return ["  ⚠️ Couldn't read storage stats"]
    used = st.get("storageSize", 0) + st.get("indexSize", 0)
    limit = _DB_LIMIT_MB * 1024 * 1024
    pct = used / limit * 100 if limit else 0
    return [
        f"  {_status_dot(pct)} <code>{_bar(int(pct), 100, 12)}</code> <b>{pct:.1f}%</b>",
        f"  💽 Used: <b>{_mb(used)}</b> of <b>{_mb(limit)}</b>",
        f"  🆓 Free: <b>{_mb(max(limit - used, 0))}</b>",
        f"  📄 Data: {_mb(st.get('dataSize', 0))} · Indexes: {_mb(st.get('indexSize', 0))}",
        f"  📚 Collections: <code>{st.get('collections', 0)}</code> · Objects: <code>{st.get('objects', 0)}</code>",
    ]


async def _server_lines() -> list:
    if not psutil:
        return [f"  ⏱ Uptime: <b>{readable_time(time.time() - _PROC_START)}</b>",
                "  ℹ️ Install <code>psutil</code> for RAM / CPU stats"]
    proc = psutil.Process(os.getpid())
    proc.cpu_percent(None)
    psutil.cpu_percent(None)
    await asyncio.sleep(0.5)                       # sample window for CPU %
    cpu_bot, cpu_sys = proc.cpu_percent(None), psutil.cpu_percent(None)
    ram_bot, vm = proc.memory_info().rss, psutil.virtual_memory()
    uptime = readable_time(time.time() - proc.create_time())
    return [
        f"  ⏱ Uptime: <b>{uptime}</b>",
        f"  🧠 RAM (bot): <b>{_mb(ram_bot)}</b>",
        f"  🧠 RAM (server): <b>{_mb(vm.used)}</b> / {_mb(vm.total)} (<b>{vm.percent:.0f}%</b>)",
        f"  🔥 CPU (bot): <b>{cpu_bot:.1f}%</b> · CPU (server): <b>{cpu_sys:.1f}%</b> · Cores: <code>{psutil.cpu_count()}</code>",
    ]


async def build_stats(bot) -> str:
    now = _now()
    (chan_lines, in_channels), db_lines, server_lines = await asyncio.gather(
        _channel_lines(bot), _db_lines(), _server_lines()
    )
    total_files_n = await files.estimated_document_count()
    users = await users_col.count_documents({})
    inactive = await users_col.count_documents({"active": False})
    groups = await groups_col.count_documents({"left": {"$ne": True}})
    left_groups = await groups_col.count_documents({"left": True})
    premium = await premium_col.count_documents({"expiry_time": {"$gt": now}})
    banned = await banned_col.count_documents({})
    settings = await get_settings()

    other = max(total_files_n - in_channels, 0)
    pm = "✅" if ENABLE_PM_SEARCH else "❌"
    af_on = settings.get("autofilter_enabled", True)
    af_line = ("🟢 ON" if af_on else "🔴 OFF <i>(maintenance mode)</i>") + f"  <i>(Groups ✅ · PM {pm})</i>"
    lines = [
        "📊 <b>Bot Statistics</b>\n",
        f"🔎 <b>Autofilter:</b> {af_line}",
        f"🔤 <b>Filter words:</b> <code>{len(settings.get('filter_words') or [])}</code>",
        f"📥 <b>Auto-indexing:</b> <code>{len(settings['index_channels'])}</code> channel(s)\n",
        "📚 <b>Index channels</b>",
        *(chan_lines or ["  <i>No index channels set</i>"]),
    ]
    if other:
        lines.append(f"  ➕ Other / older files: <b>{other}</b>")
    lines += [
        f"\n📁 <b>Total indexed files:</b> <code>{total_files_n}</code>\n",
        f"👥 <b>Total users:</b> <code>{users}</code>  <i>(inactive: {inactive})</i>",
        f"🏘 <b>Total groups:</b> <code>{groups}</code>",
        f"💎 <b>Premium users:</b> <code>{premium}</code>",
        f"🚫 <b>Banned users:</b> <code>{banned}</code>",
        f"🚪 <b>Left / removed groups:</b> <code>{left_groups}</code>\n",
        "🗄 <b>MongoDB storage</b>",
        *db_lines,
        "\n🤖 <b>Bot details</b>",
        *server_lines,
    ]
    return "\n".join(lines)


def _stats_markup():
    return Markup([[Btn("🔄 Refresh", callback_data="st#refresh"), Btn("✖️ Close", callback_data="st#close")]])


@Client.on_message(filters.command("stats") & ADMIN)
async def stats_cmd(bot, message):
    wait = await message.reply_text("📊 Gathering stats…")
    await _safe_edit(wait, await build_stats(bot), _stats_markup())


@Client.on_callback_query(filters.regex(r"^st#") & ADMIN)
async def stats_callbacks(bot, query):
    if query.data.endswith("close"):
        await query.message.delete()
        return
    await query.answer("Refreshing…")
    await _safe_edit(query.message, await build_stats(bot), _stats_markup())



# ══════════════════════════════════════════════════════════════════════════════
# 9. LINK GUARD — non-admins can't post links (group or PM). The link message is
#    deleted, a warning is shown, and the text never reaches the search handler.
#    Bot admins and group admins are exempt. Runs before search (group -1).
#    Needs the bot to have "Delete messages" permission in the group.
# ══════════════════════════════════════════════════════════════════════════════

LINK_WARN_TXT = "🚫 <b>Sending links is not allowed!</b>"          # PM
LINK_WARN_GROUP_TXT = "🚫 {who}, <b>sending links is not allowed!</b>"   # group
_LINK_RE = re.compile(r"(?i)(?:https?://|ftp://|tg://|www\.|(?:t|telegram)\.(?:me|dog)/)")
_LINK_ENTITIES = (enums.MessageEntityType.URL, enums.MessageEntityType.TEXT_LINK)
_gadmin_cache: dict = {}
_link_warned: dict = {}


def has_link(message) -> bool:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if any(e.type in _LINK_ENTITIES for e in entities):
        return True
    return bool(_LINK_RE.search(text))


def _is_group_chat(chat) -> bool:
    return chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)


async def _is_group_admin(bot, chat_id: int, uid: int) -> bool:
    hit = _gadmin_cache.get((chat_id, uid))
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        member = await bot.get_chat_member(chat_id, uid)
        ok = member.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    except RPCError:
        ok = False
    if len(_gadmin_cache) > 3000:
        _gadmin_cache.clear()
    _gadmin_cache[(chat_id, uid)] = (time.time(), ok)
    return ok


async def _link_violation(_, bot, message):
    u = message.from_user
    if not u or u.is_bot or u.id in ADMINS:
        return False
    if not has_link(message):
        return False
    if _is_group_chat(message.chat) and await _is_group_admin(bot, message.chat.id, u.id):
        return False
    return True


link_violation_filter = filters.create(_link_violation)


async def _delete_later(msg, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except RPCError:
        pass


async def _handle_link(bot, message):
    try:
        await message.delete()
    except RPCError:
        logger.info("Couldn't delete a link in %s — does the bot have 'Delete messages' permission?", message.chat.id)
    u = message.from_user
    in_group = _is_group_chat(message.chat)
    key = (message.chat.id, u.id)
    if in_group and time.time() - _link_warned.get(key, 0) < 10:
        return                                   # one warning per 10s per user, no spam
    _link_warned[key] = time.time()
    if len(_link_warned) > 3000:
        _link_warned.clear()
    text = LINK_WARN_GROUP_TXT.format(who=user_link(u.id, _name_of(u))) if in_group else LINK_WARN_TXT
    try:
        warn = await bot.send_message(message.chat.id, text, disable_web_page_preview=True)
        if in_group:
            asyncio.create_task(_delete_later(warn, 15))
    except RPCError:
        pass


@Client.on_message(link_violation_filter & (filters.private | filters.group), group=-1)
async def link_guard(bot, message):
    await _handle_link(bot, message)
    message.stop_propagation()


@Client.on_edited_message(link_violation_filter & (filters.private | filters.group), group=-1)
async def link_guard_edited(bot, message):
    await _handle_link(bot, message)       # someone edited a link into an old message
    message.stop_propagation()


# ══════════════════════════════════════════════════════════════════════════════
# 10. AUTOFILTER ON/OFF + FILTER WORDS — both are applied by prepare_query(),
#     which plugins/search.py calls at the top of its search handler.
#     (Done there, not as an earlier handler, so admin replies that other
#     plugins wait for with bot.listen() are never swallowed by maintenance.)
# ══════════════════════════════════════════════════════════════════════════════

MAINTENANCE_TXT = "🛠 <b>Bot is under maintenance.</b>\n\nPlease try again later."
_maint_notified: dict = {}


async def _maintenance_notice(message):
    u = message.from_user
    key = (message.chat.id, u.id if u else 0)
    if time.time() - _maint_notified.get(key, 0) < 20:
        return                                   # already told them a moment ago
    _maint_notified[key] = time.time()
    if len(_maint_notified) > 3000:
        _maint_notified.clear()
    try:
        notice = await message.reply_text(MAINTENANCE_TXT, quote=True)
        if _is_group_chat(message.chat):
            asyncio.create_task(_delete_later(notice, 15))
    except RPCError:
        pass


@functools.lru_cache(maxsize=4)
def _fw_pattern(words: tuple):
    parts = [r"\s+".join(re.escape(p) for p in w.split()) for w in sorted(words, key=len, reverse=True)]
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def strip_filter_words(text: str, words) -> str:
    pat = _fw_pattern(tuple(words))
    if not pat:
        return text
    out = pat.sub(" ", text)
    return re.sub(r"\s{2,}", " ", out).strip(" ,;:|/-")


async def prepare_query(message, query: str) -> str:
    """Called by the search handler. Returns the query to search, or "" to stop
    (maintenance mode on, or the message contained nothing but filter words)."""
    settings = await get_settings()
    if not settings.get("autofilter_enabled", True):
        await _maintenance_notice(message)
        return ""
    words = settings.get("filter_words") or []
    if not words:
        return query
    cleaned = strip_filter_words(query, words)
    if cleaned:
        return cleaned
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.reply_text("🔎 Please type the name of the title you're looking for.", quote=True)
    return ""


def _parse_words(message) -> list:
    parts = message.text.split(None, 1)
    raw = parts[1] if len(parts) > 1 else ""
    words = []
    for chunk in raw.replace("\n", ",").split(","):
        w = re.sub(r"\s+", " ", chunk).strip().lower()
        if w and w not in words:
            words.append(w)
    return words


def _code_list(words) -> str:
    return " • ".join(f"<code>{esc(w)}</code>" for w in words)


@Client.on_message(filters.command("set_filterword") & ADMIN)
async def set_filterword_cmd(_, message):
    new = _parse_words(message)
    if not new:
        await message.reply_text(
            "<b>Usage:</b> <code>/set_filterword word, another word, a phrase</code>\n\n"
            "Separate words or phrases with commas. They'll be ignored in every search — "
            "e.g. with <code>movies</code> set, <i>punjabi movies</i> searches just <i>punjabi</i>."
        )
        return
    too_short = [w for w in new if len(w) < 2]
    too_long = [w for w in new if len(w) > 60]
    new = [w for w in new if 2 <= len(w) <= 60][:50]
    settings = await get_settings()
    current = list(settings.get("filter_words") or [])
    added = [w for w in new if w not in current]
    existed = [w for w in new if w in current]
    if added:
        await update_settings({"filter_words": current + added})
    lines = []
    if added:
        lines.append(f"✅ <b>Added {len(added)}:</b>\n{_code_list(added)}")
    if existed:
        lines.append(f"⏭ <b>Already set:</b>\n{_code_list(existed)}")
    if too_short or too_long:
        lines.append("⚠️ <b>Skipped</b> (must be 2–60 characters):\n" + _code_list(too_short + too_long))
    lines.append(f"\n🔤 Total filter words: <code>{len(current) + len(added)}</code>")
    await message.reply_text("\n\n".join(lines))


@Client.on_message(filters.command("remove_filterword") & ADMIN)
async def remove_filterword_cmd(_, message):
    targets = _parse_words(message)
    settings = await get_settings()
    current = list(settings.get("filter_words") or [])
    if not targets:
        text = "<b>Usage:</b> <code>/remove_filterword word, another word, a phrase</code>"
        if current:
            text += "\n\n<b>Current filter words:</b>\n" + _code_list(current[:100])
        await message.reply_text(text)
        return
    removed = [w for w in targets if w in current]
    missing = [w for w in targets if w not in current]
    if removed:
        await update_settings({"filter_words": [w for w in current if w not in removed]})
    lines = []
    if removed:
        lines.append(f"🗑 <b>Removed {len(removed)}:</b>\n{_code_list(removed)}")
    if missing:
        lines.append(f"❓ <b>Not found:</b>\n{_code_list(missing)}")
    lines.append(f"\n🔤 Total filter words: <code>{len(current) - len(removed)}</code>")
    await message.reply_text("\n\n".join(lines))


@Client.on_message(filters.command("filterwords"))
async def filterwords_cmd(_, message):
    settings = await get_settings()
    words = list(settings.get("filter_words") or [])
    is_admin = bool(message.from_user and message.from_user.id in ADMINS)
    if not words:
        text = "ℹ️ <b>No filter words are set.</b>"
        if is_admin:
            text += "\n\nAdd some with <code>/set_filterword word, phrase</code>"
        await message.reply_text(text)
        return
    shown, size = [], 0
    for w in words:
        size += len(w) + 22
        if size > 3300:
            break
        shown.append(w)
    text = (
        f"🔤 <b>Filter words</b> — <code>{len(words)}</code>\n\n"
        f"These words are ignored when you search:\n\n{_code_list(shown)}"
    )
    if len(shown) < len(words):
        text += f"\n\n… and {len(words) - len(shown)} more"
    if is_admin:
        text += "\n\n<i>/set_filterword · /remove_filterword</i>"
    await message.reply_text(text, quote=True)


# ══════════════════════════════════════════════════════════════════════════════
# 11. TRENDING — most searched titles of the last 7 days.
#     Only searches that returned results are counted (direct hits and fuzzy
#     corrections — not "did you mean" suggestions), and they're stored under the
#     correctly spelled title taken from the matched file, so "avtar" and
#     "avatar" both count toward "Avatar". One user counts once per title per 10 min.
#     Storage is tiny: one row per title per day, auto-deleted after 14 days.
# ══════════════════════════════════════════════════════════════════════════════

trend_col = db["trending_stats"]
_TREND_DAYS = 7
_TREND_PAGE = 10
_TREND_MAX = 100
_TREND_DOTS = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]   # button "colour" (Telegram's real coloured buttons aren't supported by pyrofork)
_trend_seen: dict = {}
_trend_cache: dict = {"ts": 0.0, "items": []}
_trend_index_ready = False


def _canonical_title(results: list) -> str | None:
    title = clean_title(display_name(results[0]))
    if len(title) < 2:
        return None
    if title.isupper() or title.islower():
        title = title.title()
    return title


def _hash(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()[:12]


async def record_trending(user_id: int, results: list):
    """Called by plugins/search.py after a search that found files."""
    try:
        global _trend_index_ready
        if not results:
            return
        title = _canonical_title(results)
        if not title:
            return
        key = title.lower()
        now = time.time()
        if now - _trend_seen.get((user_id, key), 0) < 600:
            return
        _trend_seen[(user_id, key)] = now
        if len(_trend_seen) > 5000:
            for k in [k for k, t in _trend_seen.items() if now - t > 600]:
                _trend_seen.pop(k, None)
        if not _trend_index_ready:
            await trend_col.create_index("ts", expireAfterSeconds=14 * 86400)
            _trend_index_ready = True
        day = _now().astimezone(IST).strftime("%Y%m%d")
        h = _hash(key)
        await trend_col.update_one(
            {"_id": f"{day}|{h}"},
            {"$inc": {"count": 1}, "$set": {"title": title, "key": key, "h": h, "day": day, "ts": _now()}},
            upsert=True,
        )
    except Exception:
        logger.debug("record_trending failed", exc_info=True)


async def top_trending(force: bool = False) -> list:
    if not force and time.time() - _trend_cache["ts"] < 60:
        return _trend_cache["items"]
    cutoff = (_now().astimezone(IST) - timedelta(days=_TREND_DAYS - 1)).strftime("%Y%m%d")
    pipeline = [
        {"$match": {"day": {"$gte": cutoff}}},
        {"$sort": {"day": 1}},
        {"$group": {"_id": "$h", "title": {"$last": "$title"}, "count": {"$sum": "$count"}, "last": {"$max": "$ts"}}},
        {"$sort": {"count": -1, "last": -1}},
        {"$limit": _TREND_MAX},
    ]
    items = [{"h": d["_id"], "title": d["title"], "count": d["count"]} async for d in trend_col.aggregate(pipeline)]
    _trend_cache.update(ts=time.time(), items=items)
    return items


def _trending_view(items: list, page: int):
    pages = max(1, (len(items) + _TREND_PAGE - 1) // _TREND_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * _TREND_PAGE:(page + 1) * _TREND_PAGE]
    rows = []
    for i, it in enumerate(chunk):
        dot = _TREND_DOTS[(page * _TREND_PAGE + i) % len(_TREND_DOTS)]
        label = f"{dot} {it['title']}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([Btn(label, callback_data=f"tq#{it['h']}")])
    nav = []
    if page > 0:
        nav.append(Btn("⬅️ Back", callback_data=f"tp#{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(Btn("Next ➡️", callback_data=f"tp#{page + 1}"))
    rows.append(nav)
    text = (
        f"🔥 <b>Trending Searches</b>\n<i>Most searched in the last {_TREND_DAYS} days</i>\n\n"
        f"👇 Tap a title to get its files."
    )
    return text, Markup(rows)


@Client.on_message(filters.command("trending"))
async def trending_cmd(_, message):
    settings = await get_settings()
    if not settings.get("autofilter_enabled", True):
        await _maintenance_notice(message)
        return
    items = await top_trending(force=True)
    if not items:
        await message.reply_text("📭 <b>Nothing is trending yet.</b>\n\nSearch for a title and it will show up here!", quote=True)
        return
    text, markup = _trending_view(items, 0)
    sent = await message.reply_text(text, reply_markup=markup, quote=True)
    if settings.get("query_autodelete_enabled"):
        asyncio.create_task(_delete_later(sent, settings["query_autodelete_seconds"]))


@Client.on_callback_query(filters.regex(r"^tp#"))
async def trending_page(_, query):
    items = await top_trending()
    if not items:
        await query.answer("Nothing is trending right now.", show_alert=True)
        return
    text, markup = _trending_view(items, int(query.data.split("#")[1]))
    await query.answer()
    await _safe_edit(query.message, text, markup)


@Client.on_callback_query(filters.regex(r"^tq#"))
async def trending_click(_, query):
    if not (await get_settings()).get("autofilter_enabled", True):
        await query.answer("🛠 Bot is under maintenance. Please try again later.", show_alert=True)
        return
    h = query.data.split("#", 1)[1]
    title = next((i["title"] for i in _trend_cache["items"] if i["h"] == h), None)
    if not title:
        doc = await trend_col.find_one({"h": h}, sort=[("day", -1)])
        title = doc["title"] if doc else None
    if not title:
        await query.answer("This list expired — send /trending again.", show_alert=True)
        return
    await query.answer("🔎 Searching…")

    from plugins.search import _deliver_results      # lazy: plugins load in alphabetical order
    results = await search_files(title)
    if not results:
        await _safe_edit(
            query.message,
            f"❌ <b>{esc(title)}</b> isn't available any more.",
            Markup([[Btn("⬅️ Back to trending", callback_data="tp#0")]]),
        )
        return
    asyncio.create_task(record_trending(query.from_user.id, results))
    await _deliver_results(query.message, title, results)
    try:
        await query.message.delete()
    except RPCError:
        pass



# ══════════════════════════════════════════════════════════════════════════════
# HELPERS: /syncusers  /extra   (+ nudge when a PM-only command is used in a group)
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("syncusers") & PRIVATE_ADMIN)
async def syncusers_cmd(_, message):
    """Import user ids the bot already stored (limits, verification, premium) so
    /broadcast can reach people who used the bot before this plugin existed."""
    wait = await message.reply_text("🔄 Importing existing users…")
    found = set()
    for name in ("file_limit_usage", "verify_status", "premium_users"):
        async for d in db[name].find({}, {"_id": 1}):
            if isinstance(d["_id"], int) and d["_id"] > 0:
                found.add(d["_id"])
    ops = [UpdateOne({"_id": uid}, {"$setOnInsert": {"name": None, "username": None, "active": True, "joined_at": _now()}}, upsert=True)
           for uid in found]
    added = 0
    for i in range(0, len(ops), 1000):
        res = await users_col.bulk_write(ops[i:i + 1000], ordered=False)
        added += res.upserted_count
    total = await users_col.count_documents({})
    await wait.edit_text(f"✅ Found <code>{len(found)}</code> known users, <code>{added}</code> new.\n👥 Broadcast list: <code>{total}</code> users.")


@Client.on_message(filters.command("extra") & ADMIN)
async def extra_help(_, message):
    await message.reply_text(
        "<b>🧰 Extra admin commands</b>\n\n"
        "• /stats — full dashboard\n\n"
        "<b>💬 Messaging</b>\n"
        "• /send <code>id [id…]</code> — reply to a message to send it to users\n"
        "• /broadcast — reply to a message to send to everyone <i>(PM)</i>\n"
        "• /syncusers — import old users into the broadcast list <i>(PM)</i>\n\n"
        "<b>👤 Users</b>\n"
        "• /id — reply (or <code>/id user_id</code>) for user info\n"
        "• /ban <code>user_id</code> · /unban <code>user_id</code> · /showban\n"
        "• /checklimit · /resetlimit <code>user_id</code> · /resetlimitall <i>(PM)</i>\n\n"
        "<b>🗄 Files</b>\n"
        "• /delete <code>file_link</code> — delete one file (DB or DB + channel)\n"
        "• /deleteall — wipe all indexed files from MongoDB <i>(PM)</i>\n\n"
        "<b>🔤 Search control</b>\n"
        "• /set_filterword <code>a, b, phrase</code> · /remove_filterword <code>a, b</code>\n"
        "• /filterwords · /trending <i>(everyone)</i>\n"
        "• Autofilter ON/OFF — top button in /settings\n\n"
        "<b>👥 Groups</b>\n"
        "• /show_groups <i>(PM)</i> · /leave_groups <code>group_id</code>"
    )


_PM_ONLY = ["deleteall", "resetlimitall", "show_groups", "broadcast", "syncusers"]


@Client.on_message(filters.command(_PM_ONLY) & ~filters.private & ADMIN)
async def pm_only_notice(_, message):
    await message.reply_text("🔒 Use this command in my private chat — it shows sensitive data / affects everyone.")
