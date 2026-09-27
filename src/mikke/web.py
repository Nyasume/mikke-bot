"""The bot's only HTTP server: Telegram webhook, token-free images, health check."""

import contextlib
import logging
import re
from pathlib import PurePosixPath

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

logger = logging.getLogger(__name__)

FILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,256}")
STREAM_TIMEOUT_SECONDS = 120
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def build_app(bot: Bot, dp: Dispatcher, webhook_secret: str | None = None) -> web.Application:
    """Routes: GET /healthz, GET /img/<file_id>, and POST / (the webhook) when a secret is given."""

    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def image(request: web.Request) -> web.StreamResponse:
        """Stream a Telegram file without exposing the bot token in the URL.

        getFile runs on every request: the file_id stays valid, while the
        file_path download link expires after about an hour, so the fallback
        links in old messages keep working.
        """
        file_id = request.match_info["file_id"]
        if not FILE_ID_RE.fullmatch(file_id):
            raise web.HTTPNotFound
        try:
            file = await bot.get_file(file_id)
        except TelegramAPIError as e:
            logger.info("getFile failed for /img/%s: %s", file_id, e)
            raise web.HTTPNotFound from e
        content_type = IMAGE_TYPES.get(PurePosixPath(file.file_path or "").suffix.lower())
        if not file.file_path or content_type is None:
            raise web.HTTPNotFound

        headers = {"Content-Type": content_type, "Cache-Control": "public, max-age=86400"}
        if request.method == "HEAD":
            return web.Response(headers=headers)

        url = bot.session.api.file_url(bot.token, file.file_path)
        async with contextlib.aclosing(bot.session.stream_content(url=url, timeout=STREAM_TIMEOUT_SECONDS)) as chunks:
            try:
                first = await anext(chunks, b"")
            except (aiohttp.ClientError, TimeoutError) as e:
                # the error text holds the file URL with the token: log the type and status only
                logger.warning("Downloading /img/%s failed: %s %s", file_id, type(e).__name__, getattr(e, "status", ""))
                raise web.HTTPBadGateway from None
            response = web.StreamResponse(headers=headers)
            await response.prepare(request)
            await response.write(first)
            async for chunk in chunks:
                await response.write(chunk)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/img/{file_id}", image)
    if webhook_secret is not None:
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=webhook_secret).register(app, path="/")
    return app
