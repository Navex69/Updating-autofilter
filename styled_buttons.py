"""
Coloured inline buttons (Telegram's blue / green / red button styles).

Why this module exists: the Pyrogram fork this bot uses (pyrofork 2.3.45)
speaks an older Telegram protocol version with no button-style field, so a
coloured button can't be sent through it. Telegram's HTTP Bot API accepts it
(`style`: "primary" = blue, "success" = green, "danger" = red).

How it works
  * Whenever the bot sends or edits a message WITH an inline keyboard, the call
    is made through the Bot API instead, in ONE step, with colours already on
    the buttons — so a keyboard is never shown grey and then re-coloured, no
    matter how often the message is edited (pagination, menus, toggles...).
  * The bot's code is unchanged and gets the same kind of return value
    (sends return the real Message; edits return a light Message).
  * Text formatting is parsed by Pyrogram itself and passed as entities, so
    messages look identical to before.
  * Anything unusual (a feature the Bot API path doesn't cover, an API error,
    a network problem) falls back to the original Pyrogram call, which then
    behaves — and raises errors — exactly as it always did. In that case the
    colours are applied right after in the background, so the worst case is
    a brief grey flash, never a broken message.
  * Messages without a keyboard are never touched.

Colours are chosen automatically by what a button does (see RULES).
Set BUTTON_COLORS=false in the environment to turn everything off.
Colours show on Telegram apps updated after Feb 9 2026; older apps show grey.
"""
import asyncio
import inspect
import logging
import os
import re
import time
from collections import OrderedDict

import aiohttp
from pyrogram import Client, enums, raw, types, utils
from pyrogram.file_id import FileId, FileType
from pyrogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("BUTTON_COLORS", "true").strip().lower() not in ("0", "false", "no", "off")

PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"  # blue, green, red

# ── what colour a button gets ────────────────────────────────────────────────
# First match wins; anything unmatched is blue. Fields: cb = callback_data,
# url = link, text = label. Green = go / get / confirm / add / ON,
# red = stop / cancel / remove / close / OFF, blue = navigate / info / filters.
RULES = [
    # fast download: red launcher -> green Download -> blue Watch
    ("cb",   r"^fdl#", DANGER),
    ("url",  r"/dl/", SUCCESS),
    ("url",  r"/watch/", PRIMARY),
    # settings / admin panels
    ("cb",   r"^cfg#ask#", PRIMARY),
    ("cb",   r"^cfg#(close|(fsub|idx|mu)_rm)|^idx#(cancel|stop)|^cancel_", DANGER),
    ("cb",   r"^cfg#(idx_add|fsub_add|mu_add)|^idx#go", SUCCESS),
    # /trending: green title buttons (blue Back / Next fall through to the default)
    ("cb",   r"^trq#", SUCCESS),
    # admin replies to requests (colour by the action name)
    ("cb",   r"^(uploaded|not_available|not_released)#", None),
    # file / verify / request actions
    ("url",  r"\?start=(file_|getfile)", SUCCESS),
    ("cb",   r"^(req|retry)#", SUCCESS),
    ("text", r"^\W*(verify|get my file|join)\b", SUCCESS),
    # toggles and status icons
    ("text", r"^\W*(✅|☑️|✔️)", SUCCESS),
    ("text", r"^\W*(❌|🚫|✖️|⏹|⛔|🗑)", DANGER),
    # confirmation / destructive wording (word boundaries: "Unban" is not "ban")
    ("text", r"\b(yes|unban|approve|accept|confirm|allow)\b", SUCCESS),
    ("text", r"\b(ban|block|delete|remove|reset|leave|cancel|close|stop|reject|decline)\b", DANGER),
]
_NAME_STYLES = {"uploaded": SUCCESS, "not_available": DANGER, "not_released": DANGER}
_COMPILED = [(f, re.compile(p, re.I), s) for f, p, s in RULES]


def style_for(button) -> str:
    explicit = getattr(button, "_style", None)
    if explicit:
        return explicit
    cb = button.callback_data
    if isinstance(cb, bytes):
        cb = cb.decode("utf-8", "ignore")
    fields = {"cb": cb or "", "url": button.url or "", "text": button.text or ""}
    for field, pattern, style in _COMPILED:
        if pattern.search(fields[field]):
            if style is None:  # name-based rule
                return _NAME_STYLES.get(fields[field].split("#", 1)[0], PRIMARY)
            return style
    return PRIMARY


# ── keyboard -> Bot API JSON ─────────────────────────────────────────────────
def _to_api_button(button):
    """None if it's a button type we don't convert (the whole message then
    goes through the normal path instead of risking an altered keyboard)."""
    if button.requires_password or button.callback_game or button.login_url:
        return None
    d = {"text": button.text, "style": style_for(button)}
    cb = button.callback_data
    if cb is not None:
        d["callback_data"] = cb.decode("utf-8") if isinstance(cb, bytes) else cb
    elif button.url:
        d["url"] = button.url
    elif button.user_id:
        d["url"] = f"tg://user?id={button.user_id}"
    elif button.switch_inline_query is not None:
        d["switch_inline_query"] = button.switch_inline_query
    elif button.switch_inline_query_current_chat is not None:
        d["switch_inline_query_current_chat"] = button.switch_inline_query_current_chat
    elif button.web_app:
        d["web_app"] = {"url": button.web_app.url}
    else:
        return None
    return d


def _to_api_markup(markup: InlineKeyboardMarkup):
    rows = []
    for row in markup.inline_keyboard:
        out = []
        for button in row:
            converted = _to_api_button(button)
            if converted is None:
                return None
            out.append(converted)
        rows.append(out)
    return {"inline_keyboard": rows} if rows else None


# ── Bot API transport ────────────────────────────────────────────────────────
_token: str | None = None
_session: aiohttp.ClientSession | None = None
_gate = asyncio.Semaphore(20)
_fail_streak = 0
_paused_until = 0.0


async def _http_post(method: str, payload: dict):
    """-> (http_status | None, json | None). Patched in tests."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    try:
        async with _gate, _session.post(
            f"https://api.telegram.org/bot{_token}/{method}", json=payload
        ) as resp:
            return resp.status, await resp.json(content_type=None)
    except Exception as exc:
        logger.debug("Bot API %s failed: %r", method, exc)
        return None, None


def _api_available() -> bool:
    return time.time() >= _paused_until


async def _call(method: str, payload: dict):
    """-> result dict, or None (caller falls back to the normal Pyrogram call)."""
    global _fail_streak, _paused_until
    status, data = await _http_post(method, payload)
    if data and data.get("ok"):
        _fail_streak = 0
        return data["result"]
    # 400s are ordinary per-message problems ("not modified", deleted, can't
    # edit...). Anything else means the Bot API path itself is unhealthy.
    if status is None or status >= 500 or status in (401, 404, 429):
        _fail_streak += 1
        if _fail_streak >= 6:
            _paused_until = time.time() + 300
            _fail_streak = 0
            logger.warning("Coloured buttons: Bot API path paused for 5 minutes after repeated failures")
    else:
        _fail_streak = 0
        logger.debug("Bot API %s rejected: %s", method, (data or {}).get("description"))
    return None


# ── text formatting: let Pyrogram parse, hand the entities to the Bot API ────
def _entity_to_api(e):
    name = type(e).__name__
    base = {"offset": e.offset, "length": e.length}
    simple = {
        "MessageEntityBold": "bold", "MessageEntityItalic": "italic",
        "MessageEntityUnderline": "underline", "MessageEntityStrike": "strikethrough",
        "MessageEntitySpoiler": "spoiler", "MessageEntityCode": "code",
    }
    if name in simple:
        return {**base, "type": simple[name]}
    if name == "MessageEntityPre":
        d = {**base, "type": "pre"}
        if getattr(e, "language", None):
            d["language"] = e.language
        return d
    if name == "MessageEntityTextUrl":
        return {**base, "type": "text_link", "url": e.url}
    if name in ("MessageEntityMentionName", "InputMessageEntityMentionName"):
        uid = getattr(e.user_id, "user_id", e.user_id)
        return {**base, "type": "text_mention", "user": {"id": int(uid)}}
    if name == "MessageEntityBlockquote":
        return {**base, "type": "expandable_blockquote" if getattr(e, "collapsed", False) else "blockquote"}
    if name == "MessageEntityCustomEmoji":
        return {**base, "type": "custom_emoji", "custom_emoji_id": str(e.document_id)}
    return None  # something we don't map -> don't risk changing the formatting


async def _parse(client, text, parse_mode):
    """-> (plain_text, [api entities]) using the exact same parser as the
    normal path, or None."""
    try:
        parsed = await utils.parse_text_entities(client, text, parse_mode, None)
    except Exception:
        return None
    out = []
    for e in parsed["entities"] or []:
        d = _entity_to_api(e)
        if d is None:
            return None
        out.append(d)
    return parsed["message"], out


# ── per-method Bot API handlers (return a Message, or None = use normal path) ─
def _chat_ref(chat_id):
    if isinstance(chat_id, int):
        return chat_id
    if isinstance(chat_id, str) and chat_id.startswith("@"):
        return chat_id
    return None  # "me", deep objects, ... -> normal path


def _common(payload: dict, a: dict):
    if a.get("disable_notification"):
        payload["disable_notification"] = True
    if a.get("protect_content"):
        payload["protect_content"] = True
    if a.get("message_thread_id"):
        payload["message_thread_id"] = a["message_thread_id"]
    if a.get("reply_to_message_id"):
        payload["reply_parameters"] = {"message_id": a["reply_to_message_id"]}


def _blocked(a: dict, names) -> bool:
    return any(a.get(n) for n in names)


async def _fetch_message(client, chat_id, message_id, text=None):
    """The real Message for a message just sent (so callers can use .link,
    .delete(), .id ... exactly as before); a light stand-in if the lookup fails."""
    try:
        msg = await client.get_messages(chat_id, message_id)
        if msg and not msg.empty:
            return msg
    except Exception:
        pass
    ctype = enums.ChatType.PRIVATE if (isinstance(chat_id, int) and chat_id > 0) else enums.ChatType.SUPERGROUP
    return types.Message(id=message_id, client=client, chat=types.Chat(id=chat_id, type=ctype, client=client), text=text)


_SEND_BLOCK = ("business_connection_id", "reply_to_story_id", "reply_to_chat_id", "quote_text",
               "quote_entities", "schedule_date", "invert_media", "message_effect_id")


async def _h_send_message(client, a, api_markup):
    chat = _chat_ref(a.get("chat_id"))
    if chat is None or _blocked(a, _SEND_BLOCK + ("entities",)) or not a.get("text"):
        return None
    parsed = await _parse(client, a["text"], a.get("parse_mode"))
    if parsed is None:
        return None
    text, entities = parsed
    payload = {"chat_id": chat, "text": text, "reply_markup": api_markup}
    if entities:
        payload["entities"] = entities
    if a.get("disable_web_page_preview"):
        payload["link_preview_options"] = {"is_disabled": True}
    _common(payload, a)
    result = await _call("sendMessage", payload)
    if result is None:
        return None
    return await _fetch_message(client, result["chat"]["id"], result["message_id"], text)


_MEDIA_METHODS = {
    FileType.PHOTO: ("sendPhoto", "photo"), FileType.VIDEO: ("sendVideo", "video"),
    FileType.AUDIO: ("sendAudio", "audio"), FileType.DOCUMENT: ("sendDocument", "document"),
    FileType.ANIMATION: ("sendAnimation", "animation"), FileType.VOICE: ("sendVoice", "voice"),
}


async def _h_send_cached_media(client, a, api_markup):
    chat = _chat_ref(a.get("chat_id"))
    file_id = a.get("file_id")
    if chat is None or not isinstance(file_id, str) or _blocked(a, _SEND_BLOCK + ("caption_entities",)):
        return None
    try:
        method, field = _MEDIA_METHODS.get(FileId.decode(file_id).file_type, (None, None))
    except Exception:
        return None
    if not method:
        return None
    payload = {"chat_id": chat, field: file_id, "reply_markup": api_markup}
    caption = a.get("caption")
    if caption:
        parsed = await _parse(client, caption, a.get("parse_mode"))
        if parsed is None:
            return None
        payload["caption"] = parsed[0]
        if parsed[1]:
            payload["caption_entities"] = parsed[1]
    if a.get("has_spoiler"):
        payload["has_spoiler"] = True
    _common(payload, a)
    result = await _call(method, payload)
    if result is None:
        return None
    return await _fetch_message(client, result["chat"]["id"], result["message_id"])


def _edit_ref(a):
    chat, mid = _chat_ref(a.get("chat_id")), a.get("message_id")
    if chat is None or not isinstance(mid, int) or a.get("business_connection_id"):
        return None, None
    return chat, mid


async def _h_edit_text(client, a, api_markup):
    chat, mid = _edit_ref(a)
    if chat is None or _blocked(a, ("entities", "invert_media")) or not a.get("text"):
        return None
    parsed = await _parse(client, a["text"], a.get("parse_mode"))
    if parsed is None:
        return None
    text, entities = parsed
    payload = {"chat_id": chat, "message_id": mid, "text": text, "reply_markup": api_markup}
    if entities:
        payload["entities"] = entities
    if a.get("disable_web_page_preview"):
        payload["link_preview_options"] = {"is_disabled": True}
    if await _call("editMessageText", payload) is None:
        return None
    return _light_message(client, chat, mid, text=text)


async def _h_edit_caption(client, a, api_markup):
    chat, mid = _edit_ref(a)
    if chat is None or _blocked(a, ("caption_entities", "invert_media")):
        return None
    payload = {"chat_id": chat, "message_id": mid, "reply_markup": api_markup}
    caption = a.get("caption")
    if caption:
        parsed = await _parse(client, caption, a.get("parse_mode"))
        if parsed is None:
            return None
        payload["caption"] = parsed[0]
        if parsed[1]:
            payload["caption_entities"] = parsed[1]
    if await _call("editMessageCaption", payload) is None:
        return None
    return _light_message(client, chat, mid, caption=caption)


async def _h_edit_markup(client, a, api_markup):
    chat, mid = _edit_ref(a)
    if chat is None:
        return None
    payload = {"chat_id": chat, "message_id": mid, "reply_markup": api_markup}
    if await _call("editMessageReplyMarkup", payload) is None:
        return None
    return _light_message(client, chat, mid)


def _light_message(client, chat_id, message_id, **fields):
    ctype = enums.ChatType.PRIVATE if (isinstance(chat_id, int) and chat_id > 0) else enums.ChatType.SUPERGROUP
    return types.Message(id=message_id, client=client, chat=types.Chat(id=chat_id, type=ctype, client=client), **fields)


_HANDLERS = {
    "send_message": _h_send_message,
    "send_cached_media": _h_send_cached_media,
    "edit_message_text": _h_edit_text,
    "edit_message_caption": _h_edit_caption,
    "edit_message_reply_markup": _h_edit_markup,
}

# ── per-message ordering ─────────────────────────────────────────────────────
_locks: "OrderedDict[tuple, asyncio.Lock]" = OrderedDict()
_seq: dict = {}


def _lock_for(key):
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
        while len(_locks) > 2000:
            old_key, old_lock = next(iter(_locks.items()))
            if old_lock.locked():
                break
            _locks.pop(old_key, None)
            _seq.pop(old_key, None)
    else:
        _locks.move_to_end(key)
    return lock


def _bump(key) -> int:
    _lock_for(key)
    _seq[key] = _seq.get(key, 0) + 1
    return _seq[key]


# ── fallback: normal Pyrogram call, then colour the keyboard right after ─────
async def _recolor(chat_id, message_id, markup, seq):
    try:
        api_markup = _to_api_markup(markup)
        if not api_markup or not _api_available():
            return
        key = (chat_id, message_id)
        async with _lock_for(key):
            if _seq.get(key) != seq:  # edited again since — a newer pass owns it
                return
            await _call("editMessageReplyMarkup",
                        {"chat_id": chat_id, "message_id": message_id, "reply_markup": api_markup})
    except Exception as exc:
        logger.debug("Button colour pass failed: %r", exc)


_tasks: set = set()


def _spawn(chat_id, message_id, markup, seq):
    task = asyncio.create_task(_recolor(chat_id, message_id, markup, seq))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── hooks on the Pyrogram client ─────────────────────────────────────────────
_SEND_METHODS = (
    "send_message", "send_photo", "send_document", "send_video", "send_audio",
    "send_animation", "send_voice", "send_cached_media", "copy_message",
)
_EDIT_METHODS = (
    "edit_message_text", "edit_message_caption", "edit_message_reply_markup", "edit_message_media",
)


def _bind(sig, args, kwargs):
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        return None
    bound.apply_defaults()
    return dict(bound.arguments)


def _wrap(cls, name: str, is_edit: bool):
    original = getattr(cls, name, None)
    if original is None or getattr(original, "_styled", False):
        return False
    sig = inspect.signature(original)
    if "reply_markup" not in sig.parameters:
        return False
    handler = _HANDLERS.get(name)

    async def wrapper(self, *args, **kwargs):
        a = _bind(sig, (self, *args), kwargs)
        markup = a.get("reply_markup") if a else None
        markup = markup if isinstance(markup, InlineKeyboardMarkup) else None
        if not markup and not is_edit:
            return await original(self, *args, **kwargs)

        api_markup = None
        if markup and handler and _api_available() and getattr(self, "bot_token", None) == _token:
            api_markup = _to_api_markup(markup)

        if not is_edit:
            if api_markup:
                try:
                    msg = await handler(self, a, api_markup)
                except Exception:
                    logger.debug("Bot API send path failed, using normal path", exc_info=True)
                    msg = None
                if msg is not None:
                    _bump((msg.chat.id, msg.id))
                    return msg
            result = await original(self, *args, **kwargs)
            if markup and hasattr(result, "id") and hasattr(result, "chat"):
                _spawn(result.chat.id, result.id, markup, _bump((result.chat.id, result.id)))
            return result

        chat_id, message_id = a.get("chat_id") if a else None, a.get("message_id") if a else None
        if isinstance(chat_id, int) and isinstance(message_id, int):
            key = (chat_id, message_id)
            # Edits for one message run one at a time, so a colour pass can never
            # put an older keyboard back. Even a keyboard-less edit bumps the
            # counter so a pending pass can't resurrect a removed keyboard.
            async with _lock_for(key):
                seq = _bump(key)
                if api_markup:
                    try:
                        msg = await handler(self, a, api_markup)
                    except Exception:
                        logger.debug("Bot API edit path failed, using normal path", exc_info=True)
                        msg = None
                    if msg is not None:
                        return msg
                result = await original(self, *args, **kwargs)
            if markup:
                _spawn(chat_id, message_id, markup, seq)
            return result

        result = await original(self, *args, **kwargs)
        if markup and hasattr(result, "id") and hasattr(result, "chat"):
            _spawn(result.chat.id, result.id, markup, _bump((result.chat.id, result.id)))
        return result

    wrapper.__name__ = original.__name__
    wrapper.__doc__ = original.__doc__
    wrapper._styled = True
    setattr(cls, name, wrapper)
    return True


def install(bot_token: str, client_cls=Client) -> bool:
    """Hook the client. Call once at startup; does nothing when disabled."""
    global _token
    if not ENABLED or not bot_token:
        logger.info("Coloured buttons are OFF")
        return False
    _token = bot_token
    hooked = [n for n in _SEND_METHODS if _wrap(client_cls, n, False)]
    hooked += [n for n in _EDIT_METHODS if _wrap(client_cls, n, True)]
    logger.info("Coloured buttons are ON (%s methods hooked)", len(hooked))
    return True
