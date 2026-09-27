import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup
from cachetools import TTLCache

from mikke import texts
from mikke.media import Media
from mikke.reports import Reporter
from mikke.results import Match, fallback_keyboard, render, select
from mikke.saucenao import QuotaExceededError, SauceNao

logger = logging.getLogger(__name__)

CACHE_SIZE = 200
CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Answer:
    text: str
    keyboard: InlineKeyboardMarkup | None = None


class InvalidFileError(Exception):
    """Telegram refused to give us the file (too big, gone, ...)."""


class DownloadError(Exception):
    """Downloading the file from Telegram failed."""


class Searcher:
    """Cache, then SauceNAO, then the answer; reports go to the owner on the way."""

    def __init__(
        self,
        saucenao: SauceNao,
        reporter: Reporter,
        public_url: str | None,
        cache: TTLCache | None = None,
    ) -> None:
        self._saucenao = saucenao
        self._reporter = reporter
        self._public_url = public_url
        # accepted matches by file_unique_id or "url:<url>"; an empty list is a cached "no result"
        self._cache: TTLCache[str, list[Match]] = (
            cache if cache is not None else TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        )

    def image_url(self, file_id: str) -> str | None:
        """Public, token-free link to a Telegram file, served by our own /img/ route."""
        return f"{self._public_url}/img/{file_id}" if self._public_url else None

    async def search_file(self, bot: Bot, media: Media) -> Answer:
        async def query() -> list[dict]:
            # no point in downloading the file while the API is off limits
            self._saucenao.check_quota()
            try:
                file = await bot.get_file(media.file_id)
                if not file.file_path:
                    raise InvalidFileError("no file_path")
                data = await bot.download_file(file.file_path)
            except TelegramBadRequest as e:
                raise InvalidFileError(e.message) from e
            except (aiohttp.ClientError, TimeoutError) as e:
                # the aiohttp error text holds the file URL, bot token included: drop it
                status = getattr(e, "status", None)
                raise DownloadError(f"{type(e).__name__}{f' HTTP {status}' if status else ''}") from None
            return await self._saucenao.search(image=data.getvalue(), filename=PurePosixPath(file.file_path).name)

        return await self._search(bot, media.file_unique_id, self.image_url(media.file_id), media, query)

    async def search_url(self, bot: Bot, url: str) -> Answer:
        return await self._search(bot, f"url:{url}", url, None, lambda: self._saucenao.search(url=url))

    async def _search(
        self,
        bot: Bot,
        key: str,
        image_url: str | None,
        media: Media | None,
        query: Callable[[], Awaitable[list[dict]]],
    ) -> Answer:
        matches = self._cache.get(key)
        if matches is None:
            try:
                matches = select(await query())
            except QuotaExceededError:
                return Answer(texts.LIMIT_REACHED, fallback_keyboard(image_url))
            except InvalidFileError as e:
                logger.info("Invalid file %s: %s", key, e)
                return Answer(texts.INVALID_FILE)
            except Exception as e:
                logger.exception("Search failed for %s", key)
                await self._reporter.error(bot, f"Search failed for {image_url or key}\n{type(e).__name__}: {e}")
                return Answer(texts.ERROR, fallback_keyboard(image_url))
            self._cache[key] = matches

        if not matches:
            return Answer(texts.NO_RESULT, fallback_keyboard(image_url))
        text, keyboard = render(matches)
        await self._reporter.result(bot, text, keyboard, image_url, media)
        return Answer(text, keyboard)
