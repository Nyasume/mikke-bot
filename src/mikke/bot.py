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
    cut off mid-search, they would leave their placeholders spinning.
    """

    def __init__(self) -> None:
        self._count = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        self._count += 1
        self._idle.clear()
        try:
            return await handler(event, data)
        finally:
            self._count -= 1
            if self._count == 0:
                self._idle.set()

    async def wait(self, grace: float) -> None:
        """Return once no update is being handled, or after `grace` seconds."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._idle.wait(), grace)


def build_dispatcher(settings: Settings, searcher: Searcher, scenes: SceneSearcher, reporter: Reporter) -> Dispatcher:
    """One dispatcher for every bot: aiogram hands each handler the bot that received the update as `bot`."""
    dp = Dispatcher(searcher=searcher, scenes=scenes, reporter=reporter)
    dp["in_flight"] = in_flight = InFlight()
    dp.update.outer_middleware(in_flight)
    dp.include_router(build_router(settings.favourite_groups))
    dp.errors.register(on_error)
    return dp
