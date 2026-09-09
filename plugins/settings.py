import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from database.settings_db import (
    get_settings, update_settings, set_shortener, set_tutorial,
    add_fsub_channel, remove_fsub_channel,
)
from database.premium_db import list_premium, count_premium
from plugins.force_sub import can_manage_channel
from shortlink import make_short_link
from utils import mask_secret, IST
from strings import (
    SETTINGS_MAIN_TXT,
    FSUB_MENU_HEADER, FSUB_MENU_EMPTY, FSUB_ADD_PROMPT, FSUB_ADD_NOT_CHANNEL,
    FSUB_ADD_NOT_ADMIN, FSUB_ADD_OK, FSUB_ADD_FAILED, FSUB_REMOVED_TXT,
    PREMIUM_MENU_EMPTY, PREMIUM_MENU_ROW,
    VERIFY_MENU_HEADER, VERIFY_TIER_ROW,
    SET_SHORTENER_USAGE, SET_SHORTENER_OK, SET_SHORTENER_WARN,
    SET_VERIFY_TIME_USAGE, SET_VERIFY_TIME_OK,
    SET_TUTORIAL_USAGE, SET_TUTORIAL_OK,
)

logger = logging.getLogger(__name__)

_PREMIUM_PAGE_SIZE = 10


# ══════════════════════════════════════════════════════════════════════════════
# Menu builders — pure rendering, no side effects
# ══════════════════════════════════════════════════════════════════════════════

def _status(flag: bool) -> str:
    return "✅ ON" if flag else "❌ OFF"


def build_main_menu(settings: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_status(settings["force_sub_enabled"]), callback_data="cfg#tg#fsub"),
         InlineKeyboardButton("📢 Force-Subscribe", callback_data="cfg#m#fsub")],
        [InlineKeyboardButton(_status(settings["premium_enabled"]), callback_data="cfg#tg#premium"),
         InlineKeyboardButton("💎 Premium", callback_data="cfg#m#premium")],
        [InlineKeyboardButton(_status(settings["verify_enabled"]), callback_data="cfg#tg#verify"),
         InlineKeyboardButton("🔗 Verification", callback_data="cfg#m#verify")],
        [InlineKeyboardButton(
            "🔘 Button" if settings["result_mode"] == "button" else "📝 Text",
            callback_data="cfg#tg#resmode",
         ),
         InlineKeyboardButton("📄 Result Format", callback_data="cfg#info#resmode")],
        [InlineKeyboardButton("❌ Close", callback_data="cfg#close")],
    ])


async def build_fsub_menu(bot, settings: dict):
    channels = settings["fsub_channels"]
    rows = []
    if channels:
        async def _title(cid):
            try:
                return (await bot.get_chat(cid)).title
            except RPCError:
                return str(cid)
        titles = await asyncio.gather(*[_title(c) for c in channels])
        for cid, title in zip(channels, titles):
            rows.append([InlineKeyboardButton(f"🗑 {title}", callback_data=f"cfg#fsub_rm#{cid}")])
    rows.append([InlineKeyboardButton("➕ Add New Channel", callback_data="cfg#fsub_add")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="cfg#main")])
    text = FSUB_MENU_HEADER if channels else FSUB_MENU_EMPTY
    return text, InlineKeyboardMarkup(rows)


async def build_premium_menu(offset: int = 0):
    docs = await list_premium(skip=offset, limit=_PREMIUM_PAGE_SIZE)
    total = await count_premium()

    if not docs:
        text = PREMIUM_MENU_EMPTY
    else:
        rows_txt = "".join(
            PREMIUM_MENU_ROW.format(
                user_id=d["_id"],
                expiry=d["expiry_time"].astimezone(IST).strftime("%d %b %Y, %I:%M %p"),
            )
            for d in docs
        )
        text = f"💎 <b>Premium Users</b> ({total})\n\n{rows_txt}"

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cfg#prem_pg#{max(0, offset - _PREMIUM_PAGE_SIZE)}"))
    if offset + _PREMIUM_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"cfg#prem_pg#{offset + _PREMIUM_PAGE_SIZE}"))

    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="cfg#main")])
    return text, InlineKeyboardMarkup(rows)


def build_verify_menu(settings: dict):
    body = "\n".join(
        VERIFY_TIER_ROW.format(
            tier=tier,
            domain=settings["shorteners"].get(str(tier), {}).get("domain") or "—",
            api=mask_secret(settings["shorteners"].get(str(tier), {}).get("api", "")),
            tutorial=settings["tutorials"].get(str(tier)) or "—",
        )
        for tier in (1, 2, 3)
    )
    body += f"\n⏱ Gap 1→2: <code>{settings['verify_time']}s</code>\n⏱ Gap 2→3: <code>{settings['third_verify_time']}s</code>"
    text = VERIFY_MENU_HEADER.format(body=body)
    rows = [[InlineKeyboardButton("⬅️ Back", callback_data="cfg#main")]]
    return text, InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
# /settings entrypoint
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("settings") & filters.private & filters.user(ADMINS))
async def settings_cmd(_, message):
    settings = await get_settings()
    await message.reply_text(SETTINGS_MAIN_TXT, reply_markup=build_main_menu(settings))


@Client.on_callback_query(filters.regex(r"^cfg#") & filters.user(ADMINS))
async def settings_callback(bot, query):
    parts = query.data.split("#")
    action = parts[1]

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except RPCError:
            pass
        return

    if action == "main":
        settings = await get_settings()
        await query.answer()
        await query.message.edit_text(SETTINGS_MAIN_TXT, reply_markup=build_main_menu(settings))
        return

    if action == "tg":
        field = {
            "fsub": "force_sub_enabled",
            "premium": "premium_enabled",
            "verify": "verify_enabled",
        }.get(parts[2])
        if field:
            settings = await get_settings()
            settings = await update_settings({field: not settings[field]})
        else:  # result mode toggle
            settings = await get_settings()
            new_mode = "text" if settings["result_mode"] == "button" else "button"
            settings = await update_settings({"result_mode": new_mode})
        await query.answer()
        await query.message.edit_reply_markup(build_main_menu(settings))
        return

    if action == "info":
        await query.answer(
            "Choose whether search results show as tappable buttons or a plain text list.",
            show_alert=True,
        )
        return

    if action == "m":
        settings = await get_settings()
        await query.answer()
        if parts[2] == "fsub":
            text, markup = await build_fsub_menu(bot, settings)
        elif parts[2] == "premium":
            text, markup = await build_premium_menu()
        else:
            text, markup = build_verify_menu(settings)
        await query.message.edit_text(text, reply_markup=markup)
        return

    if action == "prem_pg":
        offset = int(parts[2])
        text, markup = await build_premium_menu(offset)
        await query.answer()
        await query.message.edit_text(text, reply_markup=markup)
        return

    if action == "fsub_rm":
        channel_id = int(parts[2])
        settings = await remove_fsub_channel(channel_id)
        await query.answer(FSUB_REMOVED_TXT)
        text, markup = await build_fsub_menu(bot, settings)
        await query.message.edit_text(text, reply_markup=markup)
        return

    if action == "fsub_add":
        await query.answer()
        await _run_fsub_add(bot, query)
        return


async def _run_fsub_add(bot, query):
    prompt = await query.message.edit_text(FSUB_ADD_PROMPT)
    try:
        reply = await bot.listen(chat_id=query.message.chat.id, user_id=query.from_user.id, timeout=90)
    except asyncio.TimeoutError:
        settings = await get_settings()
        text, markup = await build_fsub_menu(bot, settings)
        await prompt.edit_text(text, reply_markup=markup)
        return

    chat = None
    if reply.forward_from_chat and reply.forward_from_chat.type == enums.ChatType.CHANNEL:
        chat = reply.forward_from_chat
    elif reply.text:
        text = reply.text.strip()
        try:
            target = int(text) if text.lstrip("-").isdigit() else text
            chat = await bot.get_chat(target)
        except RPCError as exc:
            settings = await get_settings()
            menu_text, markup = await build_fsub_menu(bot, settings)
            await prompt.edit_text(FSUB_ADD_FAILED.format(error=exc) + "\n\n" + menu_text, reply_markup=markup)
            return

    settings = await get_settings()
    if not chat or chat.type != enums.ChatType.CHANNEL:
        text, markup = await build_fsub_menu(bot, settings)
        await prompt.edit_text(FSUB_ADD_NOT_CHANNEL + "\n\n" + text, reply_markup=markup)
        return

    if not await can_manage_channel(bot, chat.id):
        text, markup = await build_fsub_menu(bot, settings)
        await prompt.edit_text(FSUB_ADD_NOT_ADMIN + "\n\n" + text, reply_markup=markup)
        return

    settings = await add_fsub_channel(chat.id)
    text, markup = await build_fsub_menu(bot, settings)
    await prompt.edit_text(FSUB_ADD_OK.format(title=chat.title) + "\n\n" + text, reply_markup=markup)


# ══════════════════════════════════════════════════════════════════════════════
# Verification config commands (referenced from the Verification submenu)
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("set_shortener") & filters.private & filters.user(ADMINS))
async def set_shortener_cmd(_, message):
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4 or parts[1] not in ("1", "2", "3"):
        await message.reply_text(SET_SHORTENER_USAGE)
        return
    tier, domain, api = int(parts[1]), parts[2], parts[3]
    await set_shortener(tier, domain, api)

    demo = await make_short_link("https://t.me", domain, api)
    if demo == "https://t.me":
        await message.reply_text(SET_SHORTENER_WARN)
    else:
        await message.reply_text(SET_SHORTENER_OK.format(tier=tier, demo=demo), disable_web_page_preview=True)


@Client.on_message(filters.command("set_verify_time") & filters.private & filters.user(ADMINS))
async def set_verify_time_cmd(_, message):
    parts = message.text.split()
    if len(parts) != 3 or parts[1] not in ("1", "2") or not parts[2].isdigit():
        await message.reply_text(SET_VERIFY_TIME_USAGE)
        return
    gap, seconds = parts[1], int(parts[2])
    field = "verify_time" if gap == "1" else "third_verify_time"
    await update_settings({field: seconds})
    await message.reply_text(SET_VERIFY_TIME_OK.format(gap=gap, seconds=seconds))


@Client.on_message(filters.command("set_tutorial") & filters.private & filters.user(ADMINS))
async def set_tutorial_cmd(_, message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1] not in ("1", "2", "3"):
        await message.reply_text(SET_TUTORIAL_USAGE)
        return
    tier = int(parts[1])
    await set_tutorial(tier, parts[2].strip())
    await message.reply_text(SET_TUTORIAL_OK.format(tier=tier))
