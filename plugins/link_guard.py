"""
Link guard — works in groups and in the bot's DM.

Any message from a non-admin that contains a link is deleted and the sender
gets "sending link is not allowed". It runs before every other handler
(group=-1) and stops propagation, so a link is never treated as a search
query or a command argument. Bot admins are never deleted.
"""
import logging

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError

from config import ADMINS
from linkcheck import has_link
from strings import LINK_NOT_ALLOWED_TXT

logger = logging.getLogger(__name__)


def _is_link_from_user(_, __, message):
    sender = message.from_user
    if message.outgoing or not sender or sender.is_bot or sender.is_self:
        return False
    if sender.id in ADMINS:
        return False
    if message.chat.type not in (enums.ChatType.PRIVATE, enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return False
    return has_link(message)


link_filter = filters.create(_is_link_from_user)


@Client.on_message(link_filter, group=-1)
async def block_links(bot, message):
    try:
        await message.delete()
    except RPCError:
        # In a group the bot needs the "delete messages" admin right.
        logger.warning("Couldn't delete a link message in chat %s", message.chat.id, exc_info=True)
    try:
        await bot.send_message(
            message.chat.id,
            LINK_NOT_ALLOWED_TXT.format(mention=message.from_user.mention),
        )
    except RPCError:
        logger.debug("Couldn't send the link warning", exc_info=True)
    message.stop_propagation()
