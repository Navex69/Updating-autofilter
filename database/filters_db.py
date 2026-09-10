import logging
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
