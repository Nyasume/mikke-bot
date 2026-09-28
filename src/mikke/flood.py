import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ChosenInlineResult, Message
from cachetools import TTLCache

logger = logging.getLogger(__name__)


class FloodMiddleware(BaseMiddleware):
    """Drop the updates of a user who sent more than `limit` of them within `window` seconds.

    Registered as an inner middleware of the search router (messages, button
    presses, chosen inline results), so only updates that would start a search
    are counted. Time is the message date, so a backlog of updates replayed
    after a restart is not mistaken for a flood; button presses and chosen
    inline results carry no date and use the clock.

    Updates handled out of order are counted right as long as they come in runs
    (a backlog after a newer update, or the other way round); old and new ones
    interleaved across more than a window can be undercounted, which Telegram's
    ordered delivery does not produce.
    """

    def __init__(self, limit: int = 20, window: int = 3, clock: Callable[[], float] = time.time) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._seen: TTLCache[int, deque[int]] = TTLCache(maxsize=10_000, ttl=max(60, window))

    def is_flooding(self, user_id: int, timestamp: int) -> bool:
        # Updates are not always handled in the order they were sent (a replayed backlog, parallel
        # webhooks): only what is too old for this update's window goes, newer stamps stay for the
        # updates still to come, and only the stamps around this one count
        kept = [stamp for stamp in self._seen.get(user_id, ()) if timestamp - stamp < self._window]
        near = 1 + sum(1 for stamp in kept if stamp - timestamp < self._window)
        # the latest limit + 1 in arrival order are all a flood check needs
        self._seen[user_id] = deque([*kept, timestamp], self._limit + 1)
        return near > self._limit

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery | ChosenInlineResult, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery | ChosenInlineResult,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        timestamp = event.date.timestamp() if isinstance(event, Message) else self._clock()
        if user is not None and self.is_flooding(user.id, int(timestamp)):
            logger.info("User %s is flooding, %s ignored", user.id, type(event).__name__)
            if isinstance(event, CallbackQuery):
                # answered all the same, or the button keeps spinning; "query is too old" changes nothing
                with contextlib.suppress(TelegramBadRequest):
                    await event.answer()
            return None
        return await handler(event, data)
