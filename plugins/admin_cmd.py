"""
Admin Commands Plugin

Provides a unified /admin command that shows all available admin commands
with descriptions in a formatted menu.
"""
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from database.filters_db import total_files
from database.settings_db import get_settings
from strings import SETTINGS_MAIN_TXT

ADMIN_PANEL_TXT = """<b>🔧 Admin Commands Panel</b>

<b>📋 Available Commands:</b>

<b>• /admin</b> - Show this admin commands panel
<b>• /settings</b> - Open admin settings panel

<b>📚 Indexing:</b>
<b>• /index</b> - Index an entire channel (auto + manual)
<b>• /stats</b> - Show indexed file count

<b>🎬 Movie Updates:</b>
<b>• /m title [year] [s02]</b> - Post a movie/series update (e.g. <code>/m pushpa 2</code>, <code>/m suits s02</code>)

<b>💎 Premium Management:</b>
<b>• /add_premium</b> - Add premium user
<b>• /remove_premium</b> - Remove premium user

<b>🔗 Verification Setup:</b>
<b>• /set_shortener</b> - Set verification shortener
<b>• /set_verify_time</b> - Set verification time gaps
<b>• /set_tutorial</b> - Set verification tutorial links

<b>ℹ️ Note:</b> Auto movie updates, fetch channels and requests are managed through the settings panel."""


@Client.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_commands_panel(_, message):
    """Show admin commands panel with all available commands."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Settings Panel", callback_data="admin_settings")],
        [InlineKeyboardButton("📚 Index Channels", callback_data="admin_index")],
        [InlineKeyboardButton("📊 View Stats", callback_data="admin_stats")],
    ])
    await message.reply_text(
        ADMIN_PANEL_TXT,
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@Client.on_callback_query(filters.regex(r"^admin_") & filters.user(ADMINS))
async def admin_callbacks(bot, query):
    """Handle admin panel callbacks."""
    action = query.data.split("_", 1)[1]

    if action == "settings":
        # Imported lazily: settings.py is a sibling plugin.
        from plugins.settings import build_main_menu
        settings = await get_settings()
        await query.answer()
        await query.message.edit_text(SETTINGS_MAIN_TXT, reply_markup=build_main_menu(settings))
        return

    if action == "index":
        # /index walks the admin through a private-chat conversation, so it
        # can't be started from a button on someone else's message.
        await query.answer("Send /index to me in a private chat to start indexing.", show_alert=True)
        return

    if action == "stats":
        count = await total_files()
        await query.answer(f"Indexed files: {count}", show_alert=True)
        return

    await query.answer()
