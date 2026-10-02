from urllib.parse import quote

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import FASTDL_ENABLED, STREAM_SECRET, STREAM_LINK_TTL_HOURS
from fastdl.tokens import make_token
from strings import FAST_DOWNLOAD_BTN, FAST_DOWNLOAD_LINK_BTN, FAST_NEW_LINK_BTN


def start_button_markup(file_id: str) -> InlineKeyboardMarkup | None:
    """Shown under a delivered file. None (= no button at all) when the
    feature isn't configured, so delivery is untouched."""
    if not FASTDL_ENABLED:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(FAST_DOWNLOAD_BTN, callback_data=f"fdl#{file_id}")]])


def ready_markup(file_id: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(FAST_DOWNLOAD_LINK_BTN, url=url)],
        [InlineKeyboardButton(FAST_NEW_LINK_BTN, callback_data=f"fdl#{file_id}")],
    ])


def build_url(base_url: str, msg_id: int, user_id: int, filename: str) -> str:
    token = make_token(STREAM_SECRET, msg_id, user_id, STREAM_LINK_TTL_HOURS * 3600)
    return f"{base_url}/dl/{token}/{quote((filename or 'file')[:80], safe='')}"
