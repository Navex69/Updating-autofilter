"""
Admin Commands Plugin

Provides a unified /admin command that shows all available admin commands
with descriptions in a formatted menu.
"""
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from strings import *


@Client.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_commands_panel(_, message):
    """Show admin commands panel with all available commands."""
    
    text = """<b>🔧 Admin Commands Panel</b>

<b>📋 Available Commands:</b>

<b>• /admin</b> - Show this admin commands panel
<b>• /settings</b> - Open admin settings panel

<b>📚 Indexing:</b>
<b>• /index</b> - Index an entire channel (auto + manual)
<b>• /stats</b> - Show indexed file count

<b>💎 Premium Management:</b>
<b>• /add_premium</b> - Add premium user
<b>• /remove_premium</b> - Remove premium user

<b>🔗 Verification Setup:</b>
<b>• /set_shortener</b> - Set verification shortener
<b>• /set_verify_time</b> - Set verification time gaps
<b>• /set_tutorial</b> - Set verification tutorial links

<b>ℹ️ Note:</b> Movie update and request features are managed through the settings panel."""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Settings Panel", callback_data="settings_open")],
        [InlineKeyboardButton("📚 Index Channels", callback_data="admin_index")],
        [InlineKeyboardButton("📊 View Stats", callback_data="admin_stats")],
    ])
    
    await message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


@Client.on_callback_query(filters.regex(r"^admin_"))
async def admin_callbacks(bot, query):
    """Handle admin panel callbacks."""
    data = query.data.split("_")[1]
    
    if data == "index":
        from plugins.index import index_cmd
        # Create a fake message for the index command
        fake_message = query.message
        fake_message.text = "/index"
        await index_cmd(bot, fake_message)
        await query.answer()
        
    elif data == "stats":
        from plugins.start import stats_cmd
        fake_message = query.message
        fake_message.text = "/stats"
        await stats_cmd(bot, fake_message)
        await query.answer()
        
    elif data == "settings":
        from plugins.settings import settings_cmd
        fake_message = query.message
        fake_message.text = "/settings"
        await settings_cmd(bot, fake_message)
        await query.answer()
    
    await query.answer()