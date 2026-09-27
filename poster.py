"""
Automatic poster lookup for search results — zero admin setup required.

Cascade: TMDB (landscape backdrop preferred, portrait poster fallback) ->
OMDb (portrait only, IMDb data via REST API — never scrapes imdb.com, so it
never 403s). If neither has a confident match, no poster is attached to the
result page at all (never show a wrong/unrelated poster).

Results are cached in memory (per cleaned title+year) so re-searching the
same title, or paginating/filtering an existing search, never re-hits the
network.
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

# ── query cleanup — strip quality/lang/codec noise, pull out a year hint ────
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


async def _tmdb_lookup(session: aiohttp.ClientSession, title: str, year: str):
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

    backdrop = pick.get("backdrop_path")
    poster = pick.get("poster_path")
    if backdrop:
        return {"url": f"{TMDB_IMG_LANDSCAPE}{backdrop}", "landscape": True}
    if poster:
        return {"url": f"{TMDB_IMG_PORTRAIT}{poster}", "landscape": False}
    return None


async def _omdb_lookup(session: aiohttp.ClientSession, title: str, year: str):
    params = {"apikey": OMDB_API_KEY, "t": title}
    if year:
        params["y"] = year
    async with session.get("http://www.omdbapi.com/", params=params) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
    if data.get("Response") != "True":
        return None
    poster = data.get("Poster")
    if not poster or poster == "N/A":
        return None
    return {"url": poster, "landscape": False}


async def fetch_poster(query: str) -> dict | None:
    """
    Returns {"url": str, "landscape": bool} or None if no confident match
    was found anywhere. Never raises — a poster-provider hiccup should never
    break a search result page.
    """
    if not TMDB_API_KEY and not OMDB_API_KEY:
        return None

    title, year = _clean_title_year(query)
    if not title:
        return None

    cache_key = f"{title.lower()}|{year}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or None

    result = None
    timeout = aiohttp.ClientTimeout(total=POSTER_FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if TMDB_API_KEY:
                try:
                    result = await _tmdb_lookup(session, title, year)
                except Exception:
                    logger.debug("TMDB lookup failed for %r", title, exc_info=True)
            if not result and OMDB_API_KEY:
                try:
                    result = await _omdb_lookup(session, title, year)
                except Exception:
                    logger.debug("OMDb lookup failed for %r", title, exc_info=True)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        logger.debug("Poster fetch network error for %r", title)

    _cache_set(cache_key, result)
    return result
