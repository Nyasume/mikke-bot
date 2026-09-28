import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject

from mikke.config import Settings
from mikke.handlers import build_router, on_error
from mikke.keys import KeyStore
from mikke.observability import tag_bot
from mikke.reports import Reporter
from mikke.scenes import SceneSearcher
from mikke.search import Searcher


def build_bot(token: str, session: BaseSession | None = None) -> Bot:
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


class InFlight(BaseMiddleware):
    """Counts updates being handled, so shutdown can let running searches finish.

    Webhook updates are handled in background tasks that nothing waits for;
    cut off mid-search, they would leave their placeholders spinning. A webhook
    request counts too, until its update's task has started (`web.counted`),
    and once shutdown has begun (`closing`) webhooks are refused, so the count
    only goes down.
    """

    def __init__(self) -> None:
        self._count = 0
        self._idle = asyncio.Event()
        self._idle.set()
        # set on SIGTERM: from then on the webhooks answer 503 and Telegram sends the updates again later
        self.closing = False

    def enter(self) -> None:
        self._count += 1
        self._idle.clear()

    def leave(self) -> None:
        self._count -= 1
        if self._count == 0:
            self._idle.set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        self.enter()
        try:
            return await handler(event, data)
        finally:
            self.leave()

    async def wait(self, grace: float) -> None:
        """Return once no update is being handled, or after `grace` seconds."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._idle.wait(), grace)


def build_dispatcher(
    settings: Settings, searcher: Searcher, scenes: SceneSearcher, reporter: Reporter, keys: KeyStore
) -> Dispatcher:
    """One dispatcher for every bot: aiogram hands each handler the bot that received the update as `bot`."""
    dp = Dispatcher(searcher=searcher, scenes=scenes, reporter=reporter, keys=keys)
    dp["in_flight"] = in_flight = InFlight()
    # outermost, around aiogram's own middlewares (the error handler runs in one): an update counts until
    # its error has been reported too
    builtin = list(dp.update.outer_middleware)
    for middleware in builtin:
        dp.update.outer_middleware.unregister(middleware)
    for middleware in (in_flight, *builtin):
        dp.update.outer_middleware(middleware)
    # the bot's username on Sentry events; aiogram runs the error handler outside the update middlewares
    dp.update.outer_middleware(tag_bot)
    dp.errors.outer_middleware(tag_bot)
    dp.include_router(build_router(settings.favourite_groups))
    dp.errors.register(on_error)
    return dp
