import asyncio
import hashlib
import math
import time

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ENABLE_PM_SEARCH, RESULTS_PER_PAGE, MIN_QUERY_LEN
from database.filters_db import search_files, display_name, extract_meta, apply_filters
from database.settings_db import get_settings
from poster import fetch_poster
from utils import temp, human_size
from strings import (
    NOT_FOUND_TXT, RESULT_HEADER_TXT, SEARCH_EXPIRED_TXT, QUERY_AUTODELETE_NOTE,
    FILTER_LABELS, FILTER_MENU_TXT, FILTER_CLEAR_BTN, HOME_BTN, NO_MATCH_TXT,
)

CAPTION_LIMIT = 1024  # Telegram's hard cap for a photo caption

# ── small-caps file-name styling ────────────────────────────────────────────
# Header stays native <b>bold</b> (always renders, zero risk). File names get
# a small-caps look for a distinctive result page. This is a single O(n)
# str.translate() pass over already-fetched short strings — no regex, no
# extra DB/network work, so it has no effect on search/response speed.
_SMALLCAPS_TABLE = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
)


def _smallcaps(text: str) -> str:
    return text.lower().translate(_SMALLCAPS_TABLE)

# ══════════════════════════════════════════════════════════════════════════════
# Per-query result cache — one DB round trip (+ one poster lookup) per search,
# pagination and filtering are both free after that. The display mode
# (button/text) is snapshotted at search time, so an admin toggling it
# mid-way doesn't mix formats within the same result set. Filter state lives
# here too, shared by anyone tapping the same result message.
# ══════════════════════════════════════════════════════════════════════════════

_CACHE: dict = {}
_CACHE_TTL = 600  # 10 minutes
_CACHE_MAX_ENTRIES = 300

_EMPTY_FILTERS = {"season": None, "language": None, "year": None, "quality": None, "episode": None}


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


def _cache_get(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["time"] > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return entry


def _cache_put(key: str, query: str, results: list, mode: str, poster) -> dict:
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["time"])
        _CACHE.pop(oldest, None)
    entry = {
        "query": query,
        "results": results,
        "mode": mode,
        "meta": extract_meta(results),
        "poster": poster,
        "filters": dict(_EMPTY_FILTERS),
        "time": time.time(),
    }
    _CACHE[key] = entry
    return entry


def _fit_caption(text: str, limit: int = CAPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _chunk(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ══════════════════════════════════════════════════════════════════════════════
# Rendering
# ══════════════════════════════════════════════════════════════════════════════

def _filter_rows(key: str, meta: dict, filters: dict, offset: int) -> list:
    rows = []

    row1 = []
    if meta["seasons"]:
        label = f"📅 S{int(filters['season']):02d}" if filters["season"] else FILTER_LABELS["season"]
        row1.append(InlineKeyboardButton(label, callback_data=f"fm#{key}#season#{offset}"))
    if meta["languages"]:
        label = f"🌐 {filters['language'].title()}" if filters["language"] else FILTER_LABELS["language"]
        row1.append(InlineKeyboardButton(label, callback_data=f"fm#{key}#language#{offset}"))
    if meta["episodes"]:
        label = f"▶️ E{filters['episode']}" if filters["episode"] else FILTER_LABELS["episode"]
        row1.append(InlineKeyboardButton(label, callback_data=f"fm#{key}#episode#{offset}"))
    if row1:
        rows.append(row1)

    row2 = []
    if meta["years"]:
        label = f"📆 {filters['year']}" if filters["year"] else FILTER_LABELS["year"]
        row2.append(InlineKeyboardButton(label, callback_data=f"fm#{key}#year#{offset}"))
    if meta["qualities"]:
        quality_label = dict(meta["qualities"]).get(filters["quality"], "")
        label = f"🎞 {quality_label}" if filters["quality"] else FILTER_LABELS["quality"]
        row2.append(InlineKeyboardButton(label, callback_data=f"fm#{key}#quality#{offset}"))
    if row2:
        rows.append(row2)

    return rows


def _nav_row(key: str, offset: int, total: int) -> list:
    total_pages = max(1, math.ceil(total / RESULTS_PER_PAGE))
    current_page = offset // RESULTS_PER_PAGE + 1
    nav = [InlineKeyboardButton("⬅️", callback_data=f"pg#{key}#{max(0, offset - RESULTS_PER_PAGE)}")] \
        if offset > 0 else []
    nav.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))
    if offset + RESULTS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pg#{key}#{offset + RESULTS_PER_PAGE}"))
    return nav


def _render(key: str, entry: dict, offset: int):
    """Return (text, InlineKeyboardMarkup) for one page, filters applied."""
    filters_active = any(entry["filters"].values())
    filtered = apply_filters(entry["results"], entry["filters"]) if filters_active else entry["results"]
    total = len(filtered)
    if total == 0:
        offset = 0
    elif offset >= total:
        offset = ((total - 1) // RESULTS_PER_PAGE) * RESULTS_PER_PAGE
    page = filtered[offset:offset + RESULTS_PER_PAGE]
    header = RESULT_HEADER_TXT.format(query=entry["query"], total=total)

    rows = _filter_rows(key, entry["meta"], entry["filters"], offset)

    if total == 0:
        if filters_active:
            rows.append([InlineKeyboardButton(HOME_BTN, callback_data=f"home#{key}")])
        return header + "\n\n" + NO_MATCH_TXT, InlineKeyboardMarkup(rows)

    if entry["mode"] == "text":
        lines = []
        for doc in page:
            label = _smallcaps(display_name(doc))
            url = f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
            lines.append(f'📁 <a href="{url}">{label}</a> • {human_size(doc.get("file_size", 0))}')
        text = header + "\n\n" + "\n\n".join(lines)
    else:
        text = header
        for doc in page:
            label = f"{_smallcaps(display_name(doc))} • {human_size(doc.get('file_size', 0))}"
            if len(label) > 60:
                label = label[:57] + "…"
            rows.append([InlineKeyboardButton(
                label, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
            )])

    if filters_active:
        rows.append([InlineKeyboardButton(HOME_BTN, callback_data=f"home#{key}")])
    rows.append(_nav_row(key, offset, total))

    return text, InlineKeyboardMarkup(rows)


def _filter_menu(key: str, ftype: str, entry: dict, offset: int):
    meta_field = {"season": "seasons", "language": "languages", "year": "years",
                  "quality": "qualities", "episode": "episodes"}[ftype]
    values = entry["meta"][meta_field]

    buttons = []
    for v in values:
        if ftype == "quality":
            code, label = v
            buttons.append(InlineKeyboardButton(label, callback_data=f"fv#{key}#quality#{code}#{offset}"))
        elif ftype == "season":
            buttons.append(InlineKeyboardButton(f"S{v:02d}", callback_data=f"fv#{key}#season#{v}#{offset}"))
        elif ftype == "episode":
            buttons.append(InlineKeyboardButton(f"E{v}", callback_data=f"fv#{key}#episode#{v}#{offset}"))
        elif ftype == "language":
            buttons.append(InlineKeyboardButton(v.title(), callback_data=f"fv#{key}#language#{v}#{offset}"))
        else:
            buttons.append(InlineKeyboardButton(str(v), callback_data=f"fv#{key}#year#{v}#{offset}"))

    rows = _chunk(buttons, 4)
    rows.insert(0, [InlineKeyboardButton(FILTER_CLEAR_BTN, callback_data=f"fv#{key}#{ftype}#_any_#{offset}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"rr#{key}#{offset}")])

    text = FILTER_MENU_TXT.format(label=FILTER_LABELS[ftype].split(" ", 1)[1])
    return text, InlineKeyboardMarkup(rows)


async def _push(message, text: str, markup, entry: dict):
    """Edit an existing result message, matching its original send type."""
    if entry["poster"]:
        await message.edit_caption(_fit_caption(text), reply_markup=markup)
    else:
        await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _expired(message):
    if message.photo:
        await message.edit_caption(SEARCH_EXPIRED_TXT)
    else:
        await message.edit_text(SEARCH_EXPIRED_TXT)


# ══════════════════════════════════════════════════════════════════════════════
# Message handler
# ══════════════════════════════════════════════════════════════════════════════

def _search_filter(_, __, message):
    if not message.text or message.text.startswith("/"):
        return False
    if message.chat.type == enums.ChatType.PRIVATE:
        return ENABLE_PM_SEARCH
    return message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)


search_filter = filters.create(_search_filter)


async def _schedule_delete(message, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except RPCError:
        pass


@Client.on_message(search_filter)
async def handle_search(_, message):
    query = message.text.strip()
    if len(query) < MIN_QUERY_LEN:
        return

    # DB search and poster lookup run concurrently — the network round trip
    # for the poster never adds latency on top of the DB query.
    results, poster = await asyncio.gather(
        search_files(query),
        fetch_poster(query),
    )
    if not results:
        await message.reply_text(NOT_FOUND_TXT.format(query=query), quote=True)
        return

    settings = await get_settings()
    mode = settings["result_mode"]
    key = _cache_key(query)
    entry = _cache_put(key, query, results, mode, poster)

    text, markup = _render(key, entry, offset=0)
    if settings["query_autodelete_enabled"]:
        text += QUERY_AUTODELETE_NOTE.format(seconds=settings["query_autodelete_seconds"])

    if poster:
        sent = await message.reply_photo(
            poster["url"],
            caption=_fit_caption(text),
            reply_markup=markup,
            quote=True,
        )
    else:
        sent = await message.reply_text(
            text,
            reply_markup=markup,
            quote=True,
            disable_web_page_preview=True,
        )

    if settings["query_autodelete_enabled"]:
        asyncio.create_task(_schedule_delete(sent, settings["query_autodelete_seconds"]))


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — pagination, filter menus, filter selection, home reset
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^noop$"))
async def noop(_, query):
    await query.answer()


@Client.on_callback_query(filters.regex(r"^pg#"))
async def paginate(_, query):
    _, key, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    text, markup = _render(key, entry, int(offset))
    await _push(query.message, text, markup, entry)


@Client.on_callback_query(filters.regex(r"^fm#"))
async def open_filter_menu(_, query):
    _, key, ftype, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    text, markup = _filter_menu(key, ftype, entry, int(offset))
    await _push(query.message, text, markup, entry)


@Client.on_callback_query(filters.regex(r"^fv#"))
async def set_filter_value(_, query):
    _, key, ftype, value, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    entry["filters"][ftype] = None if value == "_any_" else value
    text, markup = _render(key, entry, offset=0)
    await _push(query.message, text, markup, entry)


@Client.on_callback_query(filters.regex(r"^rr#"))
async def return_to_results(_, query):
    _, key, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    text, markup = _render(key, entry, int(offset))
    await _push(query.message, text, markup, entry)


@Client.on_callback_query(filters.regex(r"^home#"))
async def reset_home(_, query):
    _, key = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    entry["filters"] = dict(_EMPTY_FILTERS)
    text, markup = _render(key, entry, offset=0)
    await _push(query.message, text, markup, entry)
