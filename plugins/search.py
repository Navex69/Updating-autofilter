import logging
import re
import time
from pymongo import ASCENDING
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
    # `words` backs the primary search path (see search_files below) — a
    # plain multikey index Mongo builds automatically for an array field,
    # giving indexed, exact, stopword-free word lookups.
    await files.create_index([("words", ASCENDING)], name="words_idx")
    await files.create_index([("file_unique_id", ASCENDING)], unique=True, name="uniq_file_idx")
    await files.create_index([("channel_id", ASCENDING)], name="channel_idx")
    logger.info("Database indexes ready.")


def display_name(doc: dict) -> str:
    """Captions carry the readable title/quality/language info admins write
    by hand; prefer that everywhere a file is shown, falling back to the
    raw filename only when there's no caption."""
    return (doc.get("caption") or "").strip() or doc.get("file_name", "Unnamed file")


async def export_all_captions(path: str) -> int:
    """Write every indexed file's display caption to a local text file, one
    per line — lets an admin see exactly what's really in the database
    (real formatting, real spelling) before tuning search behaviour.
    Streams via cursor so it stays memory-safe no matter the collection
    size. Returns the number of lines written."""
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        cursor = files.find({}, {"file_name": 1, "caption": 1})
        async for doc in cursor:
            f.write(display_name(doc) + "\n")
            count += 1
    return count


# ══════════════════════════════════════════════════════════════════════════════
# WORD TOKENISING — shared by indexing (save_file) and searching (search_files)
# so the two sides always agree on what counts as "the same word". Splitting
# on any non-alphanumeric character makes it completely punctuation-agnostic
# (dots, underscores, brackets, hyphens all count as a separator) without
# ever altering the letters/numbers themselves — nothing is stemmed,
# corrected, or dropped the way MongoDB's own $text/English-stopword system
# would (that system was quietly discarding words like "and"/"from"
# entirely, which is what let "Vishwanath and Son" degrade into a
# bare "son" search).
# ══════════════════════════════════════════════════════════════════════════════

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list:
    return _WORD_RE.findall(text.lower())


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

    caption = getattr(media, "caption", "") or ""
    words = sorted(set(_tokenize(f"{file_name} {caption}")))

    doc = {
        "file_id": media.file_id,
        "file_unique_id": file_unique_id,
        "file_name": file_name,
        "file_size": getattr(media, "file_size", 0) or 0,
        "caption": caption,
        "words": words,
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
        if existing is not None and existing.get("caption", "") != caption:
            await files.update_one(
                {"file_unique_id": file_unique_id},
                {"$set": {"caption": caption, "file_name": file_name, "words": words}},
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


async def backfill_word_index(batch_size: int = 500) -> int:
    """
    One-time migration for files indexed before the `words` field existed.
    Safe to run repeatedly (idempotent) and safe to run while the bot is
    live — updates stream in small batches so it never holds a large chunk
    of the collection in memory or blocks the DB for long.
    Returns the number of documents updated.
    """
    from pymongo import UpdateOne

    updated = 0
    batch = []
    cursor = files.find(
        {"words": {"$exists": False}}, {"file_name": 1, "caption": 1},
    )
    async for doc in cursor:
        words = sorted(set(_tokenize(f"{doc.get('file_name', '')} {doc.get('caption', '') or ''}")))
        batch.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"words": words}}))
        if len(batch) >= batch_size:
            await files.bulk_write(batch, ordered=False)
            updated += len(batch)
            batch = []
    if batch:
        await files.bulk_write(batch, ordered=False)
        updated += len(batch)
    return updated


# ══════════════════════════════════════════════════════════════════════════════
# RESULT FILTERING — season / language / year / quality / episode
# Extracted straight from file_name + caption text. Used two ways:
#   1. Post-search, to power the Season/Language/Year/Quality/Episode
#      filter buttons on a result page.
#   2. Up front, by parse_query() below, to pull the same kind of tag out
#      of the user's own typed query so it narrows Stage 1 search results
#      instead of being searched as if it were part of the title.
# ══════════════════════════════════════════════════════════════════════════════

_SE_PATTERN = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.IGNORECASE)
_SEASON_PATTERN = re.compile(r"\bS(\d{1,2})\b|\bSeason\s*(\d{1,2})\b", re.IGNORECASE)
_EPISODE_PATTERN = re.compile(r"\bE(?:p(?:isode)?)?\s*(\d{1,3})\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")

# canonical language -> every spelling/abbreviation seen in real captions
# that should count as that language. Keep this evidence-based: only add an
# abbreviation here once you've actually seen it used, since a short
# abbreviation can coincide with an unrelated real word (e.g. "pun" is also
# an English word) — the full names carry no such risk.
_LANGUAGE_ALIASES = {
    "hindi": ["hindi", "hin"],
    "english": ["english", "eng"],
    "tamil": ["tamil", "tam", "taml"],
    "telugu": ["telugu", "tel"],
    "kannada": ["kannada"],
    "malayalam": ["malayalam"],
    "bengali": ["bengali"],
    "punjabi": ["punjabi"],
    "marathi": ["marathi"],
    "gujarati": ["gujarati"],
    "urdu": ["urdu"],
    "korean": ["korean"],
    "japanese": ["japanese"],
    "chinese": ["chinese"],
    "spanish": ["spanish"],
    "french": ["french"],
    "german": ["german"],
    "dual audio": ["dual audio", "dual"],
    "multi audio": ["multi audio", "multi"],
}
_LANG_ALIAS_PATTERNS = {
    canonical: re.compile(r"\b(?:" + "|".join(re.escape(a) for a in aliases) + r")\b", re.IGNORECASE)
    for canonical, aliases in _LANGUAGE_ALIASES.items()
}

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
_QUALITY_RANK = {code: len(_QUALITY_RULES) - i for i, (_pat, code, _label) in enumerate(_QUALITY_RULES)}


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
    return [canonical for canonical, pat in _LANG_ALIAS_PATTERNS.items() if pat.search(text)]


def _language_matches(text: str, canonical: str) -> bool:
    pat = _LANG_ALIAS_PATTERNS.get(canonical)
    return bool(pat and pat.search(text))


def _quality_of(text: str) -> tuple:
    for pat, code, label in _QUALITY_RULES:
        if pat.search(text):
            return code, label
    return "", ""


def _quality_rank(text: str) -> int:
    code, _label = _quality_of(text)
    return _QUALITY_RANK.get(code, 0)


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN TITLE — cuts everything from the first season/episode/year/quality/
# language/technical-junk marker onward, since in real captions those tags
# always come after the title, never before or in the middle. This is the
# single canonical way both a query and a database caption get reduced to
# "just the title" before Stage 1 compares them for an exact match.
# ══════════════════════════════════════════════════════════════════════════════

_JUNK_RE = re.compile(
    r"\b(s\d{1,2}e?\d{0,3}|season\s*\d+|ep(?:isode)?\.?\s*\d+|\d{3,4}p|4k|uhd|hdr\d*|"
    r"bluray|bdrip|remux|web-?dl|webrip|hdrip|dvdrip|hdtv|cam|hdts|ts|"
    r"hevc|x264|x265|avc|av1|aac|ddp\d?\.?\d?|dts|flac|mp3|ac3|"
    r"esubs?|subs?|subbed|dubbed|dual\s*audio|multi\s*audio|dual|multi|"
    + "|".join(re.escape(a) for aliases in _LANGUAGE_ALIASES.values() for a in aliases)
    + r")\b.*",
    re.IGNORECASE,
)
_TITLE_PUNCT_RE = re.compile(r"[._\-+\[\]()'\"~]+")
_TITLE_SPACE_RE = re.compile(r"\s{2,}")


def clean_title(text: str) -> str:
    t = _YEAR_PATTERN.sub(" ", text)
    t = _JUNK_RE.sub(" ", t)
    t = _TITLE_PUNCT_RE.sub(" ", t)
    return _TITLE_SPACE_RE.sub(" ", t).strip()


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
        if language and not _language_matches(text, language):
            continue
        out.append(doc)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

_MAX_FETCH = 200          # hard ceiling on results returned per query
_CANDIDATE_FETCH = 3000   # how many word-index matches to pull before the
                           # exact-title re-check narrows them down — the
                           # final check is a cheap string compare, so this
                           # can afford to be generous

# Tag-extraction patterns match ONLY at the very end of the (remaining)
# query, one tag at a time, never touching the start/middle — this is what
# keeps a title that legitimately contains a language/quality-sounding word
# ("Hindi Medium", "1917") intact, while still pulling out tags a user
# actually appended ("... 1080p Hindi"). "Season 1"/"Season1"/"episode 3"/
# "ep3" are all recognised regardless of spacing (\s* matches zero spaces).
_TRAILING_SE_RE = re.compile(
    r"^(?P<title>.*\S)\s+S(?P<season>\d{1,2})E(?P<episode>\d{1,3})\s*$", re.IGNORECASE,
)
_TRAILING_SEASON_RE = re.compile(
    r"^(?P<title>.*\S)\s+(?:S(?P<season1>\d{1,2})|Season\s*(?P<season2>\d{1,2}))\s*$", re.IGNORECASE,
)
_TRAILING_EPISODE_RE = re.compile(
    r"^(?P<title>.*\S)\s+(?:Episode|Ep)\.?\s*(?P<episode>\d{1,3})\s*$", re.IGNORECASE,
)
_TRAILING_YEAR_RE = re.compile(r"^(?P<title>.*\S)\s+(?P<year>19[5-9]\d|20[0-3]\d)\s*$")
_TRAILING_QUALITY_RE = re.compile(
    r"^(?P<title>.*\S)\s+(?P<quality>2160p|4k|uhd|1080p|720p|480p|360p|"
    r"blu-?ray|bdrip|web-?dl|webrip|hdrip|hdtv|dvdrip|cam|hdts|ts)\s*$",
    re.IGNORECASE,
)
_ALL_LANG_ALIASES = sorted(
    {alias for aliases in _LANGUAGE_ALIASES.values() for alias in aliases},
    key=len, reverse=True,
)
_TRAILING_LANG_RE = re.compile(
    r"^(?P<title>.*\S)\s+(?P<lang>" + "|".join(re.escape(a) for a in _ALL_LANG_ALIASES) + r")\s*$",
    re.IGNORECASE,
)

_TRAILING_PATTERNS = [
    ("se", _TRAILING_SE_RE),
    ("season", _TRAILING_SEASON_RE),
    ("episode", _TRAILING_EPISODE_RE),
    ("year", _TRAILING_YEAR_RE),
    ("quality", _TRAILING_QUALITY_RE),
    ("language", _TRAILING_LANG_RE),
]


def parse_query(query: str) -> tuple:
    """
    Split a raw user query into (title, tags) by repeatedly peeling a
    recognised tag off the END of the query only. Returns the untouched
    title (same words, same order, same spelling the user typed) plus
    whichever of season/episode/year/quality/language were found trailing
    it. Never strips a query down to nothing — a query that IS just "1917"
    stays a title, not a year.
    """
    title = query.strip()
    tags = {"season": None, "episode": None, "year": None, "quality": None, "language": None}
    if not title:
        return title, tags

    progress = True
    while progress:
        progress = False
        for kind, pattern in _TRAILING_PATTERNS:
            m = pattern.match(title)
            if not m:
                continue
            candidate = m.group("title").strip()
            if not candidate:
                continue  # would empty the title out — refuse and try nothing else this round

            if kind == "se":
                tags["season"] = tags["season"] if tags["season"] is not None else int(m.group("season"))
                tags["episode"] = tags["episode"] if tags["episode"] is not None else int(m.group("episode"))
            elif kind == "season":
                num = m.group("season1") or m.group("season2")
                tags["season"] = tags["season"] if tags["season"] is not None else int(num)
            elif kind == "episode":
                tags["episode"] = tags["episode"] if tags["episode"] is not None else int(m.group("episode"))
            elif kind == "year":
                tags["year"] = tags["year"] or m.group("year")
            elif kind == "quality":
                code, _label = _quality_of(m.group("quality"))
                tags["quality"] = tags["quality"] or code
            elif kind == "language":
                langs = _languages_of(m.group("lang"))
                if langs and not tags["language"]:
                    tags["language"] = langs[0]

            title = candidate
            progress = True
            break  # restart the pattern list against the now-shorter title

    return title, tags


async def search_files(query: str) -> list:
    """
    Stage 1 search. Deliberately simple and strict:
      1. Pull any trailing season/episode/year/quality/language tag off the
         query (never touching the title itself).
      2. Reduce the remaining title to its clean form (same cut used on
         database captions) and require an EXACT match — not "contains
         these words", a full match — so a short query like "You" only
         ever matches a file whose title genuinely IS "You", never "You
         Love Me"; and "Weak Hero Class 2" never matches a file that's
         actually "Weak Hero Class 1". A word-index lookup narrows the
         candidates first purely for speed; the exact-title check is what
         actually decides the result, so the narrowing step can never let
         a wrong file through on its own.
      3. Hard-filter by whatever tags were pulled out in step 1.
    Callers paginate the returned list in memory — one DB round trip per
    query, not one per page.
    """
    query = query.strip()
    if not query:
        return []

    title, tags = parse_query(query)
    query_key = clean_title(title).lower()
    if not query_key:
        return []

    words = _tokenize(query_key)
    if not words:
        return []

    cursor = files.find({"words": {"$all": words}}, _RESULT_FIELDS).limit(_CANDIDATE_FETCH)
    candidates = await cursor.to_list(length=_CANDIDATE_FETCH)

    results = [doc for doc in candidates if clean_title(display_name(doc)).lower() == query_key]

    active_tags = {k: v for k, v in tags.items() if v is not None}
    if active_tags:
        results = apply_filters(results, active_tags)

    results.sort(key=lambda d: -_quality_rank(_doc_text(d)))
    return results[:_MAX_FETCH]


async def get_file_by_id(object_id: str) -> dict | None:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        oid = ObjectId(object_id)
    except InvalidId:
        return None
    return await files.find_one({"_id": oid})
