import hashlib
import time

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ENABLE_PM_SEARCH, RESULTS_PER_PAGE, MIN_QUERY_LEN
from database.filters_db import search_files
from database.settings_db import get_settings
from utils import temp, human_size
from strings import NOT_FOUND_TXT, RESULT_HEADER_TXT, SEARCH_EXPIRED_TXT

# ══════════════════════════════════════════════════════════════════════════════
# Per-query result cache — one DB round trip per search, pagination is free.
# The display mode (button/text) is snapshotted at search time, so an admin
# toggling it mid-way doesn't mix formats within the same result set.
# ══════════════════════════════════════════════════════════════════════════════

_CACHE: dict = {}
_CACHE_TTL = 600  # 10 minutes
_CACHE_MAX_ENTRIES = 300


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


def _cache_put(key: str, query: str, results: list, mode: str):
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["time"])
        _CACHE.pop(oldest, None)
    _CACHE[key] = {"query": query, "results": results, "mode": mode, "time": time.time()}


def _cache_get(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["time"] > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return entry


def _nav_row(key: str, offset: int, total: int) -> list:
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg#{key}#{max(0, offset - RESULTS_PER_PAGE)}"))
    if offset + RESULTS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg#{key}#{offset + RESULTS_PER_PAGE}"))
    return nav


def _render_page(key: str, query: str, results: list, mode: str, offset: int):
    """Return (text, InlineKeyboardMarkup) for one page, in either mode."""
    page = results[offset:offset + RESULTS_PER_PAGE]
    header = RESULT_HEADER_TXT.format(query=query, total=len(results))
    nav = _nav_row(key, offset, len(results))

    if mode == "text":
        lines = []
        for doc in page:
            label = doc["file_name"]
            url = f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
            lines.append(f'📁 <a href="{url}">{label}</a> • {human_size(doc.get("file_size", 0))}')
        text = header + "\n\n" + "\n".join(lines)
        markup = InlineKeyboardMarkup([nav]) if nav else None
        return text, markup

    rows = []
    for doc in page:
        label = f"{doc['file_name']} • {human_size(doc.get('file_size', 0))}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([InlineKeyboardButton(
            label, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
        )])
    if nav:
        rows.append(nav)
    return header, InlineKeyboardMarkup(rows)


def _search_filter(_, __, message):
    if not message.text or message.text.startswith("/"):
        return False
    if message.chat.type == enums.ChatType.PRIVATE:
        return ENABLE_PM_SEARCH
    return message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)


search_filter = filters.create(_search_filter)


@Client.on_message(search_filter)
async def handle_search(_, message):
    query = message.text.strip()
    if len(query) < MIN_QUERY_LEN:
        return

    results = await search_files(query)
    if not results:
        await message.reply_text(NOT_FOUND_TXT.format(query=query), quote=True)
        return

    settings = await get_settings()
    mode = settings["result_mode"]
    key = _cache_key(query)
    _cache_put(key, query, results, mode)

    text, markup = _render_page(key, query, results, mode, offset=0)
    await message.reply_text(
        text,
        reply_markup=markup,
        quote=True,
        disable_web_page_preview=True,
    )


@Client.on_callback_query(filters.regex(r"^pg#"))
async def paginate(_, query):
    _, key, offset = query.data.split("#")
    offset = int(offset)
    entry = _cache_get(key)
    if not entry:
        await query.answer()
        await query.message.edit_text(SEARCH_EXPIRED_TXT)
        return
    text, markup = _render_page(key, entry["query"], entry["results"], entry["mode"], offset)
    await query.answer()
    await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
