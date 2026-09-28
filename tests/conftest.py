"""Offline test harness: a fake Telegram session; SauceNAO and trace.moe are mocked with respx in the tests."""

import asyncio
import itertools
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetFile, GetMe, TelegramMethod
from aiogram.types import Chat, File, Message, User
from cachetools import TTLCache

from mikke.bot import build_bot, build_dispatcher
from mikke.config import Settings
from mikke.keys import KeyStore
from mikke.reports import Reporter
from mikke.saucenao import SauceNao
from mikke.scenes import SceneSearcher
from mikke.search import CACHE_SIZE, CACHE_TTL_SECONDS, Searcher
from mikke.tracemoe import TraceMoe
from payloads import ADMIN_ID, API_KEY, BOT_TOKEN, FAVOURITE_GROUP, IMAGE, PUBLIC_URL, USERNAMES, Clock


class FakeSession(BaseSession):
    """Records every Bot API call and answers with plausible objects. Each bot has its own.

    getMe is answered but not recorded: aiogram caches it, and run() asks for it before any update.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.file_paths: dict[str, str] = {}
        self.errors: dict[type, Exception] = {}
        self.fail_once: dict[type, Exception] = {}
        # every call to these chats fails, like writing to someone who never started the bot
        self.chat_errors: dict[int, Exception] = {}
        self.content = IMAGE
        self.streamed: list[str] = []
        # every call lets other tasks run first, like a real request, so concurrent searches interleave
        self.slow = False
        # every send_* call and the message it returned
        self.sent: list[tuple[TelegramMethod, Message]] = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:  # noqa: ASYNC109
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Mikke", username=USERNAMES[bot.id])
        self.requests.append(method)
        if self.slow:
            await asyncio.sleep(0)
        if (error := self.errors.get(type(method)) or self.fail_once.pop(type(method), None)) is not None:
            raise error
        if (error := self.chat_errors.get(getattr(method, "chat_id", None))) is not None:
            raise error
        if isinstance(method, GetFile):
            path = self.file_paths.get(method.file_id, f"photos/{method.file_id}.jpg")
            return File(file_id=method.file_id, file_unique_id=f"u-{method.file_id}", file_path=path)
        # send_*; edits name a message, inline ones have no chat
        if getattr(method, "chat_id", None) is not None and not getattr(method, "message_id", None):
            sent = Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None),
            )
            self.sent.append((method, sent))
            return sent
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        self.streamed.append(url)
        yield self.content[:4]
        yield self.content[4:]

    def calls[M: TelegramMethod](self, method_type: type[M]) -> list[M]:
        return [request for request in self.requests if isinstance(request, method_type)]


@dataclass
class Harness:
    settings: Settings
    bots: list[Bot]  # the primary bot first, each with its own FakeSession
    dp: Dispatcher
    searcher: Searcher
    saucenao: SauceNao
    scenes: SceneSearcher
    tracemoe: TraceMoe
    clock: Clock
    keys: KeyStore

    @property
    def bot(self) -> Bot:
        return self.bots[0]

    @property
    def session(self) -> FakeSession:
        return self.sessions[0]

    @property
    def sessions(self) -> list[FakeSession]:
        return [bot.session for bot in self.bots]

    async def feed(self, update: dict, bot: Bot | None = None) -> None:
        """Hand `update` to the dispatcher as received by `bot`, the primary bot by default."""
        await self.dp.feed_raw_update(bot or self.bot, update)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "bot_token": BOT_TOKEN,
        "saucenao_api_key": API_KEY,
        "admin_ids": [ADMIN_ID],
        "favourite_groups": [FAVOURITE_GROUP],
        "public_url": PUBLIC_URL,
        "bot_mode": "polling",
        "report_results": True,
        "report_errors": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
async def http() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def make_harness(http: httpx.AsyncClient, tmp_path):
    def factory(**overrides: Any) -> Harness:
        settings = make_settings(**{"data_dir": tmp_path} | overrides)
        bots = [build_bot(token, session=FakeSession()) for token in settings.bot_tokens()]
        clock = Clock()
        saucenao = SauceNao(http, settings.saucenao_api_key.get_secret_value(), clock=clock, sleep=clock.sleep)
        reporter = Reporter(settings.admin_ids, results=settings.report_results, errors=settings.report_errors)
        keys = KeyStore(settings.data_dir / "mikke.sqlite3")
        cache = TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS, timer=clock)
        searcher = Searcher(saucenao, settings.public_url, keys, cache=cache)
        tracemoe = TraceMoe(http, clock=clock)
        scene_cache = TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS, timer=clock)
        scenes = SceneSearcher(tracemoe, cache=scene_cache)
        dp = build_dispatcher(settings, searcher, scenes, reporter, keys)
        return Harness(settings, bots, dp, searcher, saucenao, scenes, tracemoe, clock, keys)

    return factory


@pytest.fixture
def harness(make_harness) -> Harness:
    return make_harness()
