"""
Extended movie metadata fetching for movie update notifications.
Fetches detailed information from TMDB and OMDb including genres, rating, etc.
"""
import re
import time
import asyncio
import logging

import aiohttp

from config import TMDB_API_KEY, OMDB_API_KEY, POSTER_FETCH_TIMEOUT

logger = logging.getLogger(__name__)

TMDB_IMG_LANDSCAPE = "https://image.tmdb.org/t/p/w1280"
TMDB_IMG_PORTRAIT = "https://image.tmdb.org/t/p/w500"

_CACHE: dict = {}
_CACHE_TTL = 6 * 3600
_CACHE_MAX_ENTRIES = 500

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_SE_RE = re.compile(r"\bS\d{1,2}(E\d{1,3})?\b", re.IGNORECASE)
_NOISE_RE = re.compile(
    r"\b(480p|720p|1080p|2160p|4k|uhd|hdr\d*|hdrip|bluray|bdrip|remux|"
    r"web[\-\s]?dl|webrip|hdtv|dvdrip|dvdscr|cam|hdts|ts|"
    r"x264|x265|hevc|avc|aac|ac3|dts|ddp\d?\.?\d?|flac|mp3|"
    r"esub|esubs|subs?|subbed|dubbed|dual audio|multi audio|dual|multi|"
    r"hindi|english|tamil|telugu|kannada|malayalam|bengali|punjabi|marathi|"
    r"gujarati|urdu|korean|japanese)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[._\-\+\[\]()]+")
_SPACE_RE = re.compile(r"\s{2,}")


def _clean_title_year(query: str) -> tuple[str, str]:
    text = query.strip()
    year_m = _YEAR_RE.search(text)
    year = year_m.group(0) if year_m else ""
    text = _SE_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    if year:
        text = text.replace(year, " ")
    text = _SPACE_RE.sub(" ", text).strip()
    return text, year


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry[0] > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return entry[1]


def _cache_set(key: str, value):
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), value)


async def _tmdb_lookup_detailed(session: aiohttp.ClientSession, title: str, year: str):
    """Fetch detailed movie/series info from TMDB."""
    params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
    if year:
        params["year"] = year
    async with session.get("https://api.themoviedb.org/3/search/multi", params=params) as r:
        if r.status != 200:
            return None
        data = await r.json()
    results = [x for x in data.get("results", []) if x.get("media_type") in ("movie", "tv")]
    if not results:
        return None

    title_lower = title.lower()
    exact = [
        x for x in results
        if (x.get("title") or x.get("name") or "").lower() == title_lower
    ]
    pick = (exact or results)[0]

    media_type = pick.get("media_type")
    item_id = pick.get("id")
    
    # Fetch detailed info
    if media_type == "movie":
        detail_url = f"https://api.themoviedb.org/3/movie/{item_id}"
    else:
        detail_url = f"https://api.themoviedb.org/3/tv/{item_id}"
    
    params = {"api_key": TMDB_API_KEY}
    async with session.get(detail_url, params=params) as r:
        if r.status != 200:
            return None
        detail_data = await r.json()

    # Extract genres
    genres = [g["name"] for g in detail_data.get("genres", [])]
    
    # Extract rating
    rating = detail_data.get("vote_average")
    if rating:
        rating = f"{rating:.1f}"
    else:
        rating = "N/A"

    # Extract poster/backdrop
    backdrop = detail_data.get("backdrop_path")
    poster = detail_data.get("poster_path")
    
    poster_url = None
    if backdrop:
        poster_url = f"{TMDB_IMG_LANDSCAPE}{backdrop}"
    elif poster:
        poster_url = f"{TMDB_IMG_PORTRAIT}{poster}"

    # Get IMDB ID for URL
    imdb_id = detail_data.get("imdb_id")
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""
    
    # Determine kind
    kind = "tv" if media_type == "tv" else "movie"

    return {
        "title": pick.get("title") or pick.get("name") or title,
        "year": detail_data.get("release_date") or detail_data.get("first_air_date") or year,
        "genres": ", ".join(genres) if genres else "N/A",
        "rating": rating,
        "poster_url": poster_url,
        "backdrop_url": f"{TMDB_IMG_LANDSCAPE}{backdrop}" if backdrop else None,
        "imdb_url": imdb_url,
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{item_id}",
        "kind": kind,
        "error": None
    }


async def _omdb_lookup_detailed(session: aiohttp.ClientSession, title: str, year: str):
    """Fetch detailed movie/series info from OMDb."""
    params = {"apikey": OMDB_API_KEY, "t": title, "plot": "short"}
    if year:
        params["y"] = year
    async with session.get("http://www.omdbapi.com/", params=params) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
    if data.get("Response") != "True":
        return None

    # Extract genres
    genres = data.get("Genre", "N/A")
    
    # Extract rating
    imdb_rating = data.get("imdbRating", "N/A")
    if imdb_rating and imdb_rating != "N/A":
        try:
            imdb_rating = f"{float(imdb_rating):.1f}"
        except ValueError:
            pass

    # Extract poster
    poster = data.get("Poster")
    poster_url = poster if poster and poster != "N/A" else None

    # Get IMDB URL
    imdb_id = data.get("imdbID")
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""

    # Determine kind
    kind = "tv" if data.get("Type") == "series" else "movie"

    return {
        "title": data.get("Title") or title,
        "year": data.get("Year") or year,
        "genres": genres,
        "rating": imdb_rating,
        "poster_url": poster_url,
        "backdrop_url": None,
        "imdb_url": imdb_url,
        "tmdb_url": "",
        "kind": kind,
        "error": None
    }


async def get_movie_details(query: str, file: str = None) -> dict:
    """
    Fetch detailed movie metadata including genres, rating, poster, etc.
    Returns dict with movie details or None if not found.
    """
    if not TMDB_API_KEY and not OMDB_API_KEY:
        return {}

    title, year = _clean_title_year(query)
    if not title:
        return {}

    cache_key = f"details_{title.lower()}|{year}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or {}

    result = None
    timeout = aiohttp.ClientTimeout(total=POSTER_FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if TMDB_API_KEY:
                try:
                    result = await _tmdb_lookup_detailed(session, title, year)
                except Exception:
                    logger.debug("TMDB detailed lookup failed for %r", title, exc_info=True)
            if not result and OMDB_API_KEY:
                try:
                    result = await _omdb_lookup_detailed(session, title, year)
                except Exception:
                    logger.debug("OMDb detailed lookup failed for %r", title, exc_info=True)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        logger.debug("Movie metadata fetch network error for %r", title)

    _cache_set(cache_key, result or {})
    return result or {}


async def get_movie_detailsx(query: str, year: str = None) -> dict:
    """
    Extended version with explicit year parameter for better matching.
    Used by the manual movie update command.
    """
    if not TMDB_API_KEY and not OMDB_API_KEY:
        return {"error": "No API keys configured"}

    title, _ = _clean_title_year(query)
    if not title:
        return {"error": "Invalid query"}

    cache_key = f"detailsx_{title.lower()}|{year or ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result = None
    timeout = aiohttp.ClientTimeout(total=POSTER_FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if TMDB_API_KEY:
                try:
                    result = await _tmdb_lookup_detailed(session, title, year or "")
                except Exception:
                    logger.debug("TMDB detailed lookup failed for %r", title, exc_info=True)
            if not result and OMDB_API_KEY:
                try:
                    result = await _omdb_lookup_detailed(session, title, year or "")
                except Exception:
                    logger.debug("OMDb detailed lookup failed for %r", title, exc_info=True)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        logger.debug("Movie metadata fetch network error for %r", title)

    _cache_set(cache_key, result or {"error": "Not found"})
    return result or {"error": "Not found"}