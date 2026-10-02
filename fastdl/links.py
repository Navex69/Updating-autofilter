from urllib.parse import quote

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import FASTDL_ENABLED, STREAM_SECRET, STREAM_LINK_TTL_HOURS
from fastdl.tokens import make_token
from strings import FAST_DOWNLOAD_BTN, FAST_DOWNLOAD_LINK_BTN, FAST_WATCH_BTN


def start_button_markup(file_id: str) -> InlineKeyboardMarkup | None:
    """Shown under a delivered file. None (= no button at all) when the
    feature isn't configured, so delivery is untouched."""
    if not FASTDL_ENABLED:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(FAST_DOWNLOAD_BTN, callback_data=f"fdl#{file_id}")]])


_VIDEO_EXT = (".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v", ".ts", ".mpg", ".mpeg", ".wmv", ".flv")


def is_video(name: str, mime: str = "") -> bool:
    return (mime or "").startswith("video/") or (name or "").lower().endswith(_VIDEO_EXT)


def ready_markup(download_url: str, watch_url: str | None = None) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(FAST_DOWNLOAD_LINK_BTN, url=download_url)]
    if watch_url:
        row.append(InlineKeyboardButton(FAST_WATCH_BTN, url=watch_url))
    return InlineKeyboardMarkup([row])


def build_url(base_url: str, msg_id: int, user_id: int, filename: str, kind: str = "dl") -> str:
    """kind: "dl" (download) or "watch" (player page). Same token works for both."""
    token = make_token(STREAM_SECRET, msg_id, user_id, STREAM_LINK_TTL_HOURS * 3600)
    return f"{base_url}/{kind}/{token}/{quote((filename or 'file')[:80], safe='')}"
