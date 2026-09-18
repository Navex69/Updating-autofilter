"""
Typo-correction fallback for search.

Pipeline (each stage only runs if the previous one found nothing — a
correctly-spelled query never reaches any of this, so normal search speed
and accuracy are completely unaffected):

  Stage 1 (plugins/search.py)  — normal DB search.
  Stage 2 (fuzzy_correct)      — compares the query against a cache of
      titles already in your own database. Free, in-memory, no network
      call, a few milliseconds. Catches ordinary typos.
  Stage 3 (ai_correct)         — Groq and Gemini are asked in parallel to
      guess the intended title. Neither guess is ever trusted directly:
      whichever answers first has its guess verified against the real
      database immediately. A genuine hit cancels the other request right
      away (saves time and quota) and is returned. A blank guess is
      discarded and the next response is tried instead. If nothing ever
      verifies, the fallback reports no match — an AI can never cause a
      wrong or made-up file to be shown as if it were real.

Both stages return (title, results) so the caller never re-runs the same
database search twice.
"""
import asyncio
import logging
import re
import time

import aiohttp
from rapidfuzz.distance import DamerauLevenshtein

from config import (
    GROQ_API_KEY, GROQ_MODEL, GEMINI_API_KEY, GEMINI_MODEL,
    AI_FETCH_TIMEOUT, FUZZY_MATCH_THRESHOLD,
)
from database.filters_db import files, search_files, clean_title, display_name

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — fuzzy match against your own database's titles
# ══════════════════════════════════════════════════════════════════════════════

_TITLE_CACHE: list = []          # [(clean_lower_title, display_title), ...]
_TITLE_CACHE_TIME = 0.0
_TITLE_CACHE_TTL = 600            # rebuild at most every 10 minutes
_TITLE_CACHE_FETCH_LIMIT = 8000   # bounded — cheap even on a huge collection


async def _rebuild_title_cache() -> list:
    seen, result = set(), []
    cursor = files.find({}, {"file_name": 1, "caption": 1, "_id": 0}).limit(_TITLE_CACHE_FETCH_LIMIT)
    async for doc in cursor:
        clean = clean_title(display_name(doc))
        key = clean.lower()
        if key and key not in seen:
            seen.add(key)
            result.append((key, clean))
    return result


async def _get_title_cache() -> list:
    global _TITLE_CACHE, _TITLE_CACHE_TIME
    now = time.monotonic()
    if _TITLE_CACHE and (now - _TITLE_CACHE_TIME) < _TITLE_CACHE_TTL:
        return _TITLE_CACHE
    _TITLE_CACHE = await _rebuild_title_cache()
    _TITLE_CACHE_TIME = now
    return _TITLE_CACHE


def _word_count_ok(query_words: list, cand_words: list) -> bool:
    # Different word counts almost always mean a genuinely different title
    # ("Kaithi" vs "Naan Kaithi") rather than a typo of the same one — this
    # single guard is what stops the fuzzy stage from cross-matching
    # similar-looking but unrelated titles.
    return abs(len(cand_words) - len(query_words)) <= 1


def _per_word_score(query_words: list, cand_words: list) -> float:
    """
    Per-word similarity, averaged. Uses Damerau-Levenshtein similarity
    (substitutions and adjacent-letter transpositions both cost a single
    edit, same as insertions/deletions) rather than the Indel-style ratio
    most fuzzy libraries default to — Indel systematically scores a
    *dropped* letter ("boyz" -> "boy") higher than a *substituted* one
    ("boyz" -> "boys"), which is backwards for real typos and is exactly
    what let a shorter, unrelated title outscore the actual correct one.
    Damerau's transposition handling also catches the extremely common
    swapped-adjacent-letters typo ("pushap" -> "Pushpa") that plain
    Levenshtein undervalues. A small penalty proportional to leftover
    length difference then breaks any remaining tie in favour of the
    same-length (substitution-style) candidate — the more common real typo.
    """
    if not query_words or not cand_words:
        return 0.0
    total_score = 0.0
    total_len_diff = 0
    for qw in query_words:
        best_score, best_len_diff = 0.0, 0
        for cw in cand_words:
            if qw == cw:
                best_score, best_len_diff = 100.0, 0
                break
            if abs(len(qw) - len(cw)) > 2:
                continue
            score = DamerauLevenshtein.normalized_similarity(qw, cw) * 100
            if score > best_score:
                best_score, best_len_diff = score, abs(len(qw) - len(cw))
        total_score += best_score
        total_len_diff += best_len_diff
    n = len(query_words)
    return (total_score / n) - 0.5 * (total_len_diff / n)


def _trailing_number(text: str) -> str | None:
    m = re.search(r"\b(\d{1,3})\s*$", text.strip())
    return m.group(1) if m else None


def _sequel_number_preserved(original: str, candidate: str) -> bool:
    """
    A spelling corrector should never re-decide which sequel/season/part
    the user meant — only fix how they spelled it. If the query explicitly
    ends in a number ("Weak Hero Class 2"), a correction that changes or
    drops that number ("Weak Hero Class 1") is being changed to a
    different, real title, not spell-corrected — reject it outright rather
    than let a fuzzy score or an AI's best guess quietly substitute it.
    """
    original_num = _trailing_number(original)
    if original_num is None:
        return True  # nothing to protect
    return _trailing_number(candidate) == original_num


def _best_fuzzy_match(query: str, cache: list):
    """Pure CPU work — called via asyncio.to_thread so it never blocks the
    event loop while other users' searches are being served."""
    q_clean = clean_title(query).lower()
    q_words = q_clean.split()
    if not q_words:
        return None
    q_collapsed = q_clean.replace(" ", "")

    best_score, best_title = 0.0, None
    for key, original in cache:
        if not _sequel_number_preserved(query, key):
            continue
        if key.replace(" ", "") == q_collapsed:
            return original  # same letters, just spacing/punctuation differs
        cand_words = key.split()
        if not _word_count_ok(q_words, cand_words):
            continue
        score = _per_word_score(q_words, cand_words)
        if score > best_score:
            best_score, best_title = score, original

    return best_title if best_score >= FUZZY_MATCH_THRESHOLD else None


async def fuzzy_correct(query: str):
    """Returns (title, results) or None. Never returns a title without
    results that are actually, currently in the database — a stale cache
    entry (e.g. the file was deleted since the cache was last built) simply
    falls through to the AI stage instead of being trusted blindly."""
    cache = await _get_title_cache()
    if not cache:
        return None
    guess = await asyncio.to_thread(_best_fuzzy_match, query, cache)
    if not guess:
        return None
    results = await search_files(guess)
    return (guess, results) if results else None


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — AI correction (Groq + Gemini racing, DB-verified)
# ══════════════════════════════════════════════════════════════════════════════

_AI_PROMPT = (
    "You correct misspelled movie/TV/anime search queries for a file "
    "search engine. The user typed: \"{query}\". Reply with ONLY the "
    "corrected, most likely real title — no year, no explanation, no "
    "punctuation beyond what belongs in the title itself. "
    "Only fix spelling — if the query names a specific numbered sequel, "
    "season, or part (e.g. ends in \"2\", \"Part 3\", \"Chapter 1\"), your "
    "answer MUST keep that exact same number; never substitute a "
    "different or more familiar entry in the series. If you cannot "
    "confidently identify a real, existing movie/TV/anime title, reply "
    "with exactly: NONE"
)


async def _groq_guess(query: str) -> str | None:
    if not GROQ_API_KEY:
        return None
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": _AI_PROMPT.format(query=query)}],
        "temperature": 0,
        "max_tokens": 20,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    timeout = aiohttp.ClientTimeout(total=AI_FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload, headers=headers,
            ) as r:
                if r.status != 200:
                    logger.debug("Groq guess HTTP %s", r.status)
                    return None
                data = await r.json()
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.debug("Groq guess failed", exc_info=True)
        return None
    return None if text.upper().strip(". ") == "NONE" else text


async def _gemini_guess(query: str) -> str | None:
    if not GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": _AI_PROMPT.format(query=query)}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 20},
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    timeout = aiohttp.ClientTimeout(total=AI_FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as r:
                if r.status != 200:
                    logger.debug("Gemini guess HTTP %s", r.status)
                    return None
                data = await r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        logger.debug("Gemini guess failed", exc_info=True)
        return None
    return None if text.upper().strip(". ") == "NONE" else text


async def ai_correct(query: str):
    """
    Races Groq and Gemini. Whichever responds first has its guess verified
    against the real database immediately — a genuine hit cancels the
    other request and is returned right away; a blank guess is discarded
    and the next response (if any) is tried instead.
    Returns (title, results) or None — never a raw, unverified AI guess.
    """
    tasks = {}
    if GROQ_API_KEY:
        tasks["groq"] = asyncio.create_task(_groq_guess(query))
    if GEMINI_API_KEY:
        tasks["gemini"] = asyncio.create_task(_gemini_guess(query))
    if not tasks:
        return None

    task_names = {t: n for n, t in tasks.items()}
    pending = set(tasks.values())
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                name = task_names[finished]
                try:
                    guess = finished.result()
                except Exception:
                    guess = None
                if not guess:
                    continue
                if not _sequel_number_preserved(query, guess):
                    logger.info("AI guess %r rejected — changed sequel number in %r", guess, query)
                    continue
                results = await search_files(guess)
                if results:
                    logger.info("AI correction via %s: %r -> %r", name, query, guess)
                    for t in pending:
                        t.cancel()
                    return guess, results
        return None
    finally:
        for t in pending:
            if not t.done():
                t.cancel()
