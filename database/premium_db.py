"""
Premium grants bypass force-sub and verification entirely (see plugins/verify.py
and plugins/force_sub.py). The master on/off switch lives in settings_db —
when it's off, `has_premium_access` always returns False here regardless of
what's stored, so admins can kill the perk bot-wide without deleting data.
"""
import logging
from datetime import datetime, timezone

from pymongo import ASCENDING

from database.client import db
from database.settings_db import get_settings
from utils import ensure_utc

logger = logging.getLogger(__name__)

premium_col = db["premium_users"]


async def ensure_indexes():
    await premium_col.create_index([("expiry_time", ASCENDING)], name="expiry_idx")


def _now():
    return datetime.now(timezone.utc)


async def add_premium(user_id: int, expiry: datetime):
    await premium_col.update_one(
        {"_id": user_id},
        {"$set": {"expiry_time": expiry}},
        upsert=True,
    )


async def remove_premium(user_id: int) -> bool:
    result = await premium_col.delete_one({"_id": user_id})
    return result.deleted_count > 0


async def get_premium(user_id: int) -> dict | None:
    doc = await premium_col.find_one({"_id": user_id})
    if not doc:
        return None
    expiry = ensure_utc(doc.get("expiry_time"))
    if expiry and expiry <= _now():
        await premium_col.delete_one({"_id": user_id})
        return None
    doc["expiry_time"] = expiry
    return doc


async def has_premium_access(user_id: int) -> bool:
    settings = await get_settings()
    if not settings["premium_enabled"]:
        return False
    doc = await get_premium(user_id)
    return doc is not None


async def list_premium(skip: int = 0, limit: int = 10) -> list:
    cursor = premium_col.find({}).sort("expiry_time", ASCENDING).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    for doc in docs:
        doc["expiry_time"] = ensure_utc(doc.get("expiry_time"))
    return docs


async def count_premium() -> int:
    return await premium_col.count_documents({})
