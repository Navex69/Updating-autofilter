import asyncio
import hashlib
import html
import math
import re
import time

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ENABLE_PM_SEARCH, RESULTS_PER_PAGE
from database.filters_db import search_files, display_name, extract_meta, apply_filters
from database.settings_db import get_settings
from poster import fetch_poster
from spellcheck import fuzzy_correct, ai_correct
from utils import temp, human_size
from strings import (
    NOT_FOUND_TXT, RESULT_HEADER_TXT, RESULT_HEADER_CORRECTED_TXT, POSTER_CAPTION_TXT,
    SEARCH_EXPIRED_TXT, QUERY_AUTODELETE_NOTE, FILTER_LABELS, FILTER_MENU_TXT,
    FILTER_CLEAR_BTN, HOME_BTN, NO_MATCH_TXT,
    STATUS_STAGE1_TXT, STATUS_STAGE2_TXT, STATUS_STAGE3_TXT,
)

TEXT_LIMIT = 4096  # Telegram's hard cap for a plain message — defensive only,
                    # real per-file entries are a few hundred chars at most so
                    # this is never expected to trigger at the default page size.

# ── caption sanitising ──────────────────────────────────────────────────────
# Some source channels bake literal HTML tags straight into the caption
# (e.g. "<b>Movie Name ...</b>") expecting Telegram to render them. That
# breaks the moment we wrap the same text inside our own <a href="...">
# link — the leftover/duplicate tags either show up as visible "<b>" text or
# make the whole entity malformed so Telegram drops the link entirely.
# Strip any tag-shaped substring first, then escape whatever plain text is
# left (handles stray "&", "<", ">" that are just part of the file name).
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")


def _strip_tags(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _safe_caption(text: str) -> str:
    return html.escape(_strip_tags(text))


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


def _cache_put(key: str, query: str, results: list, mode: str, poster, original_query: str | None = None) -> dict:
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["time"])
        _CACHE.pop(oldest, None)
    entry = {
        "query": query,
        "original_query": original_query,  # set only when a typo-correction was used
        "results": results,
        "mode": mode,
        "meta": extract_meta(results),
        "poster": poster,
        "filters": dict(_EMPTY_FILTERS),
        "time": time.time(),
    }
    _CACHE[key] = entry
    return entry


def _fit_text(text: str, limit: int = TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _chunk(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ══════════════════════════════════════════════════════════════════════════════
# Rendering — always a plain text message (4096-char budget). The poster, when
# found, is sent once as its own separate photo with a short static caption
# and is never touched again, so it can never run into a caption-length
# problem no matter how long the file list gets.
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
    header = (
        RESULT_HEADER_CORRECTED_TXT.format(
            query=html.escape(entry["query"]),
            original=html.escape(entry["original_query"]),
            total=total,
        )
        if entry.get("original_query")
        else RESULT_HEADER_TXT.format(query=html.escape(entry["query"]), total=total)
    )

    rows = _filter_rows(key, entry["meta"], entry["filters"], offset)

    if total == 0:
        if filters_active:
            rows.append([InlineKeyboardButton(HOME_BTN, callback_data=f"home#{key}")])
        return header + "\n\n" + NO_MATCH_TXT, InlineKeyboardMarkup(rows)

    if entry["mode"] == "text":
        lines = []
        for doc in page:
            label = _safe_caption(display_name(doc))
            url = f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
            lines.append(f'📁 <a href="{url}">{label}</a> • {human_size(doc.get("file_size", 0))}')
        text = header + "\n\n" + "\n\n".join(lines)
    else:
        text = header
        for doc in page:
            label = f"{_strip_tags(display_name(doc))} • {human_size(doc.get('file_size', 0))}"
            if len(label) > 60:
                label = label[:57] + "…"
            rows.append([InlineKeyboardButton(
                label, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
            )])

    if filters_active:
        rows.append([InlineKeyboardButton(HOME_BTN, callback_data=f"home#{key}")])
    rows.append(_nav_row(key, offset, total))

    return _fit_text(text), InlineKeyboardMarkup(rows)


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


async def _push(message, text: str, markup):
    """The results message is always plain text — editing is always edit_text.
    The poster (when present) is a separate, static message and never carries
    the keyboard, so a callback can never originate from it."""
    await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _expired(message):
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


async def _status_update(message, text: str):
    try:
        return await message.edit_text(text, disable_web_page_preview=True)
    except RPCError:
        return message


@Client.on_message(search_filter)
async def handle_search(_, message):
    query = message.text.strip()
    if not query:
        return

    # The "Searching..." status message is sent concurrently with the real
    # Stage 1 work below, not before it — so showing search progress never
    # adds latency to the common case where Stage 1 already finds it.
    status_task = asyncio.create_task(
        message.reply_text(STATUS_STAGE1_TXT.format(query=html.escape(query)), quote=True)
    )
    results, poster = await asyncio.gather(
        search_files(query),
        fetch_poster(query),
    )
    status = await status_task

    resolved_query = query
    original_query = None

    if not results:
        # Stage 2 — fuzzy match against your own DB's titles. Free,
        # in-memory, a few milliseconds. Catches ordinary typos without
        # ever cross-matching a different-but-similar-looking title (word
        # count must match and every word must score high individually).
        status = await _status_update(status, STATUS_STAGE2_TXT)

        hit = await fuzzy_correct(query)
        if hit:
            resolved_query, results = hit
            original_query = query
        else:
            status = await _status_update(status, STATUS_STAGE3_TXT)

            # Stage 3 — Groq + Gemini race, each guess re-verified against
            # the real database before it's trusted. Only reached when
            # Stage 1 AND Stage 2 both found nothing.
            hit = await ai_correct(query)
            if hit:
                resolved_query, results = hit
                original_query = query

        if results and not poster:
            # The original query's poster lookup was based on a misspelled
            # title and likely came back empty — retry with the corrected
            # one now that we actually know it.
            poster = await fetch_poster(resolved_query)

    if not results:
        await _status_update(status, NOT_FOUND_TXT.format(query=html.escape(query)))
        return

    settings = await get_settings()
    mode = settings["result_mode"]
    key = _cache_key(resolved_query)
    entry = _cache_put(key, resolved_query, results, mode, poster, original_query)

    text, markup = _render(key, entry, offset=0)
    if settings["query_autodelete_enabled"]:
        text += QUERY_AUTODELETE_NOTE.format(seconds=settings["query_autodelete_seconds"])

    to_delete = []

    # The poster is a short, static, always-safe caption — it never carries
    # the growing file list, so it can never hit Telegram's 1024-char photo
    # caption limit no matter how long/many the results are.
    if poster:
        poster_msg = await message.reply_photo(
            poster["url"],
            caption=POSTER_CAPTION_TXT.format(query=html.escape(resolved_query)),
            quote=True,
        )
        to_delete.append(poster_msg)
        sent = await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        sent = await message.reply_text(
            text, reply_markup=markup, quote=True, disable_web_page_preview=True,
        )
    to_delete.append(sent)

    # The stage-progress message has done its job now that the real result
    # message(s) are in the chat — clear it without making the user wait.
    asyncio.create_task(_schedule_delete(status, 0))

    if settings["query_autodelete_enabled"]:
        for m in to_delete:
            asyncio.create_task(_schedule_delete(m, settings["query_autodelete_seconds"]))


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
    await _push(query.message, text, markup)


@Client.on_callback_query(filters.regex(r"^fm#"))
async def open_filter_menu(_, query):
    _, key, ftype, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    text, markup = _filter_menu(key, ftype, entry, int(offset))
    await _push(query.message, text, markup)


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
    await _push(query.message, text, markup)


@Client.on_callback_query(filters.regex(r"^rr#"))
async def return_to_results(_, query):
    _, key, offset = query.data.split("#")
    entry = _cache_get(key)
    await query.answer()
    if not entry:
        await _expired(query.message)
        return
    text, markup = _render(key, entry, int(offset))
    await _push(query.message, text, markup)


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
    await _push(query.message, text, markup)
