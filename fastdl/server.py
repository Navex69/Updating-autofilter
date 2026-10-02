"""
HTTP side of Fast Download. Used by both the bot process (Koyeb/Render) and
the standalone Oracle server (stream_server.py).

  GET/HEAD /dl/<token>/<filename>   — file download, Range/resume supported
  GET      /health                  — lets the bot check this host is up
"""
import asyncio
import logging
import re
from urllib.parse import quote

from aiohttp import web

from config import (
    STREAM_SECRET, STREAM_PREFETCH, STREAM_MAX_CONNECTIONS, STREAM_MAX_PER_LINK,
)
from fastdl.pool import NotFound, pool
from fastdl.tokens import verify_token

logger = logging.getLogger(__name__)

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_active_total = 0
_active_by_token: dict[str, int] = {}

_EXPIRED_HTML = (
    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<body style='font-family:sans-serif;text-align:center;padding:3em'>"
    "<h3>⏳ This download link has expired</h3>"
    "<p>Go back to the bot and tap <b>New Link</b> under the file.</p></body>"
)


def parse_range(header: str | None, size: int):
    """-> (start, end_inclusive, is_partial). Raises ValueError if unsatisfiable."""
    if not header:
        return 0, size - 1, False
    m = _RANGE_RE.match(header.strip())
    if not m:  # multi-range / garbage: legal to ignore and send the whole file
        return 0, size - 1, False
    a, b = m.groups()
    if a == "" and b == "":
        return 0, size - 1, False
    if a == "":  # suffix range: last N bytes
        n = int(b)
        if n == 0:
            raise ValueError("empty suffix")
        return max(size - n, 0), size - 1, True
    start = int(a)
    end = min(int(b), size - 1) if b else size - 1
    if start >= size or start > end:
        raise ValueError("out of range")
    return start, end, True


def _disposition(name: str) -> str:
    ascii_name = re.sub(r'[^\w.\- ]', "_", name.encode("ascii", "ignore").decode()) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


async def handle_download(request: web.Request):
    global _active_total
    token = request.match_info["token"]
    parsed = verify_token(STREAM_SECRET, token)
    if not parsed:
        return web.Response(text=_EXPIRED_HTML, status=403, content_type="text/html")
    msg_id, _user_id, _exp = parsed

    if not pool.workers:
        return web.Response(text="Server is starting, try again in a moment.", status=503,
                            headers={"Retry-After": "10"})
    try:
        meta = await pool.meta(msg_id)
    except NotFound:
        return web.Response(text="File not found.", status=404)
    except Exception:
        logger.exception("meta lookup failed for msg %s", msg_id)
        return web.Response(text="Couldn't reach Telegram, try again.", status=503,
                            headers={"Retry-After": "5"})

    size = meta.size
    if size <= 0:
        return web.Response(text="File not found.", status=404)

    etag = f'"{msg_id}-{size}"'
    range_header = request.headers.get("Range")
    if_range = request.headers.get("If-Range")
    if if_range and if_range != etag:
        range_header = None  # validator mismatch: restart with the full file

    try:
        start, end, partial = parse_range(range_header, size)
    except ValueError:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})

    length = end - start + 1
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": _disposition(meta.name),
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    if request.method == "HEAD":
        headers["Content-Length"] = str(length)
        return web.Response(status=206 if partial else 200, headers=headers)

    if _active_total >= STREAM_MAX_CONNECTIONS or _active_by_token.get(token, 0) >= STREAM_MAX_PER_LINK:
        return web.Response(text="Server busy, try again in a minute.", status=429,
                            headers={"Retry-After": "15"})

    _active_total += 1
    _active_by_token[token] = _active_by_token.get(token, 0) + 1
    try:
        resp = web.StreamResponse(status=206 if partial else 200, headers=headers)
        resp.content_length = length
        await resp.prepare(request)
        try:
            async for data in pool.iter_range(msg_id, start, end, STREAM_PREFETCH):
                await resp.write(data)
            await resp.write_eof()
        except (ConnectionResetError, ConnectionError):
            pass  # user closed the tab / paused the download — normal
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stream of msg %s aborted mid-way", msg_id)
            if request.transport:
                request.transport.close()  # truncated body -> client can resume
        return resp
    finally:
        _active_total -= 1
        left = _active_by_token.get(token, 1) - 1
        if left <= 0:
            _active_by_token.pop(token, None)
        else:
            _active_by_token[token] = left


async def handle_health(_request: web.Request):
    return web.Response(text="ok")


def register_routes(app: web.Application):
    app.add_routes([
        web.get("/dl/{token}/{name:.*}", handle_download),
        web.get("/dl/{token}", handle_download),
        web.get("/health", handle_health),
    ])
