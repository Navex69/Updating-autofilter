from pyrogram import Client, filters

from database.premium_db import get_premium
from plugins.deliver import deliver_file
from plugins.verify import handle_notcopy
from utils import IST, temp
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

    if payload.startswith("getfile-"):
        # Deep link from a /m movie-update post: run the normal search flow
        # (poster, pagination, season/language/quality filters).
        import html
        from database.filters_db import search_files
        from plugins.search import _deliver_results

        search_query = payload[len("getfile-"):].replace("-", " ").strip()
        results = await search_files(search_query) if search_query else []
        if results:
            await _deliver_results(message, search_query, results)
        else:
            await message.reply_text(f"❌ No results found for <b>{html.escape(search_query)}</b>.")
        return

    await message.reply_text(START_TXT.format(mention=message.from_user.mention))


@Client.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(HELP_TXT)


@Client.on_message(filters.command("myplan"))
async def myplan_cmd(_, message):
    doc = await get_premium(message.from_user.id)
    if doc:
        expiry = doc["expiry_time"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        await message.reply_text(MYPLAN_ACTIVE_TXT.format(expiry=expiry))
    else:
        await message.reply_text(MYPLAN_NONE_TXT)
