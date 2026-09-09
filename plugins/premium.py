from datetime import datetime, timezone, timedelta

from pyrogram import Client, filters
from pyrogram.errors import RPCError

from config import ADMINS
from database.premium_db import add_premium, remove_premium
from utils import parse_duration, IST
from strings import (
    PREMIUM_ADDED_TXT, PREMIUM_REMOVED_TXT, PREMIUM_NOT_FOUND_TXT, PREMIUM_USAGE_TXT,
)


@Client.on_message(filters.command("add_premium") & filters.private & filters.user(ADMINS))
async def add_premium_cmd(bot, message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply_text(PREMIUM_USAGE_TXT)
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply_text(PREMIUM_USAGE_TXT)
        return

    seconds = parse_duration(parts[2])
    if not seconds:
        await message.reply_text(PREMIUM_USAGE_TXT)
        return

    expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await add_premium(user_id, expiry)

    expiry_str = expiry.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    await message.reply_text(PREMIUM_ADDED_TXT.format(user_id=user_id, expiry=expiry_str))
    try:
        await bot.send_message(user_id, PREMIUM_ADDED_TXT.format(user_id=user_id, expiry=expiry_str))
    except RPCError:
        pass


@Client.on_message(filters.command("remove_premium") & filters.private & filters.user(ADMINS))
async def remove_premium_cmd(_, message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: <code>/remove_premium user_id</code>")
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply_text("Usage: <code>/remove_premium user_id</code>")
        return

    removed = await remove_premium(user_id)
    await message.reply_text(
        PREMIUM_REMOVED_TXT.format(user_id=user_id) if removed else PREMIUM_NOT_FOUND_TXT
    )
