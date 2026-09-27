from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode

from reverse_search_bot.config import Settings
from reverse_search_bot.handlers import build_router, on_error
from reverse_search_bot.reports import Reporter
from reverse_search_bot.search import Searcher


def build_bot(settings: Settings, session: BaseSession | None = None) -> Bot:
    return Bot(
        settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def build_dispatcher(settings: Settings, searcher: Searcher, reporter: Reporter) -> Dispatcher:
    dp = Dispatcher(searcher=searcher, reporter=reporter)
    dp.include_router(build_router(settings.favourite_groups))
    dp.errors.register(on_error)
    return dp
