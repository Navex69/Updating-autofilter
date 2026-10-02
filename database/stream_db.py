from datetime import datetime, timezone

from pymongo import ReturnDocument

from database.client import db
from utils import IST

bin_col = db["stream_files"]      # file _id -> its copy in BIN_CHANNEL (copied once, reused)
usage_col = db["stream_usage"]    # monthly bandwidth reserved per host
daily_col = db["stream_daily"]    # per-user fast-download links used today


def _today() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def get_bin_entry(file_id: str) -> dict | None:
    return await bin_col.find_one({"_id": file_id})


async def save_bin_entry(file_id: str, bin_msg_id: int, size: int, name: str) -> dict:
    """First writer wins, so two simultaneous clicks on a new file end up
    sharing one BIN copy instead of fighting over it."""
    return await bin_col.find_one_and_update(
        {"_id": file_id},
        {"$setOnInsert": {"bin_msg_id": bin_msg_id, "size": size, "name": name}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def get_usage(host_key: str) -> int:
    doc = await usage_col.find_one({"_id": f"{_month()}:{host_key}"})
    return int(doc["bytes"]) if doc else 0


async def add_usage(host_key: str, nbytes: int):
    await usage_col.update_one(
        {"_id": f"{_month()}:{host_key}"}, {"$inc": {"bytes": int(nbytes)}}, upsert=True
    )


async def get_daily(user_id: int) -> int:
    doc = await daily_col.find_one({"_id": user_id})
    if not doc or doc.get("date") != _today():
        return 0
    return doc.get("count", 0)


async def increment_daily(user_id: int):
    res = await daily_col.update_one({"_id": user_id, "date": _today()}, {"$inc": {"count": 1}})
    if res.matched_count == 0:
        await daily_col.update_one(
            {"_id": user_id}, {"$set": {"date": _today(), "count": 1}}, upsert=True
        )
