"""The bots' only HTTP server: Telegram webhooks, token-free images, health check."""

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

import aiohttp
import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from mikke.bot import InFlight
from mikke.config import derive_secret

logger = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

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


def counted(handle: Handler, in_flight: InFlight) -> Handler:
    """A webhook route, counted in InFlight from the moment a request arrives, and refused once shutdown has begun.

    aiogram answers Telegram at once and handles the update in a task of its own,
    which counts itself when it starts. The request stops counting only after that,
    so shutdown never sees nothing running between Telegram's 200 and that task.
    """

    async def webhook(request: web.Request) -> web.StreamResponse:
        if in_flight.closing:
            # Telegram keeps the update and sends it again after a while, to the next container
            raise web.HTTPServiceUnavailable
        in_flight.enter()
        try:
            return await handle(request)
        finally:
            # callbacks run in the order they were scheduled: after the first step of the task `handle` created
            asyncio.get_running_loop().call_soon(in_flight.leave)

    return webhook


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
        except TelegramBadRequest as e:
            logger.info("getFile failed for /img/%s/%s: %s", bot.id, file_id, e)
            raise web.HTTPNotFound from e
        except TelegramAPIError as e:
            # Telegram is down or busy, and the file may well be fine: not a 404 for the search engines
            logger.warning("getFile failed for /img/%s/%s: %s", bot.id, file_id, e)
            raise web.HTTPBadGateway from e
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
        in_flight: InFlight = dp["in_flight"]
        for hook in webhooks(bots, webhook_secret):
            # answered at once, the update handled in a task of its own: a search may wait a minute for SauceNAO
            handler = SimpleRequestHandler(
                dispatcher=dp, bot=hook.bot, secret_token=hook.secret, handle_in_background=True
            )
            app.router.add_post(hook.path, counted(handler.handle, in_flight))
    return app
