import logging

from pyrogram import Client, filters
from pyrogram.errors import RPCError

from config import ADMINS
from database.filters_db import get_file_by_id, total_files
from strings import START_TXT, HELP_TXT, FILE_SEND_CAPTION, FILE_NOT_FOUND_TXT

logger = logging.getLogger(__name__)


@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(bot, message):
    args = message.text.split(maxsplit=1)

    # Deep link: t.me/<bot>?start=file_<id>  → tapped from a group search result
    if len(args) > 1 and args[1].startswith("file_"):
        object_id = args[1][len("file_"):]
        doc = await get_file_by_id(object_id)
        if not doc:
            await message.reply_text(FILE_NOT_FOUND_TXT)
            return
        try:
            await bot.send_cached_media(
                chat_id=message.chat.id,
                file_id=doc["file_id"],
                caption=FILE_SEND_CAPTION.format(file_name=doc["file_name"]),
            )
        except RPCError:
            logger.exception("Failed to deliver file %s", object_id)
            await message.reply_text(FILE_NOT_FOUND_TXT)
        return

    await message.reply_text(START_TXT.format(mention=message.from_user.mention))


@Client.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(HELP_TXT)


@Client.on_message(filters.command("stats") & filters.user(ADMINS))
async def stats_cmd(_, message):
    count = await total_files()
    await message.reply_text(f"📁 <b>Indexed files:</b> <code>{count}</code>")
