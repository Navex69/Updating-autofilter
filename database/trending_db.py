"""
Trending searches. One small document per distinct title:
  {_id: md5(title)[:16], name: <correct title as spelled in the database>,
   count: <times searched>, last_searched: <epoch>}

Only searches that found results straight away (Stage 1) or via the fuzzy
spelling match (Stage 2) are recorded, always under the title as it is spelled
in the database — never the user's own (possibly misspelled) text.
"""
import hashlib
import logging
import time

from pymongo import DESCENDING

from database.client import db
from database.filters_db import clean_title, display_name

logger = logging.getLogger(__name__)

trending_col = db["trending_queries"]
_index_ready = False


def _key(name: str) -> str:
    return hashlib.md5(name.strip().lower().encode()).hexdigest()[:16]


def title_of(results: list, fallback: str) -> str:
    """Correctly-spelled title of a result set (taken from the database itself)."""
    if results:
        title = clean_title(display_name(results[0]))
        if title:
            return title
    return clean_title(fallback) or fallback.strip()


async def _ensure_index():
    global _index_ready
    if not _index_ready:
        await trending_col.create_index([("count", DESCENDING), ("last_searched", DESCENDING)], name="trending_idx")
        _index_ready = True


async def record_search(name: str):
    """Count one successful search. Never raises — stats must not break search."""
    name = (name or "").strip()
    if not name:
        return
    try:
        await _ensure_index()
        await trending_col.update_one(
            {"_id": _key(name)},
            {"$inc": {"count": 1},
             "$set": {"last_searched": time.time()},
             "$setOnInsert": {"name": name}},
            upsert=True,
        )
    except Exception:
        logger.debug("record_search failed for %r", name, exc_info=True)


async def get_page(page: int, size: int) -> tuple:
    """-> (docs for that page, total titles). Most searched first, ties -> most recent."""
    await _ensure_index()
    total = await trending_col.count_documents({})
    cursor = (
        trending_col.find({})
        .sort([("count", DESCENDING), ("last_searched", DESCENDING)])
        .skip(page * size)
        .limit(size)
    )
    return await cursor.to_list(length=size), total


async def get_by_key(key: str) -> dict | None:
    return await trending_col.find_one({"_id": key})
