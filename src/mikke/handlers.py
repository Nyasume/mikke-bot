import asyncio
import logging
import re
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, Filter, or_f
from aiogram.types import (
    ChosenInlineResult,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    ReplyParameters,
)

from mikke import texts
from mikke.flood import FloodMiddleware
from mikke.media import Media, find_media, normalize_url
from mikke.reports import Reporter
from mikke.search import Answer, Searcher

logger = logging.getLogger(__name__)

KEYWORDS = re.compile(r"^(sauce|source|what\?)$", re.IGNORECASE)
INLINE_RESULT_ID = "url"
LOADING_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=texts.LOADING_BUTTON, callback_data="noop")]]
)


class HasMedia(Filter):
    """Passes messages with searchable media and hands it to the handler as `media`."""

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        media = find_media(message)
        return {"media": media} if media else False


def build_router(favourite_groups: list[int]) -> Router:
    router = Router(name="main")
    router.message.register(send_help, CommandStart())
    router.message.register(send_help, Command("help"))
    router.message.register(on_new_members, F.new_chat_members)
    router.inline_query.register(on_inline_query)
    router.chosen_inline_result.register(on_chosen_inline_result, F.result_id == INLINE_RESULT_ID)

    # Everything that starts a search; the flood check counts only these messages
    search = Router(name="search")
    search.message.middleware(FloodMiddleware())
    search.message.register(on_trigger, F.text, or_f(Command("sauce", "source"), F.text.regexp(KEYWORDS)))
    search.message.register(search_message, F.chat.type == ChatType.PRIVATE, HasMedia())
    if favourite_groups:
        search.message.register(search_message, F.chat.id.in_(set(favourite_groups)), F.photo, HasMedia())
    router.include_router(search)
    return router


async def send_help(message: Message) -> None:
    await message.answer(texts.HELP)


async def on_new_members(message: Message, bot: Bot) -> None:
    if any(member.id == bot.id for member in message.new_chat_members or []):
        await message.answer(texts.HELP)


async def on_trigger(message: Message, bot: Bot, searcher: Searcher) -> None:
    """`/sauce`, `/source`, `sauce`, `source` or `what?` in reply to a media message."""
    target = message.reply_to_message
    media = find_media(target) if target else None
    if target is None or media is None:
        if (message.text or "").startswith("/"):
            await message.reply(texts.USAGE)
        return
    await search_message(target, bot, searcher, media)


async def search_message(message: Message, bot: Bot, searcher: Searcher, media: Media) -> None:
    """Reply to `message` with the placeholder, then edit it into the answer."""
    placeholder = await message.answer(
        texts.LOADING,
        reply_markup=LOADING_KEYBOARD,
        reply_parameters=ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True),
    )
    answer = await searcher.search_file(bot, media)
    await _edit(bot, answer, chat_id=placeholder.chat.id, message_id=placeholder.message_id)


async def on_inline_query(query: InlineQuery) -> None:
    url = normalize_url(query.query)
    results = []
    if url:
        results.append(
            InlineQueryResultArticle(
                id=INLINE_RESULT_ID,
                title=texts.INLINE_TITLE,
                description=url,
                input_message_content=InputTextMessageContent(message_text=texts.LOADING),
                # without a keyboard Telegram gives no inline_message_id to edit later
                reply_markup=LOADING_KEYBOARD,
            )
        )
    await query.answer(results)


async def on_chosen_inline_result(result: ChosenInlineResult, bot: Bot, searcher: Searcher) -> None:
    url = normalize_url(result.query)
    if url is None or result.inline_message_id is None:
        return
    answer = await searcher.search_url(bot, url)
    await _edit(bot, answer, inline_message_id=result.inline_message_id)


async def on_error(event: ErrorEvent, bot: Bot, reporter: Reporter) -> None:
    logger.error("Update %s failed", event.update.update_id, exc_info=event.exception)
    await reporter.error(
        bot, f"Update {event.update.update_id} failed\n{type(event.exception).__name__}: {event.exception}"
    )


async def _edit(bot: Bot, answer: Answer, **target: Any) -> None:
    """Edit the placeholder into the answer; if that fails, it stays on "Mikke is looking..." for good."""
    keyboard = answer.keyboard
    for _attempt in range(2):
        try:
            await bot.edit_message_text(text=answer.text, reply_markup=keyboard, **target)
            return
        except TelegramRetryAfter as e:
            logger.warning("Editing the answer is rate limited, retrying in %s s", e.retry_after)
            await asyncio.sleep(min(e.retry_after, 60))
        except TelegramBadRequest as e:
            if keyboard is None:
                logger.warning("Could not edit the answer: %s", e.message)
                return
            # most likely a link Telegram does not accept as a button: show the text alone
            logger.warning("Could not edit the answer with buttons (%s), retrying without", e.message)
            keyboard = None
        except TelegramAPIError as e:
            logger.warning("Could not edit the answer: %s", e)
            return
