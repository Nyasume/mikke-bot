"""Offline test harness: a fake Telegram session; SauceNAO and trace.moe are mocked with respx in the tests."""

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
from mikke.reports import Reporter
from mikke.saucenao import SauceNao
from mikke.scenes import SceneSearcher
from mikke.search import CACHE_SIZE, CACHE_TTL_SECONDS, Searcher
from mikke.tracemoe import TraceMoe
from payloads import ADMIN_ID, API_KEY, BOT_TOKEN, BOT_USERNAME, FAVOURITE_GROUP, IMAGE, PUBLIC_URL, Clock


class FakeSession(BaseSession):
    """Records every Bot API call and answers with plausible objects."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.file_paths: dict[str, str] = {}
        self.errors: dict[type, Exception] = {}
        self.fail_once: dict[type, Exception] = {}
        self.content = IMAGE
        self.streamed: list[str] = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:  # noqa: ASYNC109
        self.requests.append(method)
        if (error := self.errors.get(type(method)) or self.fail_once.pop(type(method), None)) is not None:
            raise error
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Mikke", username=BOT_USERNAME)
        if isinstance(method, GetFile):
            path = self.file_paths.get(method.file_id, f"photos/{method.file_id}.jpg")
            return File(file_id=method.file_id, file_unique_id=f"u-{method.file_id}", file_path=path)
        if hasattr(method, "chat_id") and not getattr(method, "message_id", None):  # send_*
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None),
            )
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
    session: FakeSession
    bot: Bot
    dp: Dispatcher
    searcher: Searcher
    saucenao: SauceNao
    scenes: SceneSearcher
    tracemoe: TraceMoe
    clock: Clock

    async def feed(self, update: dict) -> None:
        await self.dp.feed_raw_update(self.bot, update)


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
def make_harness(http: httpx.AsyncClient):
    def factory(**overrides: Any) -> Harness:
        settings = make_settings(**overrides)
        session = FakeSession()
        bot = build_bot(settings, session=session)
        clock = Clock()
        saucenao = SauceNao(http, settings.saucenao_api_key.get_secret_value(), clock=clock)
        reporter = Reporter(settings.admin_ids, results=settings.report_results, errors=settings.report_errors)
        cache = TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS, timer=clock)
        searcher = Searcher(saucenao, reporter, settings.public_url, cache=cache)
        tracemoe = TraceMoe(http, clock=clock)
        scene_cache = TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS, timer=clock)
        scenes = SceneSearcher(tracemoe, reporter, cache=scene_cache)
        dp = build_dispatcher(settings, searcher, scenes, reporter)
        return Harness(settings, session, bot, dp, searcher, saucenao, scenes, tracemoe, clock)

    return factory


@pytest.fixture
def harness(make_harness) -> Harness:
    return make_harness()
