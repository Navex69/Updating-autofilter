"""
Request plugin - handles user file requests to admins.
Follows the clean architecture pattern of this repo.
"""
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, REQUEST_CHANNEL
from database.request_db import add_request
from strings import REQUEST_SENT_TXT, REQUEST_RECEIVED_TXT, REQUEST_NOT_CONFIGURED_TXT

# In-memory deduplication to prevent spam
_REQUEST_DEDUP = {}


async def send_request(bot, user_id: int, query: str, username: str = None, origin_message=None):
    """
    Send a file request to the admin channel.
    
    Args:
        bot: The bot instance
        user_id: The user's Telegram ID
        query: The search query/file name being requested
        username: The user's username (optional)
        origin_message: The original message (for group context)
    """
    if not REQUEST_CHANNEL:
        return None
    
    # Deduplication check
    dedup_key = f"{user_id}:{query.lower().strip()}"
    if _REQUEST_DEDUP.get(user_id) == dedup_key:
        return None
    _REQUEST_DEDUP[user_id] = dedup_key
    
    # Store in database
    username = username or f"ID: {user_id}"
    await add_request(user_id, query, username)
    
    # Build buttons
    buttons = []
    if origin_message and origin_message.chat.type in ("group", "supergroup"):
        try:
            buttons.append([InlineKeyboardButton("👀 View Request", url=origin_message.link)])
        except Exception:
            pass
    else:
        buttons.append([InlineKeyboardButton("👀 View Request", callback_data=f"view_req_{user_id}")])
    
    # Send to request channel
    user_mention = f"<a href='tg://user?id={user_id}'>{username}</a>"
    request_text = REQUEST_RECEIVED_TXT.format(
        user_mention=user_mention,
        user_id=user_id,
        query=query
    )
    
    try:
        sent = await bot.send_message(
            REQUEST_CHANNEL,
            request_text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
        )
        return sent
    except Exception as e:
        print(f"Failed to send request to channel: {e}")
        return None


@Client.on_callback_query(filters.regex(r"^view_req_"))
async def view_request_callback(bot, query):
    """Handle callback to view user request info."""
    user_id = int(query.data.split("_")[2])
    if query.from_user.id not in ADMINS:
        await query.answer("⚠️ Admin only!", show_alert=True)
        return
    
    await query.answer("Viewing request info...")
    # You can implement additional logic here to show request details


@Client.on_message(filters.command("req") & filters.private)
@Client.on_message(filters.command("request") & filters.private)
async def manual_request_cmd(bot, message):
    """
    Manual request command - /req or /request <file_name>
    Allows users to manually request a file.
    """
    if not REQUEST_CHANNEL:
        await message.reply_text(REQUEST_NOT_CONFIGURED_TXT)
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text(
            "📮 <b>How to use:</b>\n\n"
            "<code>/request Movie Name</code>\n"
            "<code>/req Movie Name</code>\n\n"
            "Example: <code>/req Inception 2010</code>"
        )
        return
    
    query = args[1].strip()
    user = message.from_user
    username = user.username or user.first_name
    
    sent = await send_request(
        bot,
        user_id=user.id,
        query=query,
        username=username,
        origin_message=message
    )
    
    if sent:
        await message.reply_text(
            REQUEST_SENT_TXT.format(query=query),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✨ View Your Request ✨", url=sent.link)]
            ])
        )
    else:
        await message.reply_text("❌ Failed to send request. Please try again later.")
