"""
Link detection shared by the link guard (plugins/link_guard.py) and the
search filter (plugins/search.py), so both always agree on what a "link" is.

Catches: http(s):// / www. / t.me style URLs, hidden hyperlinks (text_link
entities) and bare domains on a short list of common TLDs. The TLD list is
deliberately conservative — file names like "Made.in.India" or "Mr.Pro.2020"
must never be mistaken for a domain.
"""
import re

from pyrogram import enums

_URL_RE = re.compile(
    r"(?:https?://|ftp://|tg://|www\.|(?:t|telegram)\.me/|telegram\.dog/|wa\.me/|bit\.ly/|youtu\.be/)\S*",
    re.IGNORECASE,
)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![\w@.\-])(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|xyz|info|link|site|online|club|app|biz|vip|io|click)"
    r"(?![a-z0-9\-])(?:/\S*)?",
    re.IGNORECASE,
)


def text_has_link(text: str) -> bool:
    if not text:
        return False
    return bool(_URL_RE.search(text) or _BARE_DOMAIN_RE.search(text))


def has_link(message) -> bool:
    """True if the message text/caption contains any kind of link."""
    if message.text:
        text, entities = message.text, message.entities
    else:
        text, entities = message.caption, message.caption_entities
    for entity in entities or []:
        if entity.type == enums.MessageEntityType.TEXT_LINK:
            return True
    return text_has_link(text or "")
