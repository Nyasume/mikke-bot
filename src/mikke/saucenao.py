import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://saucenao.com/search.php"

# The free account allows 4 searches per 30 seconds and 100 per day. How long to
# stop calling the API once a window is used up: the short one resets after 30 s,
# the daily one is rolling and frees up gradually, so probe it again now and then.
SHORT_WINDOW_SECONDS = 30.0
LONG_WINDOW_RETRY_SECONDS = 600.0
DEFAULT_SHORT_LIMIT = 4


class QuotaExceededError(Exception):
    """A SauceNAO search window is used up; the API was not called."""


class SauceNaoError(Exception):
    """SauceNAO answered with an error."""


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SauceNao:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._clock = clock
        self._blocked_until = 0.0
        # Send times within the short window. The response headers only describe
        # finished requests, so a burst of concurrent searches is capped locally.
        self._short_limit = DEFAULT_SHORT_LIMIT
        self._sent: deque[float] = deque()

    @property
    def exhausted(self) -> bool:
        now = self._clock()
        while self._sent and now - self._sent[0] >= SHORT_WINDOW_SECONDS:
            self._sent.popleft()
        return now < self._blocked_until or len(self._sent) >= self._short_limit

    def check_quota(self) -> None:
        if self.exhausted:
            raise QuotaExceededError

    async def search(self, *, image: bytes | None = None, filename: str = "image.jpg", url: str | None = None) -> list[dict]:
        """Search by uploaded image bytes or by a public URL; return the raw `results` list."""
        self.check_quota()
        self._sent.append(self._clock())
        params = {
            "output_type": 2,
            "api_key": self._api_key,
            "db": 999,
            "numres": 5,
            "testmode": 1,
        }
        if image is not None:
            response = await self._client.post(SEARCH_URL, params=params, files={"file": (filename, image)})
        elif url is not None:
            response = await self._client.get(SEARCH_URL, params={**params, "url": url})
        else:
            raise ValueError("either image or url is required")

        if response.status_code == 429:
            self._on_rate_limited(response)
            raise QuotaExceededError
        if response.is_error:
            raise SauceNaoError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as e:
            raise SauceNaoError(f"not JSON: {response.text[:300]}") from e

        header = data.get("header") or {}
        self._track(header)
        results = data.get("results") or []
        status = _as_int(header.get("status")) or 0
        if status != 0:
            logger.info("SauceNAO status %s: %s", status, header.get("message"))
        # status > 0 is a server-side failure; with partial results (some indexes
        # down) the results are still usable
        if status > 0 and not results:
            raise SauceNaoError(f"status {status}: {header.get('message')}")
        return results

    def _track(self, header: dict) -> None:
        if (short_limit := _as_int(header.get("short_limit"))) and short_limit > 0:
            self._short_limit = short_limit
        long_left = _as_int(header.get("long_remaining"))
        short_left = _as_int(header.get("short_remaining"))
        if long_left is not None and long_left <= 0:
            self._block(self._clock() + LONG_WINDOW_RETRY_SECONDS, "daily")
        elif short_left is not None and short_left <= 0:
            self._block(self._clock() + SHORT_WINDOW_SECONDS, "30 second")

    def _on_rate_limited(self, response: httpx.Response) -> None:
        header: dict = {}
        with contextlib.suppress(ValueError, AttributeError):
            header = response.json().get("header") or {}
        if "daily" in str(header.get("message", "")).lower() or _as_int(header.get("long_remaining")) == 0:
            self._block(self._clock() + LONG_WINDOW_RETRY_SECONDS, "daily")
        else:
            self._block(self._clock() + SHORT_WINDOW_SECONDS, "30 second")

    def _block(self, until: float, window: str) -> None:
        if until > self._blocked_until:
            logger.info("SauceNAO %s limit reached, pausing searches for %.0f s", window, until - self._clock())
            self._blocked_until = until
