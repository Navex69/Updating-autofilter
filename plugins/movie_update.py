"""
Movie Update Notification Plugin

Features:
1. Manual /m command for admins to send movie update notifications
2. Automatic monitoring of FETCH_MOVIE_UPDATE channels for file uploads
3. Uses existing database search for accurate file matching
4. Different button handling: bot query search for manual, channel invite links for automatic
"""
import re
import asyncio
import logging
from datetime import datetime
from collections import defaultdict
from typing import Optional, Tuple

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from config import (
    MOVIE_UPDATE_CHANNEL,
    LINK_PREVIEW, ABOVE_PREVIEW, TMDB_POSTER, LANDSCAPE_POSTER, ADMINS
)
from database.movie_update_db import movie_update_db
from database.filters_db import search_files
from movie_metadata import get_movie_details, get_movie_detailsx
from strings import MOVIE_UPDATE_NOTIFY_TXT, MANUAL_UPDATE_NOTIFY_TXT
from utils import temp
import aiohttp

logger = logging.getLogger(__name__)

# Media filter for catching file uploads
media_filter = filters.document | filters.video | filters.audio

# Pattern matching for movie info extraction
CLEAN_PATTERN = re.compile(r'@\w+|https?://\S+|#\S+')
NORMALIZE_PATTERN = re.compile(r'[^\w\s]')
QUALITY_PATTERN = re.compile(r'\b(480p|720p|1080p|2160p|4k|uhd|hdr\d*|hdrip|bluray|bdrip|remux|web[\-\s]?dl|webrip|hdtv|dvdrip)\b', re.IGNORECASE)
SOURCE_PATTERN = re.compile(r'\b(WEBRip|BluRay|HDRip|DVDRip|HDTV|WEB-DL|WEBRip|WEB)\b', re.IGNORECASE)
RESOLUTION_PATTERN = re.compile(r'\b(480p|720p|1080p|1440p|2160p|4K)\b', re.IGNORECASE)
OTT_PLATFORMS = {
    'netflix': 'Netflix', 'prime': 'Prime Video', 'disney': 'Disney+',
    'hulu': 'Hulu', 'hbo': 'HBO Max', 'apple': 'Apple TV+',
    'paramount': 'Paramount+', 'peacock': 'Peacock'
}
EP_ONLY_RANGE = re.compile(r'\b(?:S|Season)\s*(\d+)\s*(?:E|Episode)?\s*(\d+)(?:\s*-\s*(\d+))?\b', re.IGNORECASE)
RANGE_REGEX = re.compile(r'\b(?:S|Season)\s*(\d+)\s*(?:E|Episode)?\s*(\d+)\s*-\s*(\d+)\b', re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\b(?:S|Season)\s*(\d+)\s*(?:E|Episode)?\s*(\d+)\b', re.IGNORECASE)
NAMED_REGEX = re.compile(r'\b(?:S|Season)\s*(\d+)\s*(?:E|Episode)?\s*([A-Za-z]+)\b', re.IGNORECASE)

# Language patterns
CAPTION_LANGUAGES = {
    'hin': 'Hindi', 'eng': 'English', 'tel': 'Telugu', 'tam': 'Tamil',
    'kan': 'Kannada', 'mal': 'Malayalam', 'ben': 'Bengali', 'mar': 'Marathi',
    'guj': 'Gujarati', 'pun': 'Punjabi'
}

# Release-name junk tokens that should never appear in the clean display
# title (codecs, containers, subtitle tags, audio specs, release phrases).
# Language names are added to this set at match time (see extract_media_info).
TITLE_JUNK_TOKENS = {
    # codecs / encoding
    'hevc', 'x264', 'x265', 'h264', 'h265', '10bit', '8bit', 'aac', 'ac3',
    'ddp', 'dd', 'dts', 'truehd', 'atmos',
    # containers
    'mkv', 'mp4', 'avi', 'mov',
    # subtitles
    'esubs', 'esub', 'esubd', 'msubs', 'subs', 'sub',
    # sources / release phrases
    'hdts', 'hdtc', 'hdcam', 'camrip', 'dvdscr', 'predvd', 'preweb',
    'untouched', 'dubbed', 'dual', 'audio', 'org', 'official', 'proper',
    'repack', 'channel', 'movies', 'movie'
}

# Lock management for concurrent updates
locks = {}
pending_updates = {}

def _match_languages(text: str) -> list:
    """Match language patterns in text."""
    found = []
    text_lower = text.lower()
    for code, name in CAPTION_LANGUAGES.items():
        if code in text_lower or name.lower() in text_lower:
            found.append(code)
    return found

def _match_ott_keys(text: str) -> list:
    """Match OTT platform keys in text."""
    text_lower = text.lower()
    return [k for k in OTT_PLATFORMS if k in text_lower]

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def get_source_quality(text: str) -> str:
    """Return source/format tags only: WEBRip, BluRay, HDRip, etc."""
    found = SOURCE_PATTERN.findall(text)
    seen = set()
    result = []
    for q in found:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            result.append(q)
    return ", ".join(result) if result else "N/A"

def get_resolution(text: str) -> str:
    """Return resolution tags only: 720p, 1080p, 4K, etc."""
    found = RESOLUTION_PATTERN.findall(text)
    seen = set()
    result = []
    for r in found:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            result.append(r.upper() if r.lower() == "4k" else r)
    return ", ".join(result) if result else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {OTT_PLATFORMS[k] for k in _match_ott_keys(text)}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    
    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )

def _title_tokens_to_drop(text: str) -> set:
    """Junk tokens to strip from the display title: known junk plus any
    language name/word that actually appears in this text."""
    tokens = set(TITLE_JUNK_TOKENS)
    text_lower = text.lower()
    for code, name in CAPTION_LANGUAGES.items():
        if code in text_lower or name.lower() in text_lower:
            tokens.add(name.lower())
            tokens.add(code)
    return tokens


def _strip_title_junk(base_name: str, processed: str) -> str:
    """Return a clean display title: keep real title words, drop release
    junk (codecs, containers, subs tags, languages, etc.) that survived
    base_name extraction. Preserves original word order and casing."""
    drop = _title_tokens_to_drop(processed)
    words = []
    for w in base_name.split():
        if w.lower().strip('.') in drop:
            continue
        if re.fullmatch(r'\d+(\.\d+)*', w):   # "5", "5.1", "2.0" audio specs
            continue
        if re.fullmatch(r'[\W_]+', w):          # stray punctuation-only tokens
            continue
        words.append(w.strip('.') if len(w) > 1 else w)
    return " ".join(words).strip() or base_name.strip()


def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    combined = f"{filename} {caption_clean}"
    
    # Extract year
    year_match = re.search(r'\b(19[5-9]\d|20[0-3]\d)\b', combined)
    year = year_match.group(0) if year_match else None
    
    # Extract base name (remove year, quality, etc.)
    base_name = re.sub(r'\b(19[5-9]\d|20[0-3]\d)\b', '', filename)
    base_name = QUALITY_PATTERN.sub('', base_name)
    base_name = SOURCE_PATTERN.sub('', base_name)
    base_name = RESOLUTION_PATTERN.sub('', base_name)
    base_name = normalize(base_name).strip()
    # Drop leftover release junk (codecs, containers, subtitle tags, audio
    # specs, language words) so the notification shows the clean title only.
    display_name = _strip_title_junk(base_name, combined)
    
    # Extract quality info
    quality = get_qualities(combined)
    source = get_source_quality(combined)
    resolution = get_resolution(combined)
    
    # Combine quality info
    quality_parts = []
    if source != "N/A":
        quality_parts.append(source)
    if quality != "N/A":
        quality_parts.append(quality)
    final_quality = ", ".join(quality_parts) if quality_parts else "N/A"
    
    # Extract language
    lang_keys = _match_languages(combined)
    language = ", ".join([CAPTION_LANGUAGES[k] for k in lang_keys]) if lang_keys else "N/A"
    
    # Extract OTT platform
    ott_platform = extract_ott_platform(combined)
    
    # Extract season/episode
    season, episode = extract_season_episode(filename)
    
    # Determine tag
    tag = "#SERIES" if season else "#MOVIE"
    
    return {
        "base_name": base_name,
        "display_name": display_name,
        "processed": combined,
        "year": year,
        "quality": final_quality,
        "resolution": resolution,
        "language": language,
        "ott_platform": ott_platform,
        "season": season,
        "episode": episode,
        "tag": tag
    }

async def fetch_image(url: str, size: tuple = (853, 1280)) -> Optional[bytes]:
    """Fetch and resize image from URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
    except Exception as e:
        logger.error(f"Error fetching image: {e}")
    return None

def generate_movie_message(movie_doc, base_name):
    """Generate movie update message text."""
    all_qualities = set()
    all_resolutions = set()
    all_languages = set()
    all_ott_platforms = set()
    episodes_by_season = defaultdict(set)
    
    for f in movie_doc.get("files", []):
        if q := f.get("quality"):
            if q and q != "N/A":
                all_qualities.update(x.strip() for x in q.split(",") if x.strip())
        if r := f.get("resolution"):
            if r and r != "N/A":
                all_resolutions.update(x.strip() for x in r.split(",") if x.strip())
        if l := f.get("language"):
            if l and l != "N/A":
                all_languages.update(x.strip() for x in l.split(",") if x.strip())
        if o := f.get("ott_platform"):
            if o and o != "N/A":
                all_ott_platforms.update(p.strip() for p in o.split("|") if p.strip())
        
        season = f.get("season")
        episode = f.get("episode")
        if season and episode:
            episodes_by_season[str(season)].add(str(episode))
    
    quality_str = ", ".join(sorted(all_qualities)) or "N/A"
    resolution_str = ", ".join(sorted(all_resolutions)) or "N/A"
    language_str = ", ".join(sorted(all_languages)) or "N/A"
    ott_str = " | ".join(sorted(all_ott_platforms)) or "N/A"
    
    # Collapse episode list
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for s_key, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            singles, ranges = [], []
            for ep in episodes:
                if "-" in ep:
                    ranges.append(ep)
                else:
                    try:
                        singles.append(int(ep))
                    except ValueError:
                        ranges.append(ep)
            singles.sort()
            collapsed = []
            start = end = None
            for num in singles:
                if start is None:
                    start = end = num
                elif num == end + 1:
                    end = num
                else:
                    collapsed.append(str(start) if start == end else f"{start}-{end}")
                    start = end = num
            if start is not None:
                collapsed.append(str(start) if start == end else f"{start}-{end}")
            all_ep_parts = collapsed + sorted(ranges, key=lambda s: int(s.split("-")[0]) if s.split("-")[0].isdigit() else 0)
            episode_lines.append(f"S{int(s_key)}: {', '.join(all_ep_parts)}")
        epi_str = " | ".join(episode_lines)
        if epi_str:
            epi_block = f"📺 ᴇᴘɪsᴏᴅᴇs : <b>{epi_str}</b>"
    
    rating = movie_doc.get("rating", "N/A")
    genres = movie_doc.get("genres", "N/A")
    tag = movie_doc.get("tag", "#MOVIE")
    # Prefer the clean display title recorded when the first file was
    # processed; older docs (and manual entries) fall back to the _id.
    filename = movie_doc.get("display_name") or base_name
    
    return MOVIE_UPDATE_NOTIFY_TXT.format(
        tag=tag,
        filename=filename,
        genres=genres,
        ott=ott_str,
        quality=quality_str,
        resolution=resolution_str,
        language=language_str,
        rating=rating,
        episodes=epi_block
    )

async def send_movie_update(bot, base_name):
    """Send movie update to MOVIE_UPDATE_CHANNEL."""
    max_retries = 3
    base_delay = 5
    
    for attempt in range(max_retries):
        try:
            movie_doc = await movie_update_db.get_movie_doc(base_name)
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            
            # Get unique source channels for buttons
            channels = set()
            for f in movie_doc.get("files", []):
                link = f.get("source_channel")
                if link:
                    channels.add(link)
            
            buttons = [[InlineKeyboardButton("✨ ɢᴇᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇ ✨", url=link)] for link in sorted(channels)]
            buttons.append([InlineKeyboardButton("♨️ Viral Stuff ♨️", url="https://t.me/Reload_adultbot")])
            reply_markup = InlineKeyboardMarkup(buttons)
            
            poster_url = movie_doc.get("poster_url")
            resized_poster = None
            
            if poster_url and not LINK_PREVIEW:
                is_landscape = LANDSCAPE_POSTER and TMDB_POSTER and poster_url and "original" in poster_url
                size = (2560, 1440) if is_landscape else (853, 1280)
                resized_poster = await fetch_image(poster_url, size=size)

            if resized_poster:
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": reply_markup,
                    "parse_mode": enums.ParseMode.HTML
                }
                if poster_url and LINK_PREVIEW:
                    send_params["url"] = poster_url
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await movie_update_db.update_message_id(base_name, msg.id, is_photo)
            return msg
            
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name):
    """Update existing movie message with new file info."""
    try:
        movie_doc = await movie_update_db.get_movie_doc(base_name)
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        
        channels = set()
        for f in movie_doc.get("files", []):
            link = f.get("source_channel")
            if link:
                channels.add(link)
        
        buttons = [[InlineKeyboardButton("✨ ɢᴇᴛ ᴅɪʀᴇᴄᴛ ꜰɪʟᴇ ✨", url=link)] for link in sorted(channels)]
        buttons.append([InlineKeyboardButton("♨️ Viral Stuff ♨️", url="https://t.me/Reload_adultbot")])
        reply_markup = InlineKeyboardMarkup(buttons)
        
        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await movie_update_db.update_message_id(base_name, None, False)
            except Exception:
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message: {e}")

async def process_and_send_update(bot, filename, caption, source_chat):
    """Process file and send movie update notification."""
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        if base_name not in locks:
            locks[base_name] = asyncio.Lock()
        
        async with locks[base_name]:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed, source_chat)
    except Exception as e:
        logger.exception("Processing failed: %s", e)

async def _process_with_lock(bot, filename, caption, media_info, base_name, processed, source_chat):
    """Process file with lock to prevent concurrent updates."""
    movie_doc = await movie_update_db.get_movie_doc(base_name)

    # Get source channel link
    if source_chat.username:
        channel_link = f"https://t.me/{source_chat.username}"
    else:
        channel_link = f"https://t.me/c/{str(source_chat.id)[4:]}"

    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "resolution": media_info["resolution"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"],
        "source_channel": channel_link
    }

    if not movie_doc:
        # Fetch movie metadata
        details = {}
        used_tmdb = False

        if TMDB_POSTER:
            # Query with the clean title — base_name still carries codec/
            # container junk that ruins TMDB matching.
            tmdb_result = await get_movie_detailsx(media_info["display_name"], year=media_info.get("year"))
            if tmdb_result and not tmdb_result.get("error"):
                details = tmdb_result
                used_tmdb = True
            else:
                details = await get_movie_details(base_name, file=filename) or {}
        else:
            details = await get_movie_details(base_name, file=filename) or {}

        if not details:
            logger.warning("All metadata sources failed for '%s' — sending without info", base_name)

        # Process genres
        raw_genres = details.get("genres", "") or ""
        if isinstance(raw_genres, list):
            genres = ", ".join(str(g) for g in raw_genres if g) or "N/A"
        elif isinstance(raw_genres, str) and raw_genres and raw_genres != "N/A":
            genres = ", ".join(g.strip() for g in raw_genres.split(",") if g.strip()) or "N/A"
        else:
            genres = "N/A"

        # Get poster URL
        if used_tmdb and LANDSCAPE_POSTER and details.get("backdrop_url"):
            poster_url = details["backdrop_url"]
        else:
            poster_url = details.get("poster_url")

        # Get info URL
        info_url = details.get("url") or details.get("tmdb_url") or ""

        movie_doc = {
            "_id": base_name,
            "display_name": media_info["display_name"],
            "files": [file_data],
            "poster_url": poster_url,
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": info_url,
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False
        }
        
        try:
            await movie_update_db.insert_movie_doc(movie_doc)
            await send_movie_update(bot, base_name)
            movie_doc = await movie_update_db.get_movie_doc(base_name)
        except Exception:
            movie_doc = await movie_update_db.get_movie_doc(base_name)
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc.get("files", [])):
                    return
                await movie_update_db.add_file_to_movie(base_name, file_data)
                schedule_update(bot, base_name)
    else:
        # Movie doc exists, add file if not duplicate
        if any(f["filename"] == filename for f in movie_doc.get("files", [])):
            return
        await movie_update_db.add_file_to_movie(base_name, file_data)
        schedule_update(bot, base_name)

async def _active_fetch_channels() -> list:
    """Fetch channels from the settings DB, falling back to the env var while
    the settings document hasn't been customised yet."""
    from database.settings_db import get_settings
    try:
        settings = await get_settings()
        channels = list(settings.get("fetch_movie_update_channels") or [])
        if channels:
            return channels
    except Exception:
        logger.warning("Couldn't load fetch channels from settings, using env var", exc_info=True)
    from config import FETCH_MOVIE_UPDATE
    return list(FETCH_MOVIE_UPDATE or [])


# Automatic movie update fetcher.
# NOTE: group=1 — index.py's auto-indexer lives in group 0, and Pyrogram runs
# only the first matching handler per group. With both in group 0, a channel
# that is both an index channel and a fetch channel would only ever get
# indexed (or only get a movie update), never both.
@Client.on_message(media_filter, group=1)
async def movie_update_fetcher(bot, message):
    """Automatically process files uploaded to fetch channels."""
    if message.chat.id not in await _active_fetch_channels():
        return
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return
    
    media.file_type = next(
        (ft for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    media.caption = message.caption or ""
    
    try:
        if await movie_update_db.movie_update_status(bot.me.id):
            await process_and_send_update(
                bot,
                media.file_name,
                media.caption,
                source_chat=message.chat
            )
    except Exception:
        logger.exception("Movie update fetch failed")

# Manual movie update command
_M_SEASON_RE = re.compile(r'\bs(\d{1,2})\b$', re.IGNORECASE)
_M_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')

def _parse_m_query(raw: str):
    """Parse the argument of /m command."""
    text = raw.strip()
    season = None
    year = None

    # Strip trailing season token
    m = _M_SEASON_RE.search(text)
    if m:
        season = int(m.group(1))
        text = text[:m.start()].strip()

    # Strip trailing 4-digit year
    m = _M_YEAR_RE.search(text)
    if m:
        year = m.group(1)
        text = (text[:m.start()] + text[m.end():]).strip()

    title = text.strip()
    return title, year, season

def is_title_match(search_title: str, file_text: str) -> bool:
    """Check if file text contains all words from search title."""
    search_words = set(search_title.lower().split())
    file_words = set(file_text.lower().split())
    return search_words.issubset(file_words)

async def _build_manual_update_doc(title: str, year: str, season: int):
    """Build movie document from database search results."""
    # Build search term
    if season:
        search_term = f"{title} s{season:02d}"
    else:
        search_term = title
    if year:
        search_term_with_year = f"{title} {year}"
    else:
        search_term_with_year = search_term

    # Search DB — search_files() returns a plain list of file dicts
    files = await search_files(search_term)
    if not files and year:
        files = await search_files(search_term_with_year)
    if not files:
        files = await search_files(title)

    # Filter for exact title matches
    files = [f for f in files if is_title_match(title, f"{f.get('file_name', '')} {f.get('caption', '') or ''}")]
    total = len(files)

    # Extract metadata from files
    all_qualities = set()
    all_resolutions = set()
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()
    episodes_by_season = defaultdict(set)

    for f in files:
        fname = f.get("file_name", "")
        cap = f.get("caption", "") or ""
        unified = f"{fname} {cap}".lower()

        src = get_source_quality(unified)
        if src != "N/A":
            all_qualities.update(x.strip() for x in src.split(",") if x.strip())

        res = get_resolution(unified)
        if res != "N/A":
            all_resolutions.update(x.strip() for x in res.split(",") if x.strip())

        lang_keys = _match_languages(unified)
        for k in lang_keys:
            all_languages.add(CAPTION_LANGUAGES[k])

        ott = extract_ott_platform(unified)
        if ott != "N/A":
            all_ott_platforms.update(p.strip() for p in ott.split("|") if p.strip())

        s, ep = extract_season_episode(fname)
        if s is not None and ep is not None:
            all_tags.add("#SERIES")
            episodes_by_season[str(s)].add(str(ep))
        else:
            all_tags.add("#MOVIE")

    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"

    # Collapse episode list
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for s_key, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            singles, ranges = [], []
            for ep in episodes:
                if "-" in ep:
                    ranges.append(ep)
                else:
                    try:
                        singles.append(int(ep))
                    except ValueError:
                        ranges.append(ep)
            singles.sort()
            collapsed = []
            start = end = None
            for num in singles:
                if start is None:
                    start = end = num
                elif num == end + 1:
                    end = num
                else:
                    collapsed.append(str(start) if start == end else f"{start}-{end}")
                    start = end = num
            if start is not None:
                collapsed.append(str(start) if start == end else f"{start}-{end}")
            all_ep_parts = collapsed + sorted(ranges, key=lambda s: int(s.split("-")[0]) if s.split("-")[0].isdigit() else 0)
            episode_lines.append(f"S{int(s_key)}: {', '.join(all_ep_parts)}")
        epi_str = " | ".join(episode_lines)
        if epi_str:
            epi_block = f"📺 ᴇᴘɪsᴏᴅᴇs : <b>{epi_str}</b>"

    # Build pseudo files for message generation
    pseudo_files = [{
        "quality": ", ".join(sorted(all_qualities)) or "N/A",
        "resolution": ", ".join(sorted(all_resolutions)) or "N/A",
        "language": ", ".join(sorted(all_languages)) or "N/A",
        "ott_platform": " | ".join(sorted(all_ott_platforms)) or "N/A",
        "tag": primary_tag,
        "season": None,
        "episode": None,
    }]

    return {
        "_id": title,
        "files": pseudo_files,
        "genres": "N/A",
        "rating": "N/A",
        "poster_url": None,
        "imdb_url": "",
        "tag": primary_tag,
        "ott_platform": " | ".join(sorted(all_ott_platforms)) or "N/A",
        "message_id": None,
        "is_photo": False,
        "_epi_block": epi_block,
        "_total_files": total,
    }, files

@Client.on_message(filters.command("m") & filters.user(ADMINS))
async def manual_movie_update(bot, message):
    """Manual movie update command for admins."""
    try:
        raw_arg = message.text.split(None, 1)[1].strip()
    except IndexError:
        return await message.reply_text(
            "<b>⚠️ Usage:</b>\n"
            "<code>/m pushpa 2</code>\n"
            "<code>/m suits s02</code>\n"
            "<code>/m ironman 2003</code>\n"
            "<code>/m dark knight</code>",
            parse_mode=enums.ParseMode.HTML
        )

    title, year, season = _parse_m_query(raw_arg)
    if not title:
        return await message.reply_text("<b>❌ Could not parse a title from your input.</b>")

    status_msg = await message.reply_text("<b>⏳ Fetching metadata and searching database…</b>")

    try:
        # Fetch metadata
        details = {}
        used_tmdb = False
        tmdb_query = f"{title} s{season:02d}" if season else title

        if TMDB_POSTER:
            tmdb_result = await get_movie_detailsx(tmdb_query, year=year)
            if tmdb_result and not tmdb_result.get("error"):
                details = tmdb_result
                used_tmdb = True
            else:
                details = await get_movie_details(title, file=None) or {}
        else:
            details = await get_movie_details(title, file=None) or {}

        # Build display title
        meta_title = details.get("title") or ""
        if meta_title:
            display_title = meta_title
            if year:
                display_title += f" ({year})"
            elif details.get("year"):
                display_title += f" ({details['year']})"
            if season:
                display_title += f" Season {season}"
        else:
            display_title = title.title()
            if season:
                display_title += f" Season {season}"
            if year:
                display_title += f" ({year})"

        # Process genres and rating
        raw_genres = details.get("genres", "") or ""
        if isinstance(raw_genres, list):
            genres = ", ".join(str(g) for g in raw_genres if g) or "N/A"
        elif isinstance(raw_genres, str) and raw_genres and raw_genres != "N/A":
            genres = ", ".join(g.strip() for g in raw_genres.split(",") if g.strip()) or "N/A"
        else:
            genres = "N/A"

        rating_raw = details.get("rating", "N/A")
        try:
            rating_display = f"{float(rating_raw):.1f}" if rating_raw and rating_raw != "N/A" else "N/A"
        except (ValueError, TypeError):
            rating_display = str(rating_raw) if rating_raw else "N/A"

        # Get poster URL
        if used_tmdb and LANDSCAPE_POSTER and details.get("backdrop_url"):
            poster_url = details["backdrop_url"]
        else:
            poster_url = details.get("poster_url")

        # Search DB for files
        pseudo_doc, db_files = await _build_manual_update_doc(title, year, season)
        total_files = pseudo_doc["_total_files"]
        epi_block = pseudo_doc["_epi_block"]

        # Determine tag
        primary_tag = pseudo_doc["tag"]
        if primary_tag == "#MOVIE" and details.get("kind") == "tv":
            primary_tag = "#SERIES"
        if season:
            primary_tag = "#SERIES"

        # Aggregate quality/resolution/language
        all_qualities = set()
        all_resolutions = set()
        all_languages = set()
        for f in pseudo_doc["files"]:
            q = f.get("quality", "N/A")
            if q and q != "N/A":
                all_qualities.update(x.strip() for x in q.split(",") if x.strip())
            r = f.get("resolution", "N/A")
            if r and r != "N/A":
                all_resolutions.update(x.strip() for x in r.split(",") if x.strip())
            lang = f.get("language", "N/A")
            if lang and lang != "N/A":
                all_languages.update(x.strip() for x in lang.split(",") if x.strip())

        quality_str = ", ".join(sorted(all_qualities)) or "N/A"
        resolution_str = ", ".join(sorted(all_resolutions)) or "N/A"
        language_str = ", ".join(sorted(all_languages)) or "N/A"

        # Build caption
        text = MANUAL_UPDATE_NOTIFY_TXT.format(
            tag=primary_tag,
            filename=display_title,
            genres=genres,
            quality=quality_str,
            resolution=resolution_str,
            language=language_str,
            rating=rating_display,
            episodes=epi_block,
        )

        # Build buttons with bot query search link
        raw_search = title
        if season:
            raw_search = f"{raw_search} s{season:02d}"
        search_query = raw_search.strip().replace(" ", "-")
        search_query = re.sub(r"[^A-Za-z0-9_\-]", "", search_query)

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔍 ɢᴇᴛ ꜰɪʟᴇs",
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{search_query}"
            )],
            [InlineKeyboardButton(
                "♨️ Viral Stuff ♨️",
                url="https://t.me/Reload_adultbot"
            )],
        ])

        # Send to MOVIE_UPDATE_CHANNEL
        resized_poster = None
        if poster_url and not LINK_PREVIEW:
            is_landscape = used_tmdb and LANDSCAPE_POSTER and poster_url and "original" in poster_url
            size = (2560, 1440) if is_landscape else (853, 1280)
            resized_poster = await fetch_image(poster_url, size=size)

        if resized_poster:
            await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=resized_poster,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            send_params = {
                "chat_id": MOVIE_UPDATE_CHANNEL,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": enums.ParseMode.HTML,
            }
            if poster_url and LINK_PREVIEW:
                send_params["url"] = poster_url
                send_params["invert_media"] = ABOVE_PREVIEW
            await bot.send_message(**send_params)

        # Confirm to admin
        files_note = f"({total_files} files in DB)" if total_files else "(no files found in DB yet)"
        await status_msg.edit_text(
            f"<b>✅ Update posted!</b>\n"
            f"<b>Title:</b> {display_title}\n"
            f"<b>DB:</b> {files_note}",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception as exc:
        logger.exception("Manual movie update failed: %s", exc)
        await status_msg.edit_text(f"<b>❌ Failed:</b> <code>{exc}</code>", parse_mode=enums.ParseMode.HTML)

# Movie update notifications are managed from the unified /settings panel
# (Settings -> 🎬 Movie Updates): the on/off toggle lives in the global
# settings document and fetch channels are admin-managed there too. There is
# deliberately no separate /movie_update command any more.