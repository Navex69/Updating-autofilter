import hashlib
import time

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ENABLE_PM_SEARCH, RESULTS_PER_PAGE, MIN_QUERY_LEN
from database.filters_db import search_files
from utils import temp, human_size
from strings import NOT_FOUND_TXT, RESULT_HEADER_TXT, SEARCH_EXPIRED_TXT

# ══════════════════════════════════════════════════════════════════════════════
# Per-query result cache — one DB round trip per search, pagination is free.
# ══════════════════════════════════════════════════════════════════════════════

_CACHE: dict = {}
_CACHE_TTL = 600  # 10 minutes
_CACHE_MAX_ENTRIES = 300


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


def _cache_put(key: str, query: str, results: list):
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["time"])
        _CACHE.pop(oldest, None)
    _CACHE[key] = {"query": query, "results": results, "time": time.time()}


def _cache_get(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["time"] > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return entry


def _build_page(key: str, query: str, results: list, offset: int) -> InlineKeyboardMarkup:
    page = results[offset:offset + RESULTS_PER_PAGE]
    rows = []
    for doc in page:
        label = f"{doc['file_name']} • {human_size(doc.get('file_size', 0))}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([InlineKeyboardButton(
            label, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
        )])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg#{key}#{max(0, offset - RESULTS_PER_PAGE)}"))
    if offset + RESULTS_PER_PAGE < len(results):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg#{key}#{offset + RESULTS_PER_PAGE}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


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

    key = _cache_key(query)
    _cache_put(key, query, results)
    markup = _build_page(key, query, results, offset=0)
    await message.reply_text(
        RESULT_HEADER_TXT.format(query=query, total=len(results)),
        reply_markup=markup,
        quote=True,
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
    markup = _build_page(key, entry["query"], entry["results"], offset)
    await query.answer()
    await query.message.edit_reply_markup(markup)
