import logging
from datetime import datetime, timezone

from database.client import db
from utils import IST

logger = logging.getLogger(__name__)

limit_col = db["file_limit_usage"]


def _today() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d")


async def get_today_count(user_id: int) -> int:
    doc = await limit_col.find_one({"_id": user_id})
    if not doc or doc.get("date") != _today():
        return 0
    return doc.get("count", 0)


async def increment_today(user_id: int) -> int:
    """Increment and return the new count for today."""
    today = _today()
    doc = await limit_col.find_one({"_id": user_id})
    if not doc or doc.get("date") != today:
        await limit_col.update_one(
            {"_id": user_id}, {"$set": {"date": today, "count": 1}}, upsert=True
        )
        return 1
    new_count = doc.get("count", 0) + 1
    await limit_col.update_one({"_id": user_id}, {"$set": {"count": new_count}})
    return new_count
