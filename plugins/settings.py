import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from database.settings_db import (
    get_settings, update_settings, set_shortener, set_tutorial,
    add_fsub_channel, remove_fsub_channel,
    add_index_channel, remove_index_channel,
)
from database.premium_db import list_premium, count_premium
from database.filters_db import count_by_channel
from plugins.force_sub import is_bot_admin_in
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
    INDEX_MENU_HEADER, INDEX_MENU_EMPTY, INDEX_MENU_ROW,
    INDEX_ADD_PROMPT, INDEX_ADD_NOT_CHANNEL, INDEX_ADD_NOT_ADMIN,
    INDEX_ADD_OK, INDEX_ADD_FAILED, INDEX_REMOVED_TXT,
    ASK_NUMBER_TIMEOUT, ASK_NUMBER_INVALID,
    ASK_QUERY_DELAY_PROMPT, QUERY_DELAY_SET_TXT,
    ASK_FILE_DELAY_PROMPT, FILE_DELAY_SET_TXT,
    ASK_FILE_LIMIT_PROMPT, FILE_LIMIT_SET_TXT, FILE_LIMIT_NEEDS_VERIFY_NOTE,
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
        [InlineKeyboardButton("📚 Index", callback_data="cfg#m#index"),
         InlineKeyboardButton("➕ Add Channel", callback_data="cfg#idx_add")],
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
        [InlineKeyboardButton(_status(settings["query_autodelete_enabled"]), callback_data="cfg#tg#query_ad"),
         InlineKeyboardButton(
             f"⏳ Query Auto-Delete ({settings['query_autodelete_seconds']}s)",
             callback_data="cfg#ask#query_delay",
         )],
        [InlineKeyboardButton(_status(settings["file_autodelete_enabled"]), callback_data="cfg#tg#file_ad"),
         InlineKeyboardButton(
             f"🗑 File Auto-Delete ({settings['file_autodelete_seconds']}s)",
             callback_data="cfg#ask#file_delay",
         )],
        [InlineKeyboardButton(_status(settings["file_limit_enabled"]), callback_data="cfg#tg#filelimit"),
         InlineKeyboardButton(
             f"🔢 File Limit ({settings['file_limit_count']}/day)",
             callback_data="cfg#ask#file_limit",
         )],
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


async def build_index_menu(bot, settings: dict):
    channels = settings["index_channels"]
    rows = []
    if channels:
        async def _info(cid):
            try:
                title = (await bot.get_chat(cid)).title
            except RPCError:
                title = str(cid)
            count = await count_by_channel(cid)
            return title, count
        infos = await asyncio.gather(*[_info(c) for c in channels])
        for cid, (title, count) in zip(channels, infos):
            rows.append([InlineKeyboardButton(
                INDEX_MENU_ROW.format(title=title, count=count), callback_data=f"cfg#idx_rm#{cid}"
            )])
    rows.append([InlineKeyboardButton("➕ Add New Channel", callback_data="cfg#idx_add")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="cfg#main")])
    text = INDEX_MENU_HEADER if channels else INDEX_MENU_EMPTY
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
            "query_ad": "query_autodelete_enabled",
            "file_ad": "file_autodelete_enabled",
            "filelimit": "file_limit_enabled",
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
        elif parts[2] == "index":
            text, markup = await build_index_menu(bot, settings)
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
        await _run_channel_add(
            bot, query,
            prompt_text=FSUB_ADD_PROMPT, not_channel_text=FSUB_ADD_NOT_CHANNEL,
            not_admin_text=FSUB_ADD_NOT_ADMIN, ok_text=FSUB_ADD_OK, failed_text=FSUB_ADD_FAILED,
            add_fn=add_fsub_channel, build_menu_fn=build_fsub_menu,
        )
        return

    if action == "idx_rm":
        channel_id = int(parts[2])
        settings = await remove_index_channel(channel_id)
        await query.answer(INDEX_REMOVED_TXT)
        text, markup = await build_index_menu(bot, settings)
        await query.message.edit_text(text, reply_markup=markup)
        return

    if action == "idx_add":
        await query.answer()
        await _run_channel_add(
            bot, query,
            prompt_text=INDEX_ADD_PROMPT, not_channel_text=INDEX_ADD_NOT_CHANNEL,
            not_admin_text=INDEX_ADD_NOT_ADMIN, ok_text=INDEX_ADD_OK, failed_text=INDEX_ADD_FAILED,
            add_fn=add_index_channel, build_menu_fn=build_index_menu,
        )
        return

    if action == "ask":
        await query.answer()
        await _run_ask_number(bot, query, parts[2])
        return


async def _resolve_channel(bot, reply):
    """Turn a listened-for reply (forwarded message or @username/-100 id)
    into a Chat, or None if it can't be resolved."""
    if reply.forward_from_chat and reply.forward_from_chat.type == enums.ChatType.CHANNEL:
        return reply.forward_from_chat, None
    if reply.text:
        text = reply.text.strip()
        try:
            target = int(text) if text.lstrip("-").isdigit() else text
            return await bot.get_chat(target), None
        except RPCError as exc:
            return None, exc
    return None, None


async def _run_channel_add(bot, query, *, prompt_text, not_channel_text, not_admin_text,
                            ok_text, failed_text, add_fn, build_menu_fn):
    prompt = await query.message.edit_text(prompt_text)
    try:
        reply = await bot.listen(chat_id=query.message.chat.id, user_id=query.from_user.id, timeout=90)
    except asyncio.TimeoutError:
        settings = await get_settings()
        text, markup = await build_menu_fn(bot, settings)
        await prompt.edit_text(text, reply_markup=markup)
        return

    chat, error = await _resolve_channel(bot, reply)
    settings = await get_settings()

    if error:
        menu_text, markup = await build_menu_fn(bot, settings)
        await prompt.edit_text(failed_text.format(error=error) + "\n\n" + menu_text, reply_markup=markup)
        return

    if not chat or chat.type != enums.ChatType.CHANNEL:
        text, markup = await build_menu_fn(bot, settings)
        await prompt.edit_text(not_channel_text + "\n\n" + text, reply_markup=markup)
        return

    if not await is_bot_admin_in(bot, chat.id):
        text, markup = await build_menu_fn(bot, settings)
        await prompt.edit_text(not_admin_text + "\n\n" + text, reply_markup=markup)
        return

    settings = await add_fn(chat.id)
    text, markup = await build_menu_fn(bot, settings)
    await prompt.edit_text(ok_text.format(title=chat.title) + "\n\n" + text, reply_markup=markup)


async def _run_ask_number(bot, query, field: str):
    prompts = {
        "query_delay": ASK_QUERY_DELAY_PROMPT,
        "file_delay": ASK_FILE_DELAY_PROMPT,
        "file_limit": ASK_FILE_LIMIT_PROMPT,
    }
    prompt = await query.message.edit_text(prompts[field])
    try:
        reply = await bot.listen(chat_id=query.message.chat.id, user_id=query.from_user.id, timeout=60)
        value = int(reply.text.strip())
        if value < 0:
            raise ValueError
    except asyncio.TimeoutError:
        settings = await get_settings()
        await prompt.edit_text(ASK_NUMBER_TIMEOUT, reply_markup=build_main_menu(settings))
        return
    except (ValueError, AttributeError):
        settings = await get_settings()
        await prompt.edit_text(ASK_NUMBER_INVALID, reply_markup=build_main_menu(settings))
        return

    if field == "query_delay":
        settings = await update_settings({"query_autodelete_seconds": value})
        text = QUERY_DELAY_SET_TXT.format(seconds=value)
    elif field == "file_delay":
        settings = await update_settings({"file_autodelete_seconds": value})
        text = FILE_DELAY_SET_TXT.format(seconds=value)
    else:
        settings = await update_settings({"file_limit_count": value})
        text = FILE_LIMIT_SET_TXT.format(count=value)
        if not settings["verify_enabled"]:
            text += FILE_LIMIT_NEEDS_VERIFY_NOTE

    await prompt.edit_text(text, reply_markup=build_main_menu(settings))


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
