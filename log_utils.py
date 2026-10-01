"""
Everything that talks *about* the bot to admins / the log channel lives
here: the boot-time restart notice, the new-user log and the user-verified
log. Every function is failure-tolerant — a logging problem must never
break /start, verification, or boot.
"""
import asyncio
import logging
from datetime import datetime

import aiohttp

from config import ADMINS, BOT_TOKEN, LOG_CHANNEL, RESTART_NOTIFY
from database.users_db import add_user_if_new, total_users
from strings import RESTART_TXT, NEW_USER_LOG_TXT, USER_VERIFIED_LOG_TXT
from utils import IST, temp

logger = logging.getLogger(__name__)

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}

# asyncio only keeps weak references to tasks — hold our own so a
# fire-and-forget log message can't be garbage-collected mid-flight.
_background_tasks: set = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _now_ist() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


# ── restart notice → every admin's DM ───────────────────────────────────────

async def _dm_via_bot_api(session: aiohttp.ClientSession, chat_id: int, text: str) -> bool:
    """Plain Bot API call. Unlike Pyrogram's send_message, this doesn't need
    the user to be in the local session cache — which is empty after every
    fresh deploy — so admins who *did* start the bot are never wrongly
    skipped. Telegram itself rejects the ones who never started it."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
    except Exception as exc:
        # Log the type only — aiohttp tracebacks can include the token-bearing URL.
        logger.warning("Restart notice to admin %s failed (%s)", chat_id, type(exc).__name__)
        return False

    if data.get("ok"):
        return True
    # 403 = never started / blocked the bot, 400 = chat not found. Skip quietly.
    logger.info("Skipping admin %s for restart notice: %s", chat_id, data.get("description"))
    return False


async def notify_admins_restart():
    if not RESTART_NOTIFY:
        return
    admin_ids = list(dict.fromkeys(a for a in ADMINS if isinstance(a, int)))
    if not admin_ids:
        return

    text = RESTART_TXT.format(username=temp.U_NAME, time=_now_ist())
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(*(_dm_via_bot_api(session, a, text) for a in admin_ids))
    logger.info("Restart notice delivered to %s/%s admins", sum(results), len(admin_ids))


def schedule_restart_notice():
    """Fire-and-forget so a slow Telegram response can't delay boot."""
    _spawn(notify_admins_restart())


# ── log channel ─────────────────────────────────────────────────────────────

async def _send_log(text: str):
    if not LOG_CHANNEL or temp.BOT is None:
        return
    try:
        await temp.BOT.send_message(LOG_CHANNEL, text, disable_web_page_preview=True)
    except Exception:
        logger.warning("Couldn't post to the log channel", exc_info=True)


def _user_fields(user) -> dict:
    return {
        "mention": user.mention,
        "user_id": user.id,
        "username": f"@{user.username}" if user.username else "—",
    }


async def _log_new_user(user):
    text = NEW_USER_LOG_TXT.format(
        **_user_fields(user),
        total=await total_users(),
        time=_now_ist(),
        bot=temp.U_NAME,
    )
    await _send_log(text)


async def register_user(user):
    """Call on every private /start. Records the user and, only the first
    time we've ever seen them, posts a new-user message to the log channel."""
    try:
        if await add_user_if_new(user.id):
            _spawn(_log_new_user(user))
    except Exception:
        logger.warning("Couldn't register user %s", user.id, exc_info=True)


def log_user_verified(user, tier: int):
    """Call right after a shortener verification succeeds."""
    text = USER_VERIFIED_LOG_TXT.format(
        **_user_fields(user),
        ordinal=_ORDINALS.get(tier, str(tier)),
        tier=tier,
        time=_now_ist(),
    )
    _spawn(_send_log(text))
