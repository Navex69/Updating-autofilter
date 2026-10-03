import logging

from aiohttp import web
from pyrogram import Client
from pyrogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from pyrogram.errors import PeerIdInvalid

from config import API_ID, API_HASH, BOT_TOKEN, PORT, ADMINS
from database.filters_db import ensure_indexes
from database.premium_db import ensure_indexes as ensure_premium_indexes
from database.verify_db import ensure_indexes as ensure_verify_indexes
from database.request_db import ensure_indexes as ensure_request_indexes
from database.settings_db import get_settings
from database.movie_update_db import movie_update_db
from utils import temp
from web import web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("trending", "Most searched titles"),
    BotCommand("myplan", "Check your premium status"),
    BotCommand("req", "Request a file"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("admin", "Admin commands panel"),
    BotCommand("settings", "Admin settings panel"),
]


class Bot(Client):
    def __init__(self):
        super().__init__(
            name="autofilter-bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            sleep_threshold=10,
            workers=50,
            plugins={"root": "plugins"},
        )

    async def start(self):
        await ensure_indexes()
        await ensure_premium_indexes()
        await ensure_verify_indexes()
        await ensure_request_indexes()
        await movie_update_db.ensure_indexes()
        await get_settings()  # warm the settings cache, create the doc on first boot
        await super().start()
        me = await self.get_me()
        temp.BOT = self
        temp.U_NAME = me.username

        logger.info("Bot configuration - API_ID: %s, ADMINS: %s", API_ID, ADMINS)
        logger.info("Bot started as @%s (ID: %s)", me.username, me.id)

        await self._setup_commands()

        runner = web.AppRunner(web_app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

        logger.info("%s started as @%s (health check on :%s)", me.first_name, me.username, PORT)

    async def _setup_commands(self):
        """Runs on every boot so a fresh deploy needs zero manual BotFather
        setup — the command menu just appears correctly on its own."""
        try:
            await self.set_bot_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
        except Exception:
            logger.warning("Couldn't set the default command menu", exc_info=True)

        for admin_id in ADMINS:
            if not isinstance(admin_id, int):
                continue
            try:
                await self.set_bot_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
            except PeerIdInvalid:
                logger.info("Admin %s hasn't started the bot yet, skipping admin commands", admin_id)
            except Exception:
                logger.warning("Couldn't set the admin command menu for %s", admin_id, exc_info=True)

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    Bot().run()
