import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache

logger = logging.getLogger(__name__)


class FloodMiddleware(BaseMiddleware):
    """Drop messages or button presses from a user who sent more than `limit` of them within `window` seconds.

    Registered as an inner middleware of the search router, so only updates that
    would start a search are counted. Time is the message date, so a backlog of
    updates replayed after a restart is not mistaken for a flood; button presses
    carry no date and use the clock.
    """

    def __init__(self, limit: int = 20, window: int = 3, clock: Callable[[], float] = time.time) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._seen: TTLCache[int, deque[int]] = TTLCache(maxsize=10_000, ttl=max(60, window))

    def is_flooding(self, user_id: int, timestamp: int) -> bool:
        stamps = self._seen.get(user_id) or deque(maxlen=self._limit + 1)
        while stamps and timestamp - stamps[0] >= self._window:
            stamps.popleft()
        stamps.append(timestamp)
        self._seen[user_id] = stamps
        return len(stamps) > self._limit

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        timestamp = event.date.timestamp() if isinstance(event, Message) else self._clock()
        if user is not None and self.is_flooding(user.id, int(timestamp)):
            logger.info("User %s is flooding, %s ignored", user.id, type(event).__name__)
            return None
        return await handler(event, data)
