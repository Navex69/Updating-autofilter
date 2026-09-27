"""
Request database module - stores user file requests.
Follows the same pattern as other database modules in this repo.
"""
import logging
from datetime import datetime, timezone, timedelta

from pymongo import ASCENDING

from database.client import db

logger = logging.getLogger(__name__)

request_col = db["requests"]


async def ensure_indexes():
    """Create indexes for the requests collection."""
    await request_col.create_index([("user_id", 1), ("query", 1)])
    await request_col.create_index([("created_at", ASCENDING)], name="created_idx")


def _now():
    return datetime.now(timezone.utc)


async def add_request(user_id: int, query: str, username: str = None):
    """Add a new file request."""
    doc = {
        "user_id": user_id,
        "query": query,
        "username": username,
        "created_at": _now(),
        "status": "pending"
    }
    await request_col.insert_one(doc)


async def get_recent_requests(limit: int = 50):
    """Get recent requests."""
    cursor = request_col.find().sort("created_at", ASCENDING).limit(limit)
    return await cursor.to_list(length=limit)


async def get_user_requests(user_id: int, limit: int = 20):
    """Get requests by a specific user."""
    cursor = request_col.find({"user_id": user_id}).sort("created_at", ASCENDING).limit(limit)
    return await cursor.to_list(length=limit)


async def update_request_status(request_id: str, status: str):
    """Update the status of a request."""
    await request_col.update_one(
        {"_id": request_id},
        {"$set": {"status": status}}
    )


async def delete_old_requests(days: int = 30):
    """Delete requests older than specified days."""
    cutoff = _now() - timedelta(days=days)
    await request_col.delete_many({"created_at": {"$lt": cutoff}})


async def count_requests() -> int:
    """Count total requests."""
    return await request_col.count_documents({})