"""
One document holds every admin-configurable toggle: force-sub, premium,
verification, auto-delete timers, file limits, indexing channels, and how
search results are displayed. Everything else in the bot (search, indexing,
file delivery) reads this through `get_settings()`.

Reads are cached in memory — every non-admin request (every search, every
file delivery, every auto-indexed post) hits this cache, not the database.
The cache is only refreshed when an admin actually changes something
(`update_settings`) or on first use after a cold start, so this adds no
per-request DB cost no matter how many features are toggled on.
"""
import logging

from database.client import db
from config import CHANNELS

logger = logging.getLogger(__name__)

settings_col = db["settings"]
_DOC_ID = "global"

DEFAULTS = {
    "_id": _DOC_ID,
    "force_sub_enabled": False,
    "fsub_channels": [],          # list[int] channel ids
    "premium_enabled": True,
    "verify_enabled": False,
    "result_mode": "button",      # "button" | "text"
    "verify_time": 8 * 3600,          # tier 1 -> tier 2 gap, seconds
    "third_verify_time": 8 * 3600,    # tier 2 -> tier 3 gap, seconds
    "shorteners": {
        "1": {"domain": "", "api": ""},
        "2": {"domain": "", "api": ""},
        "3": {"domain": "", "api": ""},
    },
    "tutorials": {"1": "", "2": "", "3": ""},
    # Seeded once from the CHANNELS env var so existing deployments keep
    # auto-indexing without any action; from here on this list lives only
    # in the database and is managed from /settings.
    "index_channels": list(CHANNELS),
    "query_autodelete_enabled": False,
    "query_autodelete_seconds": 300,
    "file_autodelete_enabled": False,
    "file_autodelete_seconds": 600,
    "file_limit_enabled": False,
    "file_limit_count": 2,
}

_cache: dict | None = None


def _merge_defaults(doc: dict) -> dict:
    """Fill in any keys older documents might be missing (safe upgrades)."""
    merged = {**DEFAULTS, **doc}
    merged["shorteners"] = {**DEFAULTS["shorteners"], **doc.get("shorteners", {})}
    merged["tutorials"] = {**DEFAULTS["tutorials"], **doc.get("tutorials", {})}
    return merged


async def get_settings(force_refresh: bool = False) -> dict:
    global _cache
    if _cache is not None and not force_refresh:
        return _cache

    doc = await settings_col.find_one_and_update(
        {"_id": _DOC_ID},
        {"$setOnInsert": DEFAULTS},
        upsert=True,
        return_document=True,
    )
    _cache = _merge_defaults(doc)
    return _cache


async def update_settings(patch: dict) -> dict:
    """Shallow $set on the settings document; refreshes the cache."""
    await settings_col.update_one({"_id": _DOC_ID}, {"$set": patch}, upsert=True)
    return await get_settings(force_refresh=True)


async def set_shortener(tier: int, domain: str, api: str) -> dict:
    return await update_settings({f"shorteners.{tier}": {"domain": domain, "api": api}})


async def set_tutorial(tier: int, url: str) -> dict:
    return await update_settings({f"tutorials.{tier}": url})


async def _add_channel(field: str, channel_id: int) -> dict:
    settings = await get_settings()
    channels = list(settings[field])
    if channel_id not in channels:
        channels.append(channel_id)
    return await update_settings({field: channels})


async def _remove_channel(field: str, channel_id: int) -> dict:
    settings = await get_settings()
    channels = [c for c in settings[field] if c != channel_id]
    return await update_settings({field: channels})


async def add_fsub_channel(channel_id: int) -> dict:
    return await _add_channel("fsub_channels", channel_id)


async def remove_fsub_channel(channel_id: int) -> dict:
    return await _remove_channel("fsub_channels", channel_id)


async def add_index_channel(channel_id: int) -> dict:
    return await _add_channel("index_channels", channel_id)


async def remove_index_channel(channel_id: int) -> dict:
    return await _remove_channel("index_channels", channel_id)
