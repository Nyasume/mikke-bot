import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.trace.moe/search"

# Guests get 100 searches per rolling 24 hours and one search at a time. Once the
# quota is used up, stop calling the API and probe it again now and then.
QUOTA_RETRY_SECONDS = 600.0


class QuotaExceededError(Exception):
    """The trace.moe search quota is used up; the API was not called or refused the search."""


class TraceMoeError(Exception):
    """trace.moe answered with an error."""


class TraceMoe:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        # a sponsor's key raises the quota; without it the bot searches as a guest by IP
        self._headers = {"x-trace-key": api_key} if api_key else {}
        self._clock = clock
        self._blocked_until = 0.0
        # A second search sent while one is running fails with "Concurrency limit exceeded"
        self._lock = asyncio.Lock()

    @property
    def exhausted(self) -> bool:
        return self._clock() < self._blocked_until

    def check_quota(self) -> None:
        if self.exhausted:
            raise QuotaExceededError

    async def search(self, image: bytes, filename: str = "image.jpg") -> list[dict]:
        """Search by uploaded image bytes, best match first, with AniList titles and MAL ids."""
        async with self._lock:
            # the quota may have run out while this search was waiting for its turn
            self.check_quota()
            response = await self._client.post(
                SEARCH_URL,
                params={"anilistInfo": "", "cutBorders": ""},
                files={"image": (filename, image)},
                headers=self._headers,
            )

        data: object = None
        with contextlib.suppress(ValueError):
            data = response.json()
        error = str(data.get("error") or "") if isinstance(data, dict) else ""
        # 402 is also "Concurrency limit exceeded", which is not worth a pause
        if response.status_code == 402 and "quota" in error.lower():
            logger.info("trace.moe quota used up, pausing scene searches for %.0f s", QUOTA_RETRY_SECONDS)
            self._blocked_until = self._clock() + QUOTA_RETRY_SECONDS
            raise QuotaExceededError
        if response.is_error or error:
            raise TraceMoeError(f"HTTP {response.status_code}: {error or response.text[:300]}")
        if not isinstance(data, dict):
            raise TraceMoeError(f"not JSON: {response.text[:300]}")
        return data.get("result") or []
