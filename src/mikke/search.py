import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from cachetools import LRUCache, TTLCache

from mikke import texts
from mikke.keys import KeyStore, UserKey
from mikke.media import Media
from mikke.reports import KeyUse, Outcome
from mikke.results import Scored, fallback_keyboard, hits, render, select
from mikke.saucenao import InvalidKeyError, QuotaExceededError, SauceNao

logger = logging.getLogger(__name__)

CACHE_SIZE = 200
CACHE_TTL_SECONDS = 24 * 60 * 60
# users' keys whose limits are kept in memory
OWN_KEYS = 1000
# starts adding the user's own key, in private (apikey.py)
ADD_KEY_CALLBACK = "key:add"
ADD_KEY_BUTTON = InlineKeyboardButton(text=texts.ADD_KEY_BUTTON, callback_data=ADD_KEY_CALLBACK)


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


class KeyLocks:
    """A lock per cache key: searches of the same image run one after another, so the later
    ones find the first one's answer in the cache instead of spending the quota again.

    A key's lock exists only while someone holds it or waits for it.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._locks)

    @contextlib.asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] += 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if not self._users[key]:
                del self._users[key], self._locks[key]


@dataclass(frozen=True)
class Asker:
    """The user a search is for: their own SauceNAO key is used if they have one."""

    user_id: int
    # a private chat, where a limit answer offers them their own key
    private: bool = False


class Searcher:
    """Cache, then SauceNAO with the asker's own key or the shared one, then the answer."""

    def __init__(
        self,
        saucenao: SauceNao,
        public_url: str | None,
        keys: KeyStore | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        # the shared key
        self._saucenao = saucenao
        self._public_url = public_url
        self._keys = keys
        # users' keys, each with its own limits; only the recently used ones are kept
        self._own: LRUCache[str, SauceNao] = LRUCache(maxsize=OWN_KEYS)
        # accepted matches by file_unique_id or "url:<url>"; an empty list is a cached "no result"
        self._cache: TTLCache[str, list[Scored]] = (
            cache if cache is not None else TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        )
        self._locks = KeyLocks()

    def image_url(self, bot: Bot, file_id: str) -> str | None:
        """Public, token-free link to a Telegram file, served by our own /img/ route.

        A file_id only works with the bot that received the file, so the link names that bot.
        """
        return f"{self._public_url}/img/{bot.id}/{file_id}" if self._public_url else None

    def saucenao(self, api_key: str) -> SauceNao:
        """The client for a user's key."""
        client = self._own.get(api_key)
        if client is None:
            client = self._own[api_key] = self._saucenao.with_key(api_key)
        return client

    async def search_file(self, bot: Bot, media: Media, asker: Asker | None = None) -> Answer:
        """`asker` is None for automatic searches, which use the shared key."""
        image: tuple[bytes, str] | None = None

        async def upload() -> tuple[bytes, str]:
            # once, even if the shared key has to stand in for the user's
            nonlocal image
            if image is None:
                image = await download(bot, media)
            return image

        image_url = self.image_url(bot, media.file_id)
        return await self._search(media.file_unique_id, image_url, asker, lambda sauce: sauce.search(upload=upload))

    async def search_url(self, url: str, asker: Asker | None = None) -> Answer:
        return await self._search(f"url:{url}", url, asker, lambda sauce: sauce.search(url=url))

    async def _search(
        self,
        key: str,
        image_url: str | None,
        asker: Asker | None,
        query: Callable[[SauceNao], Awaitable[list[dict]]],
    ) -> Answer:
        # one at a time per image: a search that waited finds the answer cached, or,
        # if the first one failed, which is never cached, searches with its own asker's key
        async with self._locks.hold(key):
            return await self._lookup(key, image_url, asker, query)

    async def _lookup(
        self,
        key: str,
        image_url: str | None,
        asker: Asker | None,
        query: Callable[[SauceNao], Awaitable[list[dict]]],
    ) -> Answer:
        scored = self._cache.get(key)
        if scored is not None:
            return self._result(scored, image_url, asker, cached=True)
        own: KeyUse | None = None
        stored: UserKey | None = None
        try:
            results: list[dict] = []
            keys = self._keys
            stored = await keys.get(asker.user_id) if asker and keys else None
            if asker and keys and stored and stored.valid:
                own = "own"
                try:
                    results = await query(self.saucenao(stored.api_key))
                except QuotaExceededError as e:
                    # the key's own 30-second line is too long: the shared one would be no better
                    if not e.daily:
                        raise
                    own = "used_up"
                except InvalidKeyError:
                    # searches that were already in line with the key find it rejected too; one tells the user
                    own = "rejected" if await keys.invalidate(asker.user_id, stored.api_key) else None
            if own != "own":
                results = await query(self._saucenao)
            scored = select(results)
            # shown before it is cached: a result that cannot be shown is one error, not a day of them
            answer = self._result(scored, image_url, asker, own=own)
        except QuotaExceededError:
            return self._answer(texts.LIMIT_REACHED, Outcome("limit", key=own), fallback_keyboard(image_url), asker)
        except InvalidFileError as e:
            logger.info("Invalid file %s: %s", key, e)
            return self._answer(texts.INVALID_FILE, Outcome("invalid_file", error=str(e), key=own), None, asker)
        except Exception as e:
            logger.exception("Search failed for %s", key)
            error = f"{type(e).__name__}: {e}"
            if stored is not None:
                error = error.replace(stored.api_key, "[key]")
            failed = Outcome("error", error=error, key=own)
            return self._answer(texts.ERROR, failed, fallback_keyboard(image_url), asker)
        self._cache[key] = scored
        return answer

    def _result(
        self,
        scored: list[Scored],
        image_url: str | None,
        asker: Asker | None,
        *,
        cached: bool = False,
        own: KeyUse | None = None,
    ) -> Answer:
        """The answer for the accepted matches, or for none."""
        if not scored:
            outcome = Outcome("not_found", cached, key=own)
            return self._answer(texts.NO_RESULT, outcome, fallback_keyboard(image_url), asker)
        text, keyboard = render([data for _, data in scored])
        return self._answer(text, Outcome("found", cached, hits(scored), key=own), keyboard, asker)

    @staticmethod
    def _answer(text: str, outcome: Outcome, keyboard: InlineKeyboardMarkup | None, asker: Asker | None) -> Answer:
        """The answer, with what the asker should know about their own key.

        Once, that SauceNAO rejected their key; in private, after running out of
        the shared searches, that they can add a free key of their own.
        """
        offer = False
        if outcome.key == "rejected":
            text += "\n\n" + texts.KEY_REJECTED
            offer = asker is not None and asker.private
        elif outcome.status == "limit" and outcome.key is None and asker is not None and asker.private:
            text += "\n\n" + texts.KEY_PITCH
            offer = True
        if offer:
            rows = keyboard.inline_keyboard if keyboard else []
            keyboard = InlineKeyboardMarkup(inline_keyboard=[*rows, [ADD_KEY_BUTTON]])
        return Answer(text, outcome, keyboard)
