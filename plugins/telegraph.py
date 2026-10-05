"""
/telegraph  (also /telepragh, /tgraph) — ADMIN ONLY.

Turns a photo / video into a public link, and also shows the Telegram
`file_id` of the same file. Use it to fill config.py:

    PREMIUM_PHOTO = <link or file_id of a photo>
    WELCOME_VIDEO = <file_id of the how-it-works video>

How to use (any of these):
    1. Reply to a photo / video with /telegraph
    2. Send a photo / video with /telegraph as its caption
    3. Send /telegraph, then send the photo / video

Providers (config.TELEGRAPH_PROVIDER): "auto" tries telegra.ph first for files up
to 5 MB (then graph.org), and falls back to Catbox (up to 200 MB) if it fails or
the file is bigger.
"""
import asyncio
import html
import logging
import os
import time

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS, TELEGRAPH_PROVIDER
from utils import human_size
from strings import (
    TELEGRAPH_PROMPT_TXT, TELEGRAPH_TIMEOUT_TXT, TELEGRAPH_NOT_MEDIA_TXT,
    TELEGRAPH_TOO_BIG_TXT, TELEGRAPH_DOWNLOADING_TXT, TELEGRAPH_UPLOADING_TXT,
    TELEGRAPH_FAILED_TXT, TELEGRAPH_DONE_TXT,
)

logger = logging.getLogger(__name__)

TELEGRAPH_LIMIT = 5 * 1024 * 1024        # telegra.ph / graph.org
CATBOX_LIMIT = 200 * 1024 * 1024         # catbox.moe
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
_TIMEOUT = aiohttp.ClientTimeout(total=600, connect=20)


# ── what counts as uploadable media ──────────────────────────────────────────

def _media_of(message):
    """(media_object, kind) for a photo / video / animation / image-or-video
    document, else None."""
    if not message:
        return None
    if message.photo:
        return message.photo, "photo"
    if message.video:
        return message.video, "video"
    if message.animation:
        return message.animation, "video"
    doc = message.document
    if doc and (doc.mime_type or "").startswith(("image/", "video/")):
        return doc, "video" if doc.mime_type.startswith("video/") else "photo"
    return None


def _ext_for(media, kind: str) -> str:
    name = getattr(media, "file_name", None) or ""
    if "." in name:
        return os.path.splitext(name)[1].lower()
    return ".jpg" if kind == "photo" else ".mp4"


# ── providers ────────────────────────────────────────────────────────────────

async def _upload_telegraph(path: str, host: str):
    """telegra.ph-style endpoint: POST /upload (multipart 'file') ->
    [{"src": "/file/xxxx.jpg"}] or {"error": "..."}"""
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_UA) as session:
        with open(path, "rb") as fh:
            form = aiohttp.FormData()
            form.add_field("file", fh, filename=os.path.basename(path))
            async with session.post(f"https://{host}/upload", data=form) as resp:
                data = await resp.json(content_type=None)
    if isinstance(data, list) and data and data[0].get("src"):
        return f"https://{host}{data[0]['src']}"
    raise RuntimeError(f"{host}: {data}")


async def _upload_catbox(path: str):
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_UA) as session:
        with open(path, "rb") as fh:
            form = aiohttp.FormData()
            form.add_field("reqtype", "fileupload")
            form.add_field("fileToUpload", fh, filename=os.path.basename(path))
            async with session.post("https://catbox.moe/user/api.php", data=form) as resp:
                text = (await resp.text()).strip()
    if text.startswith("https://"):
        return text
    raise RuntimeError(f"catbox: {text[:200]}")


async def upload_file(path: str, size: int):
    """-> (url, host). Raises RuntimeError with every provider's error."""
    provider = TELEGRAPH_PROVIDER
    attempts = []
    if provider in ("auto", "telegraph") and size <= TELEGRAPH_LIMIT:
        attempts += [("telegra.ph", lambda: _upload_telegraph(path, "telegra.ph")),
                     ("graph.org", lambda: _upload_telegraph(path, "graph.org"))]
    if provider in ("auto", "catbox") and size <= CATBOX_LIMIT:
        attempts.append(("catbox.moe", lambda: _upload_catbox(path)))

    errors = []
    for host, fn in attempts:
        try:
            return await fn(), host
        except Exception as exc:
            logger.warning("Upload to %s failed: %s", host, exc)
            errors.append(f"{host}: {str(exc)[:120]}")
    raise RuntimeError("\n".join(errors) or "no provider available for this file size")


def _limit_for_provider() -> int:
    return TELEGRAPH_LIMIT if TELEGRAPH_PROVIDER == "telegraph" else CATBOX_LIMIT


# ── command ──────────────────────────────────────────────────────────────────

@Client.on_message(filters.command(["telegraph", "telepragh", "tgraph"]) & filters.user(ADMINS))
async def telegraph_cmd(bot, message):
    source = message.reply_to_message if _media_of(message.reply_to_message) else (
        message if _media_of(message) else None
    )

    status = None
    if source is None:
        # Nothing attached — ask for it.
        status = await message.reply_text(TELEGRAPH_PROMPT_TXT, quote=True)
        try:
            reply = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        except asyncio.TimeoutError:
            await status.edit_text(TELEGRAPH_TIMEOUT_TXT)
            return
        if not _media_of(reply):
            await status.edit_text(TELEGRAPH_NOT_MEDIA_TXT)
            return
        source = reply

    media, kind = _media_of(source)
    size = getattr(media, "file_size", 0) or 0
    limit = _limit_for_provider()
    if size > limit:
        text = TELEGRAPH_TOO_BIG_TXT.format(size=human_size(size), limit=human_size(limit))
        await (status.edit_text(text) if status else message.reply_text(text, quote=True))
        return

    if status:
        await status.edit_text(TELEGRAPH_DOWNLOADING_TXT)
    else:
        status = await message.reply_text(TELEGRAPH_DOWNLOADING_TXT, quote=True)

    # Download to disk (not RAM) so a 100 MB video can't blow a small instance.
    path = f"/tmp/tg_{int(time.time())}_{message.from_user.id}{_ext_for(media, kind)}"
    try:
        downloaded = await bot.download_media(source, file_name=path)
        if isinstance(downloaded, str):
            path = downloaded
        real_size = os.path.getsize(path)

        await status.edit_text(TELEGRAPH_UPLOADING_TXT)
        try:
            url, host = await upload_file(path, real_size)
        except RuntimeError as exc:
            await status.edit_text(TELEGRAPH_FAILED_TXT.format(error=html.escape(str(exc))))
            return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open Link", url=url)],
        [InlineKeyboardButton("❌ Close", callback_data="tgph#close")],
    ])
    await status.edit_text(
        TELEGRAPH_DONE_TXT.format(url=url, host=host, file_id=media.file_id),
        reply_markup=markup,
        disable_web_page_preview=True,
    )


@Client.on_callback_query(filters.regex(r"^tgph#close$") & filters.user(ADMINS))
async def telegraph_close(_, query):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
