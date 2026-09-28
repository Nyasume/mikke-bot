import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from mikke import outages

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
# A search waiting for its slot looks again this often: a slot given back by a failed
# download, or a key rejected or used up meanwhile, is noticed at once
LINE_CHECK_SECONDS = 1.0

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


class UnavailableError(SauceNaoError):
    """SauceNAO did not answer, also on the retry: down, overloaded or out of reach. Not our bug."""


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
        # (used, limit) of the daily window as SauceNAO last reported it, for the owner reports
        self._usage: tuple[int, int] | None = None

    @property
    def usage(self) -> tuple[int, int] | None:
        """How much of the daily window SauceNAO last said was used: (used, limit); None before its first answer."""
        return self._usage

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
        message = self._hide(str(header.get("message")))
        if status != 0:
            logger.info("SauceNAO status %s: %s", status, message)
        # status > 0 is a server-side failure; with partial results (some indexes
        # down) the results are still usable
        if status > 0 and not results:
            raise SauceNaoError(f"status {status}: {message}")
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
            response = await self._send(image, url, deadline)

            data = _json(response)
            if response.status_code == 429:
                if self._on_rate_limited(data):
                    raise QuotaExceededError(daily=True)
                # the 30-second window: back in line
                continue
            if response.status_code == 403 and data is not None:
                # SauceNAO's JSON answer to an unknown key; Cloudflare's challenge page is HTML
                message = (data.get("header") or {}).get("message")
                logger.info("SauceNAO rejected an API key: %s", self._hide(str(message)))
                self._rejected_until = self._clock() + LONG_WINDOW_RETRY_SECONDS
                raise InvalidKeyError
            if response.is_error:
                raise SauceNaoError(self._describe(response, data))
            if data is None:
                raise SauceNaoError(f"{outages.status(response)}: not JSON")
            self._track(data.get("header") or {})
            return data

    async def _send(self, image: tuple[bytes, str] | None, url: str | None, deadline: float) -> httpx.Response:
        """Send the search in the slot just taken; SauceNAO down (a 5xx) or out of reach is `UnavailableError`.

        Such a failure gets one retry, if it can go out before the deadline. The retry takes the
        failed request's slot: a request that never reached SauceNAO cost nothing there.
        """
        params = {"output_type": 2, "api_key": self._api_key, "db": 999, "numres": 5, "testmode": 1}
        retried = False
        while True:
            sent = self._clock()
            self._sent.append(sent)
            try:
                if image is not None:
                    content, filename = image
                    response = await self._client.post(SEARCH_URL, params=params, files={"file": (filename, content)})
                else:
                    response = await self._client.get(SEARCH_URL, params={**params, "url": url})
            except outages.UNREACHABLE as e:
                failure = self._hide(outages.describe(e))
            else:
                if not outages.is_down(response):
                    return response
                failure = self._describe(response, _json(response))
            if retried or self._clock() + outages.RETRY_SECONDS > deadline:
                raise UnavailableError(failure)
            logger.warning("SauceNAO did not answer (%s), retrying in %.0f s", failure, outages.RETRY_SECONDS)
            await self._sleep(outages.RETRY_SECONDS)
            # the key may have run out or been rejected meanwhile
            self._check()
            with contextlib.suppress(ValueError):
                self._sent.remove(sent)
            retried = True

    def _describe(self, response: httpx.Response, data: dict | None) -> str:
        """The status, with SauceNAO's own message if it sent one; never the page itself."""
        message = ((data or {}).get("header") or {}).get("message")
        return self._hide(f"{outages.status(response)}: {message}") if message else outages.status(response)

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
                    await self._sleep(min(ready - self._clock(), LINE_CHECK_SECONDS))
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
        self._note(header)
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
        self._note(header)
        if "daily" in str(header.get("message", "")).lower() or _as_int(header.get("long_remaining")) == 0:
            self._pause_daily()
            return True
        self._pause_short()
        return False

    def _note(self, header: dict) -> None:
        long_limit = _as_int(header.get("long_limit"))
        long_left = _as_int(header.get("long_remaining"))
        if long_limit and long_limit > 0 and long_left is not None:
            self._usage = (long_limit - max(long_left, 0), long_limit)

    def _pause_daily(self) -> None:
        # used up, even if the answer that says so carries no numbers
        if self._usage is not None:
            self._usage = (self._usage[1], self._usage[1])
        until = self._clock() + LONG_WINDOW_RETRY_SECONDS
        if until > self._daily_until:
            logger.info("SauceNAO daily limit reached, pausing searches for %.0f s", LONG_WINDOW_RETRY_SECONDS)
            self._daily_until = until

    def _pause_short(self) -> None:
        until = self._clock() + SHORT_WINDOW_SECONDS
        if until > self._short_until:
            logger.info("SauceNAO 30 second limit reached, searches wait for %.0f s", SHORT_WINDOW_SECONDS)
            self._short_until = until
