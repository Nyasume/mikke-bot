"""The bots' only HTTP server: Telegram webhooks, token-free images, health check."""

import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import aiohttp
import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from mikke.config import derive_secret

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


@dataclass(frozen=True)
class Webhook:
    bot: Bot
    path: str
    secret: str


def webhooks(bots: list[Bot], secret: str) -> list[Webhook]:
    """Each bot's webhook path and secret; no path holds a token.

    The primary bot (the first) keeps `/` and WEBHOOK_SECRET, where its webhook
    has always been; every other bot gets `/<bot id>/` and a secret derived from
    WEBHOOK_SECRET and its id.
    """
    primary, *extra = bots
    return [
        Webhook(primary, "/", secret),
        *(Webhook(bot, f"/{bot.id}/", derive_secret(secret, bot.id)) for bot in extra),
    ]


def build_app(bots: list[Bot], dp: Dispatcher, webhook_secret: str | None = None) -> web.Application:
    """Routes: GET /healthz, GET /img/<bot id>/<file_id> and /img/<file_id>, and a webhook per bot when a secret is given.

    `bots` starts with the primary bot.
    """
    by_id = {bot.id: bot for bot in bots}

    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def image(request: web.Request) -> web.StreamResponse:
        """Stream a Telegram file without exposing the bot token in the URL.

        getFile runs on every request: the file_id stays valid, while the
        file_path download link expires after about an hour, so the fallback
        links in old messages keep working. A file_id only works with the bot
        that received the file: links name it, and the ones from before there
        were several bots, /img/<file_id>, all come from the primary bot.
        """
        file_id = request.match_info["file_id"]
        bot = by_id.get(int(request.match_info["bot_id"])) if "bot_id" in request.match_info else bots[0]
        if bot is None or not FILE_ID_RE.fullmatch(file_id):
            raise web.HTTPNotFound
        # the request's scope: the aiohttp integration sends route errors after this handler has returned
        sentry_sdk.set_tag("bot", (await bot.me()).username)
        try:
            file = await bot.get_file(file_id)
        except TelegramAPIError as e:
            logger.info("getFile failed for /img/%s/%s: %s", bot.id, file_id, e)
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
                logger.warning(
                    "Downloading /img/%s/%s failed: %s %s", bot.id, file_id, type(e).__name__, getattr(e, "status", "")
                )
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
    app.router.add_get(r"/img/{bot_id:\d{1,20}}/{file_id}", image)
    app.router.add_get("/img/{file_id}", image)
    if webhook_secret is not None:
        for hook in webhooks(bots, webhook_secret):
            SimpleRequestHandler(dispatcher=dp, bot=hook.bot, secret_token=hook.secret).register(app, path=hook.path)
    return app
