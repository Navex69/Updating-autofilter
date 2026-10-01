"""
Tiny collection that remembers who has started the bot, so "new user"
log messages fire exactly once per person — not on every /start.
"""
from datetime import datetime, timezone

from database.client import db

users_col = db["users"]


async def add_user_if_new(user_id: int) -> bool:
    """Atomically record the user. Returns True only the first time we see
    them (the upsert created the doc), False on every later call."""
    res = await users_col.update_one(
        {"_id": user_id},
        {"$setOnInsert": {"joined_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return res.upserted_id is not None


async def total_users() -> int:
    return await users_col.estimated_document_count()
