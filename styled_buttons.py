"""
Coloured inline buttons (Telegram's blue / green / red button styles).

Why this module exists: the Pyrogram fork this bot uses (pyrofork 2.3.45)
speaks an older Telegram protocol version that has no button-style field, so a
coloured button can't be sent through it. Telegram's HTTP Bot API does accept
it (`style`: "primary" = blue, "success" = green, "danger" = red).

How it works — and why it can't break anything:
  * The bot sends/edits every message exactly as before (same code, same
    return values).
  * Right after, a background task re-applies the SAME keyboard through the
    Bot API (`editMessageReplyMarkup`) with a colour on every button.
  * If anything goes wrong (old Telegram app, API hiccup, unsupported button
    type, flood limit) the buttons simply stay uncoloured. Nothing else changes.
  * Per message, edits and colour passes are serialized, and a stale colour
    pass is dropped if the message was edited again meanwhile, so fast
    pagination can never be overwritten with an old keyboard.

Colours are chosen automatically by what a button DOES (see RULES below).
Set BUTTON_COLORS=false in the environment to turn the whole thing off.
Colours show on Telegram apps updated after Feb 9 2026; older apps show the
normal grey buttons.
"""
import asyncio
import inspect
import logging
import os
import re
import time
from collections import OrderedDict

import aiohttp
from pyrogram import Client
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
    # admin replies to requests
    ("cb",   r"^(uploaded|not_available|not_released)#", None),  # resolved below by name
    # file / verify / request actions
    ("url",  r"\?start=(file_|getfile)", SUCCESS),
    ("cb",   r"^(req|retry)#", SUCCESS),
    ("text", r"^\W*(verify|get my file|join)\b", SUCCESS),
    # toggles and status icons
    ("text", r"^\W*(✅|☑️|✔️)", SUCCESS),
    ("text", r"^\W*(❌|🚫|✖️|⏹|⛔|🗑)", DANGER),
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
        m = pattern.search(fields[field])
        if m:
            if style is None:  # name-based rule
                return _NAME_STYLES.get(fields[field].split("#", 1)[0], PRIMARY)
            return style
    return PRIMARY


# ── Bot API plumbing ─────────────────────────────────────────────────────────
_session: aiohttp.ClientSession | None = None
_token: str | None = None
_gate = asyncio.Semaphore(6)       # at most 6 colour calls in flight
_fail_streak = 0
_disabled_until = 0.0


def _to_api_button(button):
    """Pyrogram button -> Bot API dict, or None if it's a type we don't convert
    (then the whole message is left uncoloured rather than risk altering it)."""
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


async def _call_api(chat_id, message_id, api_markup) -> bool:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
    url = f"https://api.telegram.org/bot{_token}/editMessageReplyMarkup"
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": api_markup}
    async with _session.post(url, json=payload) as resp:
        data = await resp.json()
    if data.get("ok"):
        return True
    desc = str(data.get("description", ""))
    if "not modified" in desc or "message to edit not found" in desc or "MESSAGE_ID_INVALID" in desc:
        return True  # nothing to do / message already gone — not a failure
    logger.debug("Button colour call rejected: %s", desc)
    return False


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


async def _recolor(chat_id, message_id, markup, seq):
    global _fail_streak, _disabled_until
    if time.time() < _disabled_until:
        return
    try:
        api_markup = _to_api_markup(markup)
        if not api_markup:
            return
        key = (chat_id, message_id)
        async with _gate, _lock_for(key):
            if _seq.get(key) != seq:  # edited again since — a newer pass owns this message
                return
            ok = await _call_api(chat_id, message_id, api_markup)
        _fail_streak = 0 if ok else _fail_streak + 1
    except Exception as exc:
        _fail_streak += 1
        logger.debug("Button colour pass failed: %r", exc)
    if _fail_streak >= 8:  # something systematic — pause instead of hammering the API
        _disabled_until = time.time() + 300
        _fail_streak = 0
        logger.warning("Button colouring paused for 5 minutes after repeated failures")


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


def _markup_of(sig, args, kwargs):
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        return None
    markup = bound.arguments.get("reply_markup")
    return markup if isinstance(markup, InlineKeyboardMarkup) else None


def _wrap(cls, name: str, is_edit: bool):
    original = getattr(cls, name, None)
    if original is None or getattr(original, "_styled", False):
        return False
    sig = inspect.signature(original)
    if "reply_markup" not in sig.parameters:
        return False

    async def wrapper(self, *args, **kwargs):
        markup = _markup_of(sig, (self, *args), kwargs)
        if not markup and not is_edit:
            return await original(self, *args, **kwargs)

        if not is_edit:
            result = await original(self, *args, **kwargs)
            target = result if hasattr(result, "id") and hasattr(result, "chat") else None
            if target:
                key = (target.chat.id, target.id)
                _lock_for(key)  # registers the key so the bookkeeping stays bounded
                _seq[key] = _seq.get(key, 0) + 1
                _spawn(target.chat.id, target.id, markup, _seq[key])
            return result

        # Edits run under the message's lock so they can't interleave with a
        # colour pass that would put an older keyboard back.
        try:
            bound = sig.bind(self, *args, **kwargs).arguments
            chat_id, message_id = bound.get("chat_id"), bound.get("message_id")
        except TypeError:
            chat_id = message_id = None
        if isinstance(chat_id, int) and isinstance(message_id, int):
            key = (chat_id, message_id)
            async with _lock_for(key):
                # Even an edit WITHOUT a keyboard bumps this, so a pending colour
                # pass can never put back a keyboard that was just removed.
                _seq[key] = _seq.get(key, 0) + 1
                seq = _seq[key]
                result = await original(self, *args, **kwargs)
            if markup:
                _spawn(chat_id, message_id, markup, seq)
            return result

        result = await original(self, *args, **kwargs)
        target = result if hasattr(result, "id") and hasattr(result, "chat") else None
        if target:
            key = (target.chat.id, target.id)
            _lock_for(key)
            _seq[key] = _seq.get(key, 0) + 1
            if markup:
                _spawn(target.chat.id, target.id, markup, _seq[key])
        return result

    wrapper.__name__ = original.__name__
    wrapper.__doc__ = original.__doc__
    wrapper._styled = True
    setattr(cls, name, wrapper)
    return True


def install(bot_token: str, client_cls=Client) -> bool:
    """Hook the client. Safe to call once at startup; does nothing when disabled."""
    global _token
    if not ENABLED or not bot_token:
        logger.info("Coloured buttons are OFF")
        return False
    _token = bot_token
    hooked = [n for n in _SEND_METHODS if _wrap(client_cls, n, False)]
    hooked += [n for n in _EDIT_METHODS if _wrap(client_cls, n, True)]
    logger.info("Coloured buttons are ON (%s methods hooked)", len(hooked))
    return True
