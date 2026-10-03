"""
Filter-word helpers. Admin-defined words / phrases are silently dropped from
a user's query before it is searched ("punjabi movies" -> "punjabi").
Matching is case-insensitive, whole-word, and phrases tolerate any amount of
whitespace between their words.
"""
import re


def parse_word_list(raw: str) -> list:
    """'Movies, Full Movie ,hd' -> ['movies', 'full movie', 'hd'] (comma / newline
    separated, lower-cased, whitespace collapsed, de-duplicated, order kept)."""
    out, seen = [], set()
    for part in re.split(r"[,\n]", raw or ""):
        word = re.sub(r"\s+", " ", part).strip().lower()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def apply_filter_words(query: str, words: list) -> str:
    """Remove every filter word/phrase from `query`; longest phrases first so
    'full movie' goes before 'movie'. Returns the cleaned query ('' if nothing left)."""
    if not words:
        return query.strip()
    cleaned = query
    for word in sorted(words, key=len, reverse=True):
        pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(p) for p in word.split()) + r"(?![A-Za-z0-9])"
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()
