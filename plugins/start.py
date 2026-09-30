from pyrogram import Client, filters

from config import ADMINS
from database.filters_db import total_files
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
        # Handle movie update search query
        from database.filters_db import search_files
        from poster import fetch_poster
        from database.settings_db import get_settings
        from utils import human_size
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import html
        
        search_query = payload[len("getfile-"):].replace("-", " ")
        
        # Search for files
        results = await search_files(search_query)
        if results:
            # Fetch poster
            poster = await fetch_poster(search_query)
            
            # Get display mode
            settings = await get_settings()
            mode = settings["result_mode"]
            
            # Build results
            text = f"🔎 Results for <b>{html.escape(search_query)}</b> — found <b>{len(results)}</b>:"
            
            rows = []
            if mode == "text":
                lines = []
                for doc in results:
                    from database.filters_db import display_name
                    label = html.escape(display_name(doc))
                    url = f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
                    lines.append(f'📁 <a href="{url}">{label}</a> • {human_size(doc.get("file_size", 0))}')
                text = text + "\n\n" + "\n\n".join(lines)
            else:
                for doc in results:
                    from database.filters_db import display_name
                    label = f"{display_name(doc)} • {human_size(doc.get('file_size', 0))}"
                    if len(label) > 60:
                        label = label[:57] + "…"
                    rows.append([InlineKeyboardButton(
                        label, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['_id']}"
                    )])
            
            # Send poster if available
            if poster:
                from strings import POSTER_CAPTION_TXT
                await message.reply_photo(
                    poster["url"],
                    caption=POSTER_CAPTION_TXT.format(query=html.escape(search_query)),
                    quote=True,
                )
            
            # Send results
            markup = InlineKeyboardMarkup(rows) if rows else None
            await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
        else:
            await message.reply_text(f"❌ No results found for <b>{search_query}</b>.", parse_mode="HTML")
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
