import logging

from aiohttp import web
from pyrogram import Client

from config import API_ID, API_HASH, BOT_TOKEN, PORT
from database.filters_db import ensure_indexes
from database.premium_db import ensure_indexes as ensure_premium_indexes
from database.verify_db import ensure_indexes as ensure_verify_indexes
from database.settings_db import get_settings
from utils import temp
from web import web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


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
        await get_settings()  # warm the settings cache, create the doc on first boot
        await super().start()
        me = await self.get_me()
        temp.BOT = self
        temp.U_NAME = me.username

        runner = web.AppRunner(web_app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

        logger.info("%s started as @%s (health check on :%s)", me.first_name, me.username, PORT)

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    Bot().run()
