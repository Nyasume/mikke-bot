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
from mikke.reports import Outcome
from mikke.results import Scored, fallback_keyboard, hits, render, select
from mikke.saucenao import QuotaExceededError, SauceNao

logger = logging.getLogger(__name__)

CACHE_SIZE = 200
CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Answer:
    text: str
    # how the search went, for the owner report
    outcome: Outcome
    keyboard: InlineKeyboardMarkup | None = None

    @property
    def invalid_file(self) -> bool:
        """Telegram would not give us the file, so searching it elsewhere is pointless too."""
        return self.outcome.status == "invalid_file"


class InvalidFileError(Exception):
    """Telegram refused to give us the file (too big, gone, ...)."""


class DownloadError(Exception):
    """Downloading the file from Telegram failed."""


async def download(bot: Bot, media: Media) -> tuple[bytes, str]:
    """The image to search and its file name, fetched from Telegram."""
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
    return data.getvalue(), PurePosixPath(file.file_path).name


class Searcher:
    """Cache, then SauceNAO, then the answer."""

    def __init__(self, saucenao: SauceNao, public_url: str | None, cache: TTLCache | None = None) -> None:
        self._saucenao = saucenao
        self._public_url = public_url
        # accepted matches by file_unique_id or "url:<url>"; an empty list is a cached "no result"
        self._cache: TTLCache[str, list[Scored]] = (
            cache if cache is not None else TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        )

    def image_url(self, bot: Bot, file_id: str) -> str | None:
        """Public, token-free link to a Telegram file, served by our own /img/ route.

        A file_id only works with the bot that received the file, so the link names that bot.
        """
        return f"{self._public_url}/img/{bot.id}/{file_id}" if self._public_url else None

    async def search_file(self, bot: Bot, media: Media) -> Answer:
        async def query() -> list[dict]:
            # SauceNao downloads the file only once the search has its slot
            return await self._saucenao.search(upload=lambda: download(bot, media))

        return await self._search(media.file_unique_id, self.image_url(bot, media.file_id), query)

    async def search_url(self, url: str) -> Answer:
        return await self._search(f"url:{url}", url, lambda: self._saucenao.search(url=url))

    async def _search(self, key: str, image_url: str | None, query: Callable[[], Awaitable[list[dict]]]) -> Answer:
        scored = self._cache.get(key)
        cached = scored is not None
        if scored is None:
            try:
                scored = select(await query())
            except QuotaExceededError:
                return Answer(texts.LIMIT_REACHED, Outcome("limit"), fallback_keyboard(image_url))
            except InvalidFileError as e:
                logger.info("Invalid file %s: %s", key, e)
                return Answer(texts.INVALID_FILE, Outcome("invalid_file", error=str(e)))
            except Exception as e:
                logger.exception("Search failed for %s", key)
                failed = Outcome("error", error=f"{type(e).__name__}: {e}")
                return Answer(texts.ERROR, failed, fallback_keyboard(image_url))
            self._cache[key] = scored

        if not scored:
            return Answer(texts.NO_RESULT, Outcome("not_found", cached), fallback_keyboard(image_url))
        text, keyboard = render([data for _, data in scored])
        return Answer(text, Outcome("found", cached, hits(scored)), keyboard)
