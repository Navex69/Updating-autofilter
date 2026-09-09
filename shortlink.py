"""
Thin wrapper around Shortzy so plugins never touch the library directly.
Verification is meant to gate file delivery, not break it — if a shortener
is mis-configured or its API is down, we fall back to the raw (un-shortened)
link rather than leaving the user stuck with no way to verify at all.
"""
import logging

from shortzy import Shortzy

logger = logging.getLogger(__name__)


async def make_short_link(long_url: str, domain: str, api: str) -> str:
    if not domain or not api:
        return long_url
    try:
        shortzy = Shortzy(api_key=api, base_site=domain)
        return await shortzy.convert(long_url)
    except Exception:
        logger.warning("Shortener %s failed, falling back to raw link", domain, exc_info=True)
        return long_url
