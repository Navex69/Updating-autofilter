"""
/filterwords, /set_filterword, /remove_filterword

Filter words are dropped from every search query (see search.py), e.g. with
"movies" as a filter word, "punjabi movies" is searched as just "punjabi".
/filterwords is for everyone; the other two are admin-only. All three work in
groups and in the bot's DM. Multiple words/phrases are comma-separated.
"""
import html

from pyrogram import Client, filters

from config import ADMINS
from database.settings_db import get_settings, update_settings
from filterwords import parse_word_list
from strings import (
    FILTERWORDS_LIST_TXT, FILTERWORDS_EMPTY_TXT,
    SET_FILTERWORD_USAGE, SET_FILTERWORD_OK,
    REMOVE_FILTERWORD_USAGE, REMOVE_FILTERWORD_OK,
)

_TEXT_LIMIT = 4000


def _fmt(words: list) -> str:
    return ", ".join(f"<code>{html.escape(w)}</code>" for w in words) or \
        "—"


@Client.on_message(filters.command("filterwords"))
async def filterwords_cmd(_, message):
    settings = await get_settings()
    words = settings.get("filter_words") or []
    if not words:
        await message.reply_text(FILTERWORDS_EMPTY_TXT)
        return

    lines, size, shown = [], 0, 0
    for w in words:
        line = f"• <code>{html.escape(w)}</code>"
        if size + len(line) > _TEXT_LIMIT:
            break
        lines.append(line)
        size += len(line) + 1
        shown += 1
    text = FILTERWORDS_LIST_TXT.format(count=len(words), words="\n".join(lines))
    if shown < len(words):
        text += f"\n… and {len(words) - shown} more"
    await message.reply_text(text)


@Client.on_message(filters.command("set_filterword") & filters.user(ADMINS))
async def set_filterword_cmd(_, message):
    args = message.text.split(maxsplit=1)
    new_words = parse_word_list(args[1]) if len(args) > 1 else []
    if not new_words:
        await message.reply_text(SET_FILTERWORD_USAGE)
        return

    settings = await get_settings()
    current = list(settings.get("filter_words") or [])
    added = [w for w in new_words if w not in current]
    existing = [w for w in new_words if w in current]
    if added:
        settings = await update_settings({"filter_words": current + added})

    await message.reply_text(SET_FILTERWORD_OK.format(
        added=_fmt(added), existing=_fmt(existing), total=len(settings.get("filter_words") or []),
    ))


@Client.on_message(filters.command("remove_filterword") & filters.user(ADMINS))
async def remove_filterword_cmd(_, message):
    args = message.text.split(maxsplit=1)
    targets = parse_word_list(args[1]) if len(args) > 1 else []
    if not targets:
        await message.reply_text(REMOVE_FILTERWORD_USAGE)
        return

    settings = await get_settings()
    current = list(settings.get("filter_words") or [])
    removed = [w for w in targets if w in current]
    missing = [w for w in targets if w not in current]
    if removed:
        remaining = [w for w in current if w not in removed]
        settings = await update_settings({"filter_words": remaining})

    await message.reply_text(REMOVE_FILTERWORD_OK.format(
        removed=_fmt(removed), missing=_fmt(missing), total=len(settings.get("filter_words") or []),
    ))
