"""Owner reports: found results with the searched image, and errors."""

import html
import logging
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from reverse_search_bot.media import Media

logger = logging.getLogger(__name__)


class Reporter:
    def __init__(self, admin_ids: list[int], *, results: bool, errors: bool) -> None:
        self._admin_ids = admin_ids
        self._results = results
        self._errors = errors

    async def result(
        self,
        bot: Bot,
        text: str,
        keyboard: InlineKeyboardMarkup | None,
        image_url: str | None,
        media: Media | None,
    ) -> None:
        if not self._results:
            return
        if image_url:
            link = "https://saucenao.com/search.php?" + urlencode({"url": image_url})
            text = f'<a href="{html.escape(link)}">SauceNAO</a>\n\n{text}'
        for admin_id in self._admin_ids:
            try:
                await bot.send_message(admin_id, text, reply_markup=keyboard)
                if media is not None:
                    await _resend(bot, admin_id, media)
            except TelegramAPIError as e:
                logger.warning("Could not report the result to %s: %s", admin_id, e)

    async def error(self, bot: Bot, description: str) -> None:
        if not self._errors:
            return
        text = f"⚠ <b>Error</b>\n<code>{html.escape(description[:3500])}</code>"
        for admin_id in self._admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except TelegramAPIError as e:
                logger.warning("Could not report the error to %s: %s", admin_id, e)


async def _resend(bot: Bot, chat_id: int, media: Media) -> None:
    match media.kind:
        case "photo":
            await bot.send_photo(chat_id, media.original_file_id)
        case "sticker":
            await bot.send_sticker(chat_id, media.original_file_id)
        case "document":
            await bot.send_document(chat_id, media.original_file_id)
        case "video":
            await bot.send_video(chat_id, media.original_file_id)
