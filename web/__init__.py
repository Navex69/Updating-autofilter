from aiohttp import web


async def _health(_):
    return web.Response(text="ok")


web_app = web.Application()
web_app.add_routes([web.get("/", _health)])

