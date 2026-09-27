from pyrogram import Client, filters

from config import ADMINS
from database.filters_db import total_files
from database.premium_db import get_premium
from plugins.deliver import deliver_file
from plugins.verify import handle_notcopy
from utils import IST
from strings import (
    START_TXT, HELP_TXT,
    MYPLAN_ACTIVE_TXT, MYPLAN_NONE_TXT,
)


@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(bot, message):
    args = message.text.split(maxsplit=1)
    payload = args[1] if len(args) > 1 else ""

    if payload.startswith("file_"):
        await deliver_file(bot, message.from_user.id, payload[len("file_"):])
        return

    if payload.startswith("notcopy_"):
        await handle_notcopy(bot, message, payload[len("notcopy_"):])
        return

    await message.reply_text(START_TXT.format(mention=message.from_user.mention))


@Client.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(HELP_TXT)


@Client.on_message(filters.command("stats") & filters.user(ADMINS))
async def stats_cmd(_, message):
    count = await total_files()
    await message.reply_text(f"📁 <b>Indexed files:</b> <code>{count}</code>")


@Client.on_message(filters.command("myplan"))
async def myplan_cmd(_, message):
    doc = await get_premium(message.from_user.id)
    if doc:
        expiry = doc["expiry_time"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        await message.reply_text(MYPLAN_ACTIVE_TXT.format(expiry=expiry))
    else:
        await message.reply_text(MYPLAN_NONE_TXT)
