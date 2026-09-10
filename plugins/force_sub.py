import logging

from pyrogram.errors import RPCError, UserNotParticipant
from pyrogram.types import InlineKeyboardButton
from pyrogram import enums

from database.settings_db import get_settings

logger = logging.getLogger(__name__)


async def missing_channels(bot, user_id: int) -> list:
    """Return join buttons for every fsub channel the user hasn't joined.
    Empty list means the user is clear (or the feature is off / unconfigured)."""
    settings = await get_settings()
    if not settings["force_sub_enabled"] or not settings["fsub_channels"]:
        return []

    buttons = []
    for channel_id in settings["fsub_channels"]:
        try:
            await bot.get_chat_member(channel_id, user_id)
        except UserNotParticipant:
            try:
                chat = await bot.get_chat(channel_id)
                invite = await bot.create_chat_invite_link(channel_id)
                buttons.append([InlineKeyboardButton(f"📢 Join {chat.title}", url=invite.invite_link)])
            except RPCError:
                logger.warning("Can't build invite for fsub channel %s", channel_id, exc_info=True)
        except RPCError:
            # Bot itself lost access to this channel — skip it rather than
            # blocking every user because of one broken channel.
            logger.warning("Can't check membership for fsub channel %s", channel_id, exc_info=True)
    return buttons


async def is_bot_admin_in(bot, chat_id: int) -> bool:
    """True only if the bot itself is an admin/owner of this channel — the
    minimum needed to reliably receive its posts (indexing) or generate
    invite links for it (force-sub)."""
    try:
        member = await bot.get_chat_member(chat_id, bot.me.id)
        return member.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    except RPCError:
        return False
