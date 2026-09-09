from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.verify_db import consume_token, mark_verified
from utils import temp
from strings import VERIFY_DONE_TXT, VERIFY_GET_FILE_BTN, VERIFY_EXPIRED_TXT


async def handle_notcopy(bot, message, token: str):
    doc = await consume_token(token, message.from_user.id)
    if not doc:
        await message.reply_text(VERIFY_EXPIRED_TXT)
        return

    await mark_verified(message.from_user.id, doc["tier"])

    buttons = InlineKeyboardMarkup([[InlineKeyboardButton(
        VERIFY_GET_FILE_BTN, url=f"https://t.me/{temp.U_NAME}?start=file_{doc['file_id']}"
    )]])
    await message.reply_text(VERIFY_DONE_TXT, reply_markup=buttons)
