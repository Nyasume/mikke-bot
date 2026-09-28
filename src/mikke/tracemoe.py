import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from mikke import outages

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.trace.moe/search"

# Guests get 100 searches per rolling 24 hours and one search at a time. Once the
# quota is used up, stop calling the API and probe it again now and then.
QUOTA_RETRY_SECONDS = 600.0

# The image to upload and its file name, fetched only once the search has its turn
Upload = Callable[[], Awaitable[tuple[bytes, str]]]


class QuotaExceededError(Exception):
    """The trace.moe search quota is used up; the API was not called or refused the search."""


class TraceMoeError(Exception):
    """trace.moe answered with an error."""


class UnavailableError(TraceMoeError):
    """trace.moe did not answer, also on the retry: down, overloaded or out of reach. Not our bug."""


class TraceMoe:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        # a sponsor's key raises the quota; without it the bot searches as a guest by IP
        self._headers = {"x-trace-key": api_key} if api_key else {}
        self._clock = clock
        self._sleep = sleep
        self._blocked_until = 0.0
        # A second search sent while one is running fails with "Concurrency limit exceeded"
        self._lock = asyncio.Lock()
        # (used, limit) of the quota as trace.moe last reported it, for the owner reports
        self._usage: tuple[int, int] | None = None

    @property
    def sponsored(self) -> bool:
        """Searching with a sponsor's key rather than as a guest."""
        return bool(self._headers)

    @property
    def usage(self) -> tuple[int, int] | None:
        """How much of the quota trace.moe last said was used: (used, limit); None before its first answer."""
        return self._usage

    @property
    def exhausted(self) -> bool:
        return self._clock() < self._blocked_until

    def check_quota(self) -> None:
        if self.exhausted:
            raise QuotaExceededError

    async def search(self, upload: Upload) -> list[dict]:
        """Search by an uploaded image, best match first, with AniList titles and MAL ids.

        `upload` runs once this search has its turn: searches waiting in line hold no image,
        and fetch none once the quota has run out.
        """
        async with self._lock:
            # the quota may have run out while this search was waiting for its turn
            self.check_quota()
            response = await self._send(*await upload())

        data: object = None
        with contextlib.suppress(ValueError):
            data = response.json()
        error = str(data.get("error") or "") if isinstance(data, dict) else ""
        if isinstance(data, dict):
            self._track(data, searched=not (response.is_error or error))
        # 402 is also "Concurrency limit exceeded", which is not worth a pause
        if response.status_code == 402 and "quota" in error.lower():
            logger.info("trace.moe quota used up, pausing scene searches for %.0f s", QUOTA_RETRY_SECONDS)
            self._blocked_until = self._clock() + QUOTA_RETRY_SECONDS
            raise QuotaExceededError
        # the status, with trace.moe's own message if it sent one; never the page itself
        failure = f"{outages.status(response)}: {error}" if error else outages.status(response)
        if outages.is_down(response):
            raise UnavailableError(failure)
        if response.is_error or error:
            raise TraceMoeError(failure)
        if not isinstance(data, dict):
            raise TraceMoeError(f"{outages.status(response)}: not JSON")
        return data.get("result") or []

    async def _send(self, image: bytes, filename: str) -> httpx.Response:
        """Send the search, and once more if trace.moe is down (a 5xx) or out of reach.

        The retry goes out in this search's turn, with the same image; not after a timeout that took
        the whole minute, which held the line long enough. Out of reach at the end is `UnavailableError`.
        """
        retried = False
        while True:
            try:
                response = await self._client.post(
                    SEARCH_URL,
                    params={"anilistInfo": "", "cutBorders": ""},
                    files={"image": (filename, image)},
                    headers=self._headers,
                )
            except outages.UNREACHABLE as e:
                failure = outages.describe(e)
                if retried or isinstance(e, outages.SLOW):
                    raise UnavailableError(failure) from None
            else:
                if retried or not outages.is_down(response):
                    return response
                failure = outages.status(response)
            logger.warning("trace.moe did not answer (%s), retrying in %.0f s", failure, outages.RETRY_SECONDS)
            await self._sleep(outages.RETRY_SECONDS)
            retried = True

    def _track(self, data: dict, *, searched: bool) -> None:
        # results and "Search quota depleted" carry it; quotaUsed does not count the search it answers yet
        quota, used = data.get("quota"), data.get("quotaUsed")
        if isinstance(quota, int) and isinstance(used, int) and quota > 0:
            self._usage = (used + 1 if searched else used, quota)
