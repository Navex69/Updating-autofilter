"""
A single Motor client for the whole process. Opening a separate
AsyncIOMotorClient per module means a separate connection pool per module —
wasteful anywhere, and worth avoiding specifically on Mongo Atlas's free M0
tier, which caps total concurrent connections. Every database/*.py module
gets its collection from `db` here instead of creating its own client.
"""
from motor.motor_asyncio import AsyncIOMotorClient

from config import DATABASE_URI, DATABASE_NAME

# tz_aware=True: every datetime read back from Mongo comes back as a
# timezone-aware UTC object instead of a naive one. Premium expiry and
# verification-tier logic both compare stored timestamps against
# `datetime.now(timezone.utc)` — without this, that comparison raises
# "can't subtract offset-naive and offset-aware datetimes".
_client = AsyncIOMotorClient(DATABASE_URI, tz_aware=True)
db = _client[DATABASE_NAME]
