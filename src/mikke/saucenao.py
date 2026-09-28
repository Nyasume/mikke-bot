import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://saucenao.com/search.php"
# A key check searches SauceNAO's own banner by URL: it is always there and costs no upload
PROBE_URL = "https://saucenao.com/images/static/banner.gif"

# The free account allows 4 searches per 30 seconds and 100 per day. A search waits
# in line for a free slot in the 30-second window; the daily window is rolling and
# frees up gradually, so once it is used up the key rests and is probed again now and then.
SHORT_WINDOW_SECONDS = 30.0
LONG_WINDOW_RETRY_SECONDS = 600.0
DEFAULT_SHORT_LIMIT = 4
# How long a search may wait for its turn. A search expected to wait longer ends as "limit" at once;
# one let in may still be pushed back by slow downloads or a 429, but by one more window at most.
MAX_WAIT_SECONDS = 60.0

ACCOUNT_TYPES = {1: "Basic (free)", 2: "Premium"}

# The image to upload and its file name, fetched only once the search has a slot
Upload = Callable[[], Awaitable[tuple[bytes, str]]]


class QuotaExceededError(Exception):
    """A SauceNAO search window is used up, or the search would wait too long for a slot."""

    def __init__(self, *, daily: bool = False) -> None:
        super().__init__("daily limit" if daily else "30 second limit")
        self.daily = daily


class InvalidKeyError(Exception):
    """SauceNAO does not know the API key: made up, revoked or regenerated."""


class SauceNaoError(Exception):
    """SauceNAO answered with an error."""


@dataclass(frozen=True)
class Account:
    """What SauceNAO reports about an API key."""

    type: int | None
    short_limit: int | None
    long_limit: int | None
    long_remaining: int | None

    @property
    def plan(self) -> str | None:
        if self.type is None:
            return None
        return ACCOUNT_TYPES.get(self.type, f"account type {self.type}")


def _json(response: httpx.Response) -> dict | None:
    with contextlib.suppress(ValueError):
        data = response.json()
        return data if isinstance(data, dict) else None
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SauceNao:
    """A SauceNAO client for one API key, with that key's limits."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._clock = clock
        self._sleep = sleep
        self._short_until = 0.0
        self._daily_until = 0.0
        self._rejected_until = 0.0
        # Send times within the short window. The response headers only describe
        # finished requests, so a burst of concurrent searches is capped locally.
        self._short_limit = DEFAULT_SHORT_LIMIT
        self._sent: deque[float] = deque()
        # The line for a slot: asyncio.Lock wakes its waiters in arrival order
        self._line = asyncio.Lock()
        self._waiting = 0

    def with_key(self, api_key: str) -> "SauceNao":
        """A client for another key, over the same connection pool, with limits of its own."""
        if api_key == self._api_key:
            return self
        return SauceNao(self._client, api_key, clock=self._clock, sleep=self._sleep)

    async def search(self, *, upload: Upload | None = None, url: str | None = None) -> list[dict]:
        """Search by an uploaded image or by a public URL; return the raw `results` list."""
        data = await self._query(upload, url)
        header = data.get("header") or {}
        results = data.get("results") or []
        status = _as_int(header.get("status")) or 0
        if status != 0:
            logger.info("SauceNAO status %s: %s", status, header.get("message"))
        # status > 0 is a server-side failure; with partial results (some indexes
        # down) the results are still usable
        if status > 0 and not results:
            raise SauceNaoError(f"status {status}: {header.get('message')}")
        return results

    async def account(self) -> Account:
        """Check the key with one real search and return what SauceNAO reports for it."""
        header = (await self._query(None, PROBE_URL)).get("header") or {}
        return Account(
            type=_as_int(header.get("account_type")),
            short_limit=_as_int(header.get("short_limit")),
            long_limit=_as_int(header.get("long_limit")),
            long_remaining=_as_int(header.get("long_remaining")),
        )

    async def _query(self, upload: Upload | None, url: str | None) -> dict:
        if upload is None and url is None:
            raise ValueError("either upload or url is required")
        deadline = self._clock() + MAX_WAIT_SECONDS
        image: tuple[bytes, str] | None = None
        while True:
            reserved = await self._reserve(deadline)
            try:
                # no point in downloading the file before the search can go out
                if upload is not None and image is None:
                    image = await upload()
            finally:
                # the slot counts from the actual send, or not at all if the download failed
                with contextlib.suppress(ValueError):
                    self._sent.remove(reserved)
            self._sent.append(self._clock())
            params = {"output_type": 2, "api_key": self._api_key, "db": 999, "numres": 5, "testmode": 1}
            if image is not None:
                content, filename = image
                response = await self._client.post(SEARCH_URL, params=params, files={"file": (filename, content)})
            else:
                response = await self._client.get(SEARCH_URL, params={**params, "url": url})

            data = _json(response)
            if response.status_code == 429:
                if self._on_rate_limited(data):
                    raise QuotaExceededError(daily=True)
                # the 30-second window: back in line
                continue
            if response.status_code == 403 and data is not None:
                # SauceNAO's JSON answer to an unknown key; Cloudflare's challenge page is HTML
                logger.info("SauceNAO rejected an API key: %s", (data.get("header") or {}).get("message"))
                self._rejected_until = self._clock() + LONG_WINDOW_RETRY_SECONDS
                raise InvalidKeyError
            if response.is_error:
                raise SauceNaoError(self._hide(f"HTTP {response.status_code}: {response.text[:300]}"))
            if data is None:
                raise SauceNaoError(self._hide(f"not JSON: {response.text[:300]}"))
            self._track(data.get("header") or {})
            return data

    async def _reserve(self, deadline: float) -> float:
        """Wait in line for a free slot in the 30-second window and take it; return its time."""
        self._check()
        if self._turn(ahead=self._waiting) > deadline:
            raise QuotaExceededError
        self._waiting += 1
        try:
            async with self._line:
                # the key may have run out or been rejected while this search waited
                self._check()
                while (ready := self._turn()) > self._clock():
                    if ready > deadline + SHORT_WINDOW_SECONDS:
                        raise QuotaExceededError
                    await self._sleep(ready - self._clock())
                    self._check()
                now = self._clock()
                self._sent.append(now)
                return now
        finally:
            self._waiting -= 1

    def _check(self) -> None:
        now = self._clock()
        if now < self._rejected_until:
            raise InvalidKeyError
        if now < self._daily_until:
            raise QuotaExceededError(daily=True)

    def _turn(self, ahead: int = 0) -> float:
        """When a search behind `ahead` others in line gets its slot, if each of them sends at once."""
        now = self._clock()
        while self._sent and now - self._sent[0] >= SHORT_WINDOW_SECONDS:
            self._sent.popleft()
        times = list(self._sent)
        for _ in range(ahead + 1):
            turn = max(now, self._short_until)
            if len(times) >= self._short_limit:
                turn = max(turn, times[-self._short_limit] + SHORT_WINDOW_SECONDS)
            times.append(turn)
        return turn

    def _hide(self, text: str) -> str:
        # a user's key must not reach the logs or reports, even if SauceNAO ever quotes it
        return text.replace(self._api_key, "[key]")

    def _track(self, header: dict) -> None:
        if (short_limit := _as_int(header.get("short_limit"))) and short_limit > 0:
            self._short_limit = short_limit
        long_left = _as_int(header.get("long_remaining"))
        short_left = _as_int(header.get("short_remaining"))
        if long_left is not None and long_left <= 0:
            self._pause_daily()
        elif short_left is not None and short_left <= 0:
            self._pause_short()

    def _on_rate_limited(self, data: dict | None) -> bool:
        """Pause after a 429; True if it was the daily window."""
        header = (data or {}).get("header") or {}
        if "daily" in str(header.get("message", "")).lower() or _as_int(header.get("long_remaining")) == 0:
            self._pause_daily()
            return True
        self._pause_short()
        return False

    def _pause_daily(self) -> None:
        until = self._clock() + LONG_WINDOW_RETRY_SECONDS
        if until > self._daily_until:
            logger.info("SauceNAO daily limit reached, pausing searches for %.0f s", LONG_WINDOW_RETRY_SECONDS)
            self._daily_until = until

    def _pause_short(self) -> None:
        until = self._clock() + SHORT_WINDOW_SECONDS
        if until > self._short_until:
            logger.info("SauceNAO 30 second limit reached, searches wait for %.0f s", SHORT_WINDOW_SECONDS)
            self._short_until = until
