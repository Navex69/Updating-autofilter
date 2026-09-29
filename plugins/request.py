"""
Request plugin - handles user file requests to admins.

Flow:
  user (search button or /req)  ->  send_request()  ->  REQUEST_CHANNEL post
  admin taps "Show Options"     ->  picks a canned reply / custom reply
  the requester gets the reply in PM, the channel post is struck through.

IMPORTANT (why the bot used to go silent): pyrogram runs only the FIRST
matching handler in a group, and plugins load alphabetically. The old
"admin typed a custom reply" handler matched *every* private text message and
loaded before search.py / start.py / settings.py, so it swallowed all
commands and searches. The input handler below now uses a filter that only
matches when the message is a reply to one of our pending admin prompts.
"""
import html
import logging
import re
import time

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    RPCError, UserIsBlocked, InputUserDeactivated, PeerIdInvalid,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, REQUEST_CHANNEL
from database.request_db import add_request
from strings import (
    REQUEST_SENT_TXT, REQUEST_RECEIVED_TXT, REQUEST_NOT_CONFIGURED_TXT,
    ALREADY_AVAILABLE_TXT, NOT_RELEASED_TXT, CHECK_SPELLING_TXT, UPLOADED_TXT,
    NOT_AVAILABLE_TXT, YEAR_LANGUAGE_TXT, WRONG_SPELLING_TXT, CUSTOM_REPLY_TXT,
)

logger = logging.getLogger(__name__)

_ADMIN_STATUSES = (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
_GROUP_TYPES = (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
_DELIVERY_ERRORS = (UserIsBlocked, InputUserDeactivated, PeerIdInvalid)

_DEDUP_TTL = 3600       # same user + same query is ignored for an hour
_WAIT_TTL = 3600        # abandoned admin prompts are forgotten after an hour

# user_id -> (dedup_key, timestamp)
_REQUEST_DEDUP: dict = {}
# (chat_id, prompt_message_id) -> {...}
_CUSTOM_REPLY_WAIT: dict = {}
_WRONG_SPELL_WAIT: dict = {}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _msg_link(message):
    """message.link raises / returns None for private chats — never let that
    break a button."""
    try:
        return message.link
    except Exception:
        return None


async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    if user_id in ADMINS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except RPCError:
        return False
    return member.status in _ADMIN_STATUSES


def _requested_name(message) -> str:
    """Pull just the requested title out of the request-channel post (the
    text is the whole '#FILE_REQUEST ...' block). HTML-escaped because every
    template wraps it in <code>."""
    text = (message.text or message.caption or "").strip()
    m = re.search(r"Query:\s*(.+)", text)
    return html.escape((m.group(1) if m else text).strip())


def _purge_waits():
    cutoff = time.time() - _WAIT_TTL
    for store in (_CUSTOM_REPLY_WAIT, _WRONG_SPELL_WAIT):
        for key in [k for k, v in store.items() if v["time"] < cutoff]:
            store.pop(key, None)


def is_duplicate_request(user_id: int, query: str) -> bool:
    entry = _REQUEST_DEDUP.get(user_id)
    if not entry:
        return False
    key, ts = entry
    return key == f"{user_id}:{query.lower().strip()}" and time.time() - ts < _DEDUP_TTL


async def _strike_and_mark(message, label_button: InlineKeyboardButton):
    """Strike through the request post and swap its keyboard for one status
    button, in a single edit."""
    original = message.text.html if message.text else ""
    try:
        await message.edit_text(
            f"<s>{original}</s>",
            reply_markup=InlineKeyboardMarkup([[label_button]]),
        )
    except RPCError:
        logger.warning("Couldn't update request post %s", message.id, exc_info=True)


async def _notify_requester(bot, user_id: int, text: str, status_link: str | None):
    markup = None
    if status_link:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("♻️ View Status ♻️", url=status_link)]])
    try:
        await bot.send_message(chat_id=user_id, text=text, reply_markup=markup)
    except _DELIVERY_ERRORS:
        pass  # user blocked the bot / deleted account — nothing more to do
    except RPCError:
        logger.warning("Couldn't notify requester %s", user_id, exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# Sending a request to the admin channel
# ══════════════════════════════════════════════════════════════════════════════

async def send_request(bot, user_id: int, query: str, username: str = None, origin_message=None):
    """Post a file request to REQUEST_CHANNEL. Returns the sent message, or
    None if not configured / duplicate / failed."""
    if not REQUEST_CHANNEL:
        return None

    if is_duplicate_request(user_id, query):
        return None

    username = username or f"ID: {user_id}"

    buttons = []
    origin_link = _msg_link(origin_message) if origin_message else None
    if origin_message and origin_message.chat.type in _GROUP_TYPES and origin_link:
        buttons.append([InlineKeyboardButton("👀 View Request", url=origin_link)])
    else:
        buttons.append([InlineKeyboardButton("👀 View Request", callback_data=f"view_req_{user_id}")])

    buttons.append([InlineKeyboardButton(
        "⚙ Show Options",
        callback_data=f"show_options#{user_id}#{origin_message.id if origin_message else 0}",
    )])

    request_text = REQUEST_RECEIVED_TXT.format(
        user_mention=f"<a href='tg://user?id={user_id}'>{html.escape(str(username))}</a>",
        user_id=user_id,
        query=html.escape(query),
    )

    try:
        sent = await bot.send_message(
            REQUEST_CHANNEL, request_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to send request to channel %s", REQUEST_CHANNEL)
        return None

    # Only remember it once it actually went out, so a failed send can be retried.
    _REQUEST_DEDUP[user_id] = (f"{user_id}:{query.lower().strip()}", time.time())
    try:
        await add_request(user_id, query, username)
    except Exception:
        logger.warning("Couldn't store request in DB", exc_info=True)
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# Admin callbacks
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^view_req_"))
async def view_request_callback(bot, query):
    if not await _is_admin(bot, query.message.chat.id, query.from_user.id):
        return await query.answer("⚠️ Admin only!", show_alert=True)
    user_id = query.data.split("_")[2]
    await query.answer(f"Requested in the bot's PM by user ID {user_id}", show_alert=True)


@Client.on_callback_query(filters.regex(r"^show_options#"))
async def show_options_callback(bot, query):
    _, user_id, msg_id = query.data.split("#")
    if not await _is_admin(bot, query.message.chat.id, query.from_user.id):
        return await query.answer("⚠️ Admin only!", show_alert=True)

    buttons = [
        [InlineKeyboardButton("Already Available", callback_data=f"already_available#{user_id}#{msg_id}"),
         InlineKeyboardButton("Not Released Yet", callback_data=f"not_released#{user_id}#{msg_id}")],
        [InlineKeyboardButton("Tell Me Year/Language", callback_data=f"year#{user_id}#{msg_id}"),
         InlineKeyboardButton("Check Your Spelling", callback_data=f"upload_in#{user_id}#{msg_id}")],
        [InlineKeyboardButton("Uploaded", callback_data=f"uploaded#{user_id}#{msg_id}"),
         InlineKeyboardButton("Not Available", callback_data=f"not_available#{user_id}#{msg_id}")],
        [InlineKeyboardButton("Uploaded, Wrong Spelling", callback_data=f"spl_wrong#{user_id}#{msg_id}")],
        [InlineKeyboardButton("💬 Custom Reply", callback_data=f"custom_reply#{user_id}#{msg_id}")],
    ]
    try:
        await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
    except RPCError:
        pass
    await query.answer()


# kind -> (message sent to requester, label left on the channel post, alert prefix)
_RESPONSES = {
    "not_released":      (NOT_RELEASED_TXT,      "🚫 Not Released 🚫",        "na_alert"),
    "not_available":     (NOT_AVAILABLE_TXT,     "🚫 Not Available 🚫",       "hm_alert"),
    "uploaded":          (UPLOADED_TXT,          "🙂 Uploaded 🙂",            "ul_alert"),
    "already_available": (ALREADY_AVAILABLE_TXT, "🫤 Already Available 🫤",   "aa_alert"),
    "upload_in":         (CHECK_SPELLING_TXT,    "⚠️ Check Your Spelling ⚠️",  "upload_alert"),
    "year":              (YEAR_LANGUAGE_TXT,     "⚠️ Tell Me Year/Language ⚠️", "yrs_alert"),
}


@Client.on_callback_query(filters.regex(r"^(not_released|not_available|uploaded|already_available|upload_in|year)#"))
async def canned_response_callback(bot, query):
    kind, user_id, _msg_id = query.data.split("#")
    user_id = int(user_id)
    template, label, alert = _RESPONSES[kind]

    if not await _is_admin(bot, query.message.chat.id, query.from_user.id):
        return await query.answer("⚠️ Admin only!", show_alert=True)

    requested_name = _requested_name(query.message)
    link = _msg_link(query.message)

    await query.answer("Message sent to requester")
    await _strike_and_mark(
        query.message,
        InlineKeyboardButton(label, callback_data=f"{alert}#{user_id}"),
    )
    await _notify_requester(bot, user_id, template.format(requested_name=requested_name), link)


# ── Admin prompts that wait for a typed reply ────────────────────────────────

async def _open_prompt(bot, query, *, text: str, cancel_prefix: str, store: dict):
    _, user_id, msg_id = query.data.split("#")
    chat_id = query.message.chat.id

    if not await _is_admin(bot, chat_id, query.from_user.id):
        return await query.answer("⚠️ Admin only!", show_alert=True)

    _purge_waits()
    prompt = await bot.send_message(
        chat_id, text,
        reply_to_message_id=query.message.id,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"{cancel_prefix}#{query.message.id}")]]),
    )
    store[(chat_id, prompt.id)] = {
        "user_id": int(user_id), "msg_id": int(msg_id),
        "request_msg": query.message, "time": time.time(),
    }
    await query.answer()


@Client.on_callback_query(filters.regex(r"^spl_wrong#"))
async def spl_wrong_callback(bot, query):
    await _open_prompt(bot, query, text="✏️ <b>Send correct spelling</b>",
                       cancel_prefix="cancel_wrong", store=_WRONG_SPELL_WAIT)


@Client.on_callback_query(filters.regex(r"^custom_reply#"))
async def custom_reply_callback(bot, query):
    await _open_prompt(bot, query, text="💬 <b>Send your custom reply message for the user</b>",
                       cancel_prefix="cancel_custom", store=_CUSTOM_REPLY_WAIT)


async def _cancel_prompt(bot, query, store: dict):
    # The cancel button lives on the prompt message itself.
    key = (query.message.chat.id, query.message.id)
    if not await _is_admin(bot, query.message.chat.id, query.from_user.id):
        return await query.answer("⚠️ Admin only!", show_alert=True)
    if store.pop(key, None) is None:
        await query.answer("Nothing to cancel", show_alert=True)
    else:
        await query.answer("Cancelled")
    try:
        await query.message.delete()
    except RPCError:
        pass


@Client.on_callback_query(filters.regex(r"^cancel_wrong#"))
async def cancel_wrong_callback(bot, query):
    await _cancel_prompt(bot, query, _WRONG_SPELL_WAIT)


@Client.on_callback_query(filters.regex(r"^cancel_custom#"))
async def cancel_custom_callback(bot, query):
    await _cancel_prompt(bot, query, _CUSTOM_REPLY_WAIT)


# ── Requester-facing alerts (button left on the channel post) ───────────────

_ALERTS = {
    "na_alert": "Sorry your request is not available, make sure it's released. If yes, then give us some time 🤗",
    "hm_alert": "❌ Your requested movie is not available on the internet.",
    "ul_alert": "Your request is uploaded",
    "aa_alert": "Your request is already available, you need to check first and then make a request 🤨",
    "upload_alert": "You undeducated, check your spelling 😑",
    "yrs_alert": "Dude you need to provide more info 😑 (like : year, language, hollywood or bollywood)",
    "ulws_alert": "Correct spelling provided by admin ✏️",
    "responded_alert": "Admin has replied to your request 💬",
}


@Client.on_callback_query(filters.regex(r"^(na_alert|hm_alert|ul_alert|aa_alert|upload_alert|yrs_alert|ulws_alert|responded_alert)#"))
async def alert_callback(_, query):
    kind, user_id = query.data.split("#")
    if str(query.from_user.id) == user_id:
        await query.answer(_ALERTS[kind], show_alert=True)
    else:
        await query.answer("⚠️ Admin only!", show_alert=True)


# ══════════════════════════════════════════════════════════════════════════════
# Admin's typed reply (custom reply / correct spelling)
#
# The filter only matches a reply to one of OUR pending prompts, so it can
# never intercept ordinary commands or searches.
# ══════════════════════════════════════════════════════════════════════════════

def _is_pending_prompt_reply(_, __, message) -> bool:
    reply = message.reply_to_message
    if not reply or not message.text:
        return False
    key = (message.chat.id, reply.id)
    return key in _CUSTOM_REPLY_WAIT or key in _WRONG_SPELL_WAIT


pending_prompt_reply = filters.create(_is_pending_prompt_reply)


@Client.on_message(filters.text & filters.reply & pending_prompt_reply)
async def handle_admin_prompt_reply(bot, message):
    key = (message.chat.id, message.reply_to_message.id)

    # Messages posted "as the channel" have no from_user; only admins can post
    # there. A real user account must be an admin.
    if message.from_user and not await _is_admin(bot, message.chat.id, message.from_user.id):
        return

    if key in _CUSTOM_REPLY_WAIT:
        data = _CUSTOM_REPLY_WAIT.pop(key)
        text = CUSTOM_REPLY_TXT.format(custom_message=message.text.html)
        label = InlineKeyboardButton("💬 Admin Replied 💬", callback_data=f"responded_alert#{data['user_id']}")
    else:
        data = _WRONG_SPELL_WAIT.pop(key)
        text = WRONG_SPELLING_TXT.format(correct_spelling=html.escape(message.text))
        label = InlineKeyboardButton("✏️ Correct Spelling ✏️", callback_data=f"ulws_alert#{data['user_id']}")

    req_msg = data["request_msg"]
    await _notify_requester(bot, data["user_id"], text, _msg_link(req_msg))
    await _strike_and_mark(req_msg, label)

    # Tidy up the admin's typed message and the prompt.
    for m in (message, message.reply_to_message):
        try:
            await m.delete()
        except RPCError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# /req and /request
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command(["req", "request"]) & filters.private)
async def manual_request_cmd(bot, message):
    """/req <file name> — lets a user request a file manually."""
    if not REQUEST_CHANNEL:
        await message.reply_text(REQUEST_NOT_CONFIGURED_TXT)
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply_text(
            "📮 <b>How to use:</b>\n\n"
            "<code>/request Movie Name</code>\n"
            "<code>/req Movie Name</code>\n\n"
            "Example: <code>/req Inception 2010</code>"
        )
        return

    query = args[1].strip()
    user = message.from_user
    if not user:
        return

    if is_duplicate_request(user.id, query):
        await message.reply_text("📮 You already requested this — the admins have it, please wait.")
        return

    sent = await send_request(
        bot,
        user_id=user.id,
        query=query,
        username=user.username or user.first_name,
        origin_message=message,
    )

    if sent:
        link = _msg_link(sent)
        await message.reply_text(
            REQUEST_SENT_TXT.format(query=html.escape(query)),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ View Your Request ✨", url=link)]]) if link else None,
        )
    else:
        await message.reply_text("❌ Failed to send request. Please try again later.")
