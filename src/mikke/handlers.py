import asyncio
import logging
import re
from dataclasses import replace
from typing import Any

import sentry_sdk
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, Filter, or_f
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChosenInlineResult,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    ReplyParameters,
    User,
)

from mikke import texts
from mikke.flood import FloodMiddleware
from mikke.media import Media, find_media, normalize_url
from mikke.observability import is_expected
from mikke.reports import Reporter, Search
from mikke.scenes import Alert, SceneSearcher
from mikke.search import Answer, Searcher

logger = logging.getLogger(__name__)

KEYWORDS = re.compile(r"^(sauce|source|what\?)$", re.IGNORECASE)
INLINE_RESULT_ID = "url"
LOADING_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=texts.LOADING_BUTTON, callback_data="noop")]]
)
SCENE_CALLBACK = "scene"
SCENE_BUTTON = InlineKeyboardButton(text=texts.SCENE_BUTTON, callback_data=SCENE_CALLBACK)
# The trace.moe quota is 100 searches a day for the whole bot, so presses are limited harder than messages
SCENE_FLOOD_LIMIT = 5
SCENE_FLOOD_WINDOW = 60


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
    search.callback_query.middleware(FloodMiddleware(SCENE_FLOOD_LIMIT, SCENE_FLOOD_WINDOW))
    search.callback_query.register(on_scene_button, F.data == SCENE_CALLBACK)
    router.include_router(search)
    return router


async def send_help(message: Message) -> None:
    await message.answer(texts.HELP)


async def on_new_members(message: Message, bot: Bot) -> None:
    if any(member.id == bot.id for member in message.new_chat_members or []):
        await message.answer(texts.HELP)


async def on_trigger(message: Message, bot: Bot, searcher: Searcher, reporter: Reporter) -> None:
    """`/sauce`, `/source`, `sauce`, `source` or `what?` in reply to a media message."""
    target = message.reply_to_message
    media = find_media(target) if target else None
    if target is None or media is None:
        if (message.text or "").startswith("/"):
            await message.reply(texts.USAGE)
        return
    await _search_message(target, bot, searcher, reporter, media, asked_by=message)


async def search_message(message: Message, bot: Bot, searcher: Searcher, reporter: Reporter, media: Media) -> None:
    """Media in private, or a photo in a favourite group."""
    await _search_message(message, bot, searcher, reporter, media, asked_by=message)


async def _search_message(
    message: Message, bot: Bot, searcher: Searcher, reporter: Reporter, media: Media, *, asked_by: Message
) -> None:
    """Reply to `message` with the placeholder, edit it into the answer, then report the search to the owner.

    `asked_by` is the message that asked: `message` itself, or a /sauce in reply to it.
    """
    placeholder = await message.answer(
        texts.LOADING,
        reply_markup=LOADING_KEYBOARD,
        reply_parameters=ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True),
    )
    answer = await searcher.search_file(bot, media)
    if not answer.invalid_file:
        rows = answer.keyboard.inline_keyboard if answer.keyboard else []
        answer = replace(answer, keyboard=InlineKeyboardMarkup(inline_keyboard=[*rows, [SCENE_BUTTON]]))
    await _edit(bot, answer, chat_id=placeholder.chat.id, message_id=placeholder.message_id)
    search = Search(
        "SauceNAO",
        answer.outcome,
        answer.text,
        asked_by.from_user,
        chat=message.chat,
        message_id=message.message_id,
        sender_chat=asked_by.sender_chat,
        media=media,
        image_url=searcher.image_url(bot, media.file_id),
    )
    await reporter.search(bot, search)


async def on_scene_button(
    callback: CallbackQuery, bot: Bot, scenes: SceneSearcher, searcher: Searcher, reporter: Reporter
) -> None:
    """🎬 under an answer: look up the anime scene of the media that answer replies to."""
    answer_message = callback.message
    target = answer_message.reply_to_message if isinstance(answer_message, Message) else None
    media = find_media(target) if target else None
    if media is None:
        await _answer_callback(callback, texts.SCENE_NO_MEDIA)
        return
    result = await scenes.search(bot, media)
    if isinstance(result, Alert):
        await _answer_callback(callback, result.text)
    else:
        await answer_message.answer(
            result.text,
            reply_markup=result.keyboard,
            reply_parameters=ReplyParameters(message_id=target.message_id, allow_sending_without_reply=True),
        )
        await _answer_callback(callback)
    search = Search(
        "trace.moe",
        result.outcome,
        result.text,
        callback.from_user,
        chat=target.chat,
        message_id=target.message_id,
        media=media,
        image_url=searcher.image_url(bot, media.file_id),
    )
    await reporter.search(bot, search)


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


async def on_chosen_inline_result(result: ChosenInlineResult, bot: Bot, searcher: Searcher, reporter: Reporter) -> None:
    url = normalize_url(result.query)
    if url is None or result.inline_message_id is None:
        return
    answer = await searcher.search_url(url)
    await _edit(bot, answer, inline_message_id=result.inline_message_id)
    await reporter.search(bot, Search("SauceNAO", answer.outcome, answer.text, result.from_user, image_url=url))


async def on_error(
    event: ErrorEvent,
    bot: Bot,
    reporter: Reporter,
    event_from_user: User | None = None,
    event_chat: Chat | None = None,
) -> None:
    update, error = event.update, event.exception
    if is_expected(error):
        # the user blocked the bot or deleted a message it was answering: nothing to fix
        logger.warning("Update %s: %s", update.update_id, error)
        return
    with sentry_sdk.new_scope() as scope:
        # numeric ids only: names and texts stay out of Sentry
        scope.set_tag("update_type", update.event_type)
        scope.set_context("telegram", {"update_id": update.update_id, "chat_id": event_chat.id if event_chat else None})
        if event_from_user is not None:
            scope.set_user({"id": str(event_from_user.id)})
        # the Sentry logging integration makes this record the event, once
        logger.error("Update %s failed", update.update_id, exc_info=error)
    await reporter.error(bot, f"Update {update.update_id} failed\n{type(error).__name__}: {error}")


async def _answer_callback(callback: CallbackQuery, alert: str | None = None) -> None:
    """Stop the spinner on the pressed button, with a popup if `alert` is given."""
    try:
        await callback.answer(alert, show_alert=alert is not None)
    except TelegramBadRequest as e:
        # "query is too old": the search took longer than Telegram waits for the answer
        logger.info("Could not answer the button press: %s", e.message)


def _without_links(keyboard: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """The keyboard without its link rows, so the 🎬 button stays; None if nothing is left."""
    rows = [row for row in keyboard.inline_keyboard if not any(button.url for button in row)] if keyboard else []
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


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
            without_links = _without_links(keyboard)
            if without_links == keyboard:
                logger.warning("Could not edit the answer: %s", e.message)
                return
            # most likely a link Telegram does not accept as a button: show the text without the links
            logger.warning("Could not edit the answer with link buttons (%s), retrying without", e.message)
            keyboard = without_links
        except TelegramAPIError as e:
            logger.warning("Could not edit the answer: %s", e)
            return
