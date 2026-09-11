import logging
import re
import time
from pymongo import TEXT, ASCENDING
from pymongo.errors import DuplicateKeyError
from config import COLLECTION_NAME
from database.client import db

logger = logging.getLogger(__name__)

files = db[COLLECTION_NAME]

_RESULT_FIELDS = {
    "file_name": 1, "caption": 1, "file_size": 1, "file_id": 1, "file_unique_id": 1,
}


async def ensure_indexes():
    """Call once at startup. Safe to call repeatedly — Mongo no-ops if present."""
    await files.create_index(
        [("file_name", TEXT), ("caption", TEXT)],
        weights={"file_name": 10, "caption": 3},
        name="search_text_idx",
    )
    await files.create_index([("file_unique_id", ASCENDING)], unique=True, name="uniq_file_idx")
    await files.create_index([("channel_id", ASCENDING)], name="channel_idx")
    logger.info("Database indexes ready.")


def display_name(doc: dict) -> str:
    """Captions carry the readable title/quality/language info admins write
    by hand; prefer that everywhere a file is shown, falling back to the
    raw filename only when there's no caption."""
    return (doc.get("caption") or "").strip() or doc.get("file_name", "Unnamed file")


# ══════════════════════════════════════════════════════════════════════════════
# INDEXING (auto + manual share this single entrypoint)
# ══════════════════════════════════════════════════════════════════════════════

async def save_file(media, channel_id: int | None = None) -> str:
    """
    Save one media item. `media` is a pyrogram Document/Video object with
    `.caption` and `.file_type` attached by the caller.

    Returns one of: 'saved', 'updated', 'duplicate', 'skipped', 'error'
    """
    file_name = getattr(media, "file_name", None)
    if not file_name:
        # No filename means nothing useful to search on — skip it.
        return "skipped"

    file_unique_id = getattr(media, "file_unique_id", None)
    if not file_unique_id:
        return "skipped"

    doc = {
        "file_id": media.file_id,
        "file_unique_id": file_unique_id,
        "file_name": file_name,
        "file_size": getattr(media, "file_size", 0) or 0,
        "caption": getattr(media, "caption", "") or "",
        "file_type": getattr(media, "file_type", "document"),
        "mime_type": getattr(media, "mime_type", "") or "",
        "channel_id": channel_id,
        "indexed_at": time.time(),
    }

    try:
        await files.insert_one(doc)
        return "saved"
    except DuplicateKeyError:
        # Same file re-posted or re-indexed. If the caption changed, keep it
        # fresh — captions are edited more often than files are re-uploaded.
        existing = await files.find_one({"file_unique_id": file_unique_id}, {"caption": 1})
        if existing is not None and existing.get("caption", "") != doc["caption"]:
            await files.update_one(
                {"file_unique_id": file_unique_id},
                {"$set": {"caption": doc["caption"], "file_name": file_name}},
            )
            return "updated"
        return "duplicate"
    except Exception:
        logger.exception("save_file failed for %s", file_name)
        return "error"


async def total_files() -> int:
    return await files.estimated_document_count()


async def count_by_channel(channel_id: int) -> int:
    return await files.count_documents({"channel_id": channel_id})


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

_MAX_FETCH = 200  # hard ceiling per query, keeps a broad/bad query cheap


async def search_files(query: str) -> list:
    """
    Return up to _MAX_FETCH matching documents, best match first.
    Callers paginate this list in memory — one DB round trip per query,
    not one per page.
    """
    query = query.strip()
    if not query:
        return []

    cursor = (
        files.find(
            {"$text": {"$search": query}},
            {**_RESULT_FIELDS, "score": {"$meta": "textScore"}},
        )
        .sort([("score", {"$meta": "textScore"})])
        .limit(_MAX_FETCH)
    )
    results = await cursor.to_list(length=_MAX_FETCH)
    if results:
        return results

    # Fallback: the text index found nothing (common on partial words —
    # "aveng" won't match "avengers" via $text). Try a bounded regex scan.
    import re
    try:
        pattern = re.compile(re.escape(query).replace(r"\ ", r".*"), re.IGNORECASE)
    except re.error:
        return []
    cursor = files.find({"file_name": pattern}, _RESULT_FIELDS).limit(_MAX_FETCH)
    return await cursor.to_list(length=_MAX_FETCH)


async def get_file_by_id(object_id: str) -> dict | None:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        oid = ObjectId(object_id)
    except InvalidId:
        return None
    return await files.find_one({"_id": oid})


# ══════════════════════════════════════════════════════════════════════════════
# RESULT FILTERING — season / language / year / quality / episode
# Extracted straight from file_name + caption text, once per search (meta),
# then re-applied in memory on every filter tap. No extra DB round trips.
# ══════════════════════════════════════════════════════════════════════════════

_SE_PATTERN = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.IGNORECASE)
_SEASON_PATTERN = re.compile(r"\bS(\d{1,2})\b|\bSeason\s*(\d{1,2})\b", re.IGNORECASE)
_EPISODE_PATTERN = re.compile(r"\bE(?:p(?:isode)?)?\s*(\d{1,3})\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")

_LANGUAGES = [
    "hindi", "english", "tamil", "telugu", "kannada", "malayalam",
    "bengali", "punjabi", "marathi", "gujarati", "urdu", "korean",
    "japanese", "dual audio", "multi audio",
]
_LANG_PATTERNS = {lang: re.compile(rf"\b{re.escape(lang)}\b", re.IGNORECASE) for lang in _LANGUAGES}

# (regex, code, label) — first match wins, ordered best quality first
_QUALITY_RULES = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.IGNORECASE), "2160p", "4K / 2160p"),
    (re.compile(r"\b1080p\b", re.IGNORECASE), "1080p", "1080p"),
    (re.compile(r"\b720p\b", re.IGNORECASE), "720p", "720p"),
    (re.compile(r"\b480p\b", re.IGNORECASE), "480p", "480p"),
    (re.compile(r"\b360p\b", re.IGNORECASE), "360p", "360p"),
    (re.compile(r"\bblu-?ray|bdrip\b", re.IGNORECASE), "bluray", "BluRay"),
    (re.compile(r"\bweb-?dl\b", re.IGNORECASE), "webdl", "WEB-DL"),
    (re.compile(r"\bwebrip\b", re.IGNORECASE), "webrip", "WEBRip"),
    (re.compile(r"\bhdrip\b", re.IGNORECASE), "hdrip", "HDRip"),
    (re.compile(r"\bhdtv\b", re.IGNORECASE), "hdtv", "HDTV"),
    (re.compile(r"\bdvdrip\b", re.IGNORECASE), "dvdrip", "DVDRip"),
    (re.compile(r"\b(cam|hdts|ts)\b", re.IGNORECASE), "cam", "CAM/TS"),
]


def _doc_text(doc: dict) -> str:
    return f"{doc.get('file_name', '')} {doc.get('caption', '') or ''}"


def _season_of(text: str) -> int:
    m = _SE_PATTERN.search(text)
    if m:
        return int(m.group(1))
    m = _SEASON_PATTERN.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    return 0


def _episode_of(text: str) -> int:
    m = _SE_PATTERN.search(text)
    if m:
        return int(m.group(2))
    m = _EPISODE_PATTERN.search(text)
    if m:
        return int(m.group(1))
    return 0


def _year_of(text: str) -> str:
    m = _YEAR_PATTERN.search(text)
    return m.group(0) if m else ""


def _languages_of(text: str) -> list:
    return [lang for lang, pat in _LANG_PATTERNS.items() if pat.search(text)]


def _quality_of(text: str) -> tuple:
    for pat, code, label in _QUALITY_RULES:
        if pat.search(text):
            return code, label
    return "", ""


def extract_meta(results: list) -> dict:
    """One pass over the full (unfiltered) result set. Returns the distinct
    filter values available, so filter buttons never offer an empty choice."""
    seasons, episodes, years = set(), set(), set()
    languages = set()
    qualities: dict = {}  # code -> label

    for doc in results:
        text = _doc_text(doc)
        season = _season_of(text)
        if season:
            seasons.add(season)
        episode = _episode_of(text)
        if episode:
            episodes.add(episode)
        year = _year_of(text)
        if year:
            years.add(year)
        languages.update(_languages_of(text))
        code, label = _quality_of(text)
        if code:
            qualities[code] = label

    quality_order = [code for _pat, code, _label in _QUALITY_RULES]
    ordered_qualities = [(c, qualities[c]) for c in quality_order if c in qualities]

    return {
        "seasons": sorted(seasons),
        "episodes": sorted(episodes),
        "years": sorted(years, reverse=True),
        "languages": sorted(languages),
        "qualities": ordered_qualities,
    }


def apply_filters(results: list, filters: dict) -> list:
    """AND-combine every active filter over the full result set."""
    if not filters:
        return results

    season = filters.get("season")
    episode = filters.get("episode")
    year = filters.get("year")
    quality = filters.get("quality")
    language = filters.get("language")

    out = []
    for doc in results:
        text = _doc_text(doc)
        if season and _season_of(text) != int(season):
            continue
        if episode and _episode_of(text) != int(episode):
            continue
        if year and _year_of(text) != str(year):
            continue
        if quality and _quality_of(text)[0] != quality:
            continue
        if language and not _LANG_PATTERNS[language].search(text):
            continue
        out.append(doc)
    return out
