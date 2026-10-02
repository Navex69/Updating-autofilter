"""
"⚡ Fast Download" button handler. The button sits under every delivered file
(added in deliver._send_file, only when the feature is configured). A tap:
  1. checks the user's daily link quota,
  2. makes sure the file has a copy in BIN_CHANNEL (copied once, then reused),
  3. picks a host (Oracle first, Koyeb/Render as fallback),
  4. swaps the button for a signed, expiring download link.
Delivery itself and all its gates (premium / force-sub / limit / verify) are
untouched — this only runs after the file has already been delivered.
"""
import logging

from pyrogram import Client, filters
from pyrogram.errors import RPCError

from config import (
    BIN_CHANNEL, FASTDL_ENABLED, STREAM_LINK_TTL_HOURS,
    STREAM_DAILY_LIMIT, STREAM_PREMIUM_DAILY_LIMIT,
)
from database.filters_db import get_file_by_id, display_name
from database.premium_db import has_premium_access
from database.stream_db import (
    get_bin_entry, save_bin_entry, add_usage, get_daily, increment_daily,
)
from fastdl.hosts import choose_host
from fastdl.links import build_url, ready_markup
from strings import (
    FILE_NOT_FOUND_TXT, FAST_LINK_READY_TXT, FAST_LIMIT_REACHED_TXT,
    FAST_UNAVAILABLE_TXT, FAST_ERROR_TXT,
)

logger = logging.getLogger(__name__)
_busy: set = set()  # (user, file) pairs mid-request, so double-taps can't double-count


async def _answer(query, text: str, alert: bool = False):
    try:
        await query.answer(text, show_alert=alert)
    except RPCError:
        pass  # query too old — nothing useful to do


@Client.on_callback_query(filters.regex(r"^fdl#"))
async def fast_download(bot, query):
    if not FASTDL_ENABLED:
        await _answer(query, "Fast download isn't available.", True)
        return

    file_id = query.data.split("#", 1)[1]
    user_id = query.from_user.id
    key = (user_id, file_id)
    if key in _busy:
        await _answer(query, "⏳ Working on it...")
        return
    _busy.add(key)
    try:
        limit = STREAM_PREMIUM_DAILY_LIMIT if await has_premium_access(user_id) else STREAM_DAILY_LIMIT
        if await get_daily(user_id) >= limit:
            await _answer(query, FAST_LIMIT_REACHED_TXT.format(limit=limit), True)
            return

        doc = await get_file_by_id(file_id)
        if not doc:
            await _answer(query, FILE_NOT_FOUND_TXT, True)
            return

        entry = await get_bin_entry(file_id)
        if not entry:
            sent = await bot.send_cached_media(
                chat_id=BIN_CHANNEL, file_id=doc["file_id"], caption=display_name(doc)[:900],
            )
            media = sent.document or sent.video or sent.audio
            size = int(doc.get("file_size") or getattr(media, "file_size", 0) or 0)
            entry = await save_bin_entry(file_id, sent.id, size, doc.get("file_name", "file"))

        size = int(entry.get("size") or 0)
        host = await choose_host(size)
        if not host:
            await _answer(query, FAST_UNAVAILABLE_TXT, True)
            return

        url = build_url(host.base_url, entry["bin_msg_id"], user_id, entry.get("name", "file"))
        await query.message.edit_reply_markup(ready_markup(file_id, url))
        await add_usage(host.key, size)
        await increment_daily(user_id)
        await _answer(query, FAST_LINK_READY_TXT.format(hours=STREAM_LINK_TTL_HOURS))
    except RPCError:
        logger.exception("Fast download failed for file %s (user %s)", file_id, user_id)
        await _answer(query, FAST_ERROR_TXT, True)
    except Exception:
        logger.exception("Fast download crashed for file %s (user %s)", file_id, user_id)
        await _answer(query, FAST_ERROR_TXT, True)
    finally:
        _busy.discard(key)
