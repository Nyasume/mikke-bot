"""Owner reports: one activity report per search, and errors.

Reports go through the bot that handled the update, since the file_ids they
use only work with that bot. An admin gets them only from bots they started.

An activity report is a rich message (sendRichMessage) with the searched image
embedded: a photo by its file_id, anything else (static stickers, image files,
the thumbnails searched for GIFs, videos and animated stickers) by its public,
token-free /img/ link, which Telegram fetches from us. If Telegram rejects the
rich message, the same report goes out as a plain HTML message with the
original media resent as a reply to it.

Users' own SauceNAO keys are reported as events (added, removed, refused at
/apikey, rejected during a search), with who and never the key.
"""

import html
import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import Chat, InputMediaPhoto, InputRichMessage, InputRichMessageMedia, ReplyParameters, User

from mikke.media import Media
from mikke.observability import redact
from mikke.saucenao import Account

logger = logging.getLogger(__name__)

Engine = Literal["SauceNAO", "trace.moe"]
Status = Literal["found", "not_found", "limit", "error", "invalid_file"]
# The asker's own SauceNAO key: it answered, or it was used up for the day or rejected and the shared key stood in
KeyUse = Literal["own", "used_up", "rejected"]
KeyEvent = Literal["added", "removed", "rejected", "refused"]

HEADINGS: dict[Status, str] = {
    "found": "✅ {}: found",
    "not_found": "🤷 {}: nothing found",
    "limit": "⏳ {}: quota used up",
    "error": "⚠ {}: error",
    "invalid_file": "🚫 {}: Telegram would not give the file",
}
KEY_NOTES: dict[KeyUse, str] = {
    "own": "🔑 own key",
    "used_up": "🔑 own key used up, shared key",
    "rejected": "🔑 own key rejected, shared key",
}
KEY_EVENTS: dict[KeyEvent, str] = {
    "added": "🔑 SauceNAO key added",
    "removed": "🔑 SauceNAO key removed",
    # during a search: the shared key stood in, and the user was told
    "rejected": "🔑 SauceNAO rejected a saved key",
    # at /apikey: not saved
    "refused": "🔑 SauceNAO refused a new key",
}
# The engine's own page for an image URL, so the owner can look again
ENGINE_PAGES: dict[Engine, str] = {"SauceNAO": "https://saucenao.com/search.php?", "trace.moe": "https://trace.moe/?"}
# The searched image in a rich report: <img src="tg://photo?id=searched"/>
IMAGE_ID = "searched"
MAX_ERROR_LENGTH = 1000


@dataclass(frozen=True)
class Hit:
    """A match the user was shown."""

    title: str | None
    similarity: float  # percent
    links: tuple[tuple[str, str], ...] = ()  # (site, url)


@dataclass(frozen=True)
class Outcome:
    """How a search went."""

    status: Status
    cached: bool = False
    hits: tuple[Hit, ...] = ()
    # "error": the exception; "invalid_file": why Telegram would not give the file
    error: str | None = None
    key: KeyUse | None = None


@dataclass(frozen=True)
class Search:
    """One search, for its activity report: what was searched, by whom, where, and how it went."""

    engine: Engine
    outcome: Outcome
    answer: str  # the text the user got (HTML)
    user: User | None
    # the chat and the searched message; no chat in inline mode
    chat: Chat | None = None
    message_id: int | None = None
    # a channel or an anonymous admin that asked; `user` is then Telegram's stand-in bot
    sender_chat: Chat | None = None
    media: Media | None = None  # None in inline mode
    # a public, token-free link to the searched image: /img/<bot id>/<file_id>, or the URL searched inline
    image_url: str | None = None


@dataclass(frozen=True)
class _Report:
    """An activity report's content, as inline HTML lines both formats accept."""

    heading: str
    quote: list[str]  # the answer the user got, or the error
    hits: list[str]
    details: list[str]  # who asked and where
    footer: str

    def rich(self) -> str:
        """Rich HTML (sendRichMessage) with the embedded image on top."""
        blocks = [f'<img src="tg://photo?id={IMAGE_ID}"/>', f"<h3>{self.heading}</h3>"]
        if self.quote:
            blocks.append("<blockquote>" + "".join(f"<p>{line}</p>" for line in self.quote) + "</blockquote>")
        if self.hits:
            blocks.append("<ul>" + "".join(f"<li>{line}</li>" for line in self.hits) + "</ul>")
        blocks += [f"<p>{line}</p>" for line in self.details]
        blocks.append(f"<footer>{self.footer}</footer>")
        return "".join(blocks)

    def plain(self) -> str:
        """Telegram HTML (sendMessage), for when the rich message is rejected."""
        lines = [f"<b>{self.heading}</b>"]
        if self.quote:
            lines.append("<blockquote>" + "\n".join(self.quote) + "</blockquote>")
        lines += [f"• {line}" for line in self.hits]
        lines += [*self.details, self.footer]
        return "\n".join(lines)


class Reporter:
    def __init__(self, admin_ids: list[int], *, results: bool, errors: bool) -> None:
        self._admin_ids = admin_ids
        self._results = results
        self._errors = errors
        # (bot id, admin id) pairs already warned about: that admin never started that bot, or blocked it
        self._unreachable: set[tuple[int, int]] = set()

    async def search(self, bot: Bot, search: Search) -> None:
        """The activity report of one search; with those off, a failed search is reported as an error.

        Called once the user has the answer, and never raises: reports are best-effort.
        """
        try:
            if self._results:
                await self._activity(bot, search)
            elif search.outcome.status == "error":
                subject = search.image_url or (search.media.file_unique_id if search.media else "?")
                await self.error(bot, f"{search.engine} search failed for {subject}\n{search.outcome.error}")
        except Exception:
            logger.exception("Could not report a %s search", search.engine)
        if search.outcome.key == "rejected":
            await self.key(bot, search.user, "rejected")

    async def key(self, bot: Bot, user: User | None, event: KeyEvent, account: Account | None = None) -> None:
        """A user's own SauceNAO key was added, removed or rejected: who, never the key. Never raises."""
        if not (self._results and self._admin_ids):
            return
        try:
            lines = [f"<b>{KEY_EVENTS[event]}</b>"]
            if account is not None and (summary := _account(account)):
                lines.append(summary)
            lines += [_who(user, None), f"via @{(await bot.me()).username}"]
            for admin_id in self._admin_ids:
                try:
                    await bot.send_message(admin_id, "\n".join(lines), disable_notification=True)
                except TelegramAPIError as e:
                    await self._failed(bot, admin_id, "key event", e)
        except Exception:
            logger.exception("Could not report a key event")

    async def error(self, bot: Bot, description: str) -> None:
        if not self._errors:
            return
        description = _hide_token(bot, description)
        text = f"⚠ <b>Error</b>\n<code>{html.escape(description[:3500])}</code>"
        for admin_id in self._admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except TelegramAPIError as e:
                await self._failed(bot, admin_id, "error", e)

    async def _activity(self, bot: Bot, search: Search) -> None:
        if not self._admin_ids:
            return
        report = _render(search, (await bot.me()).username or str(bot.id), bot)
        image = _image(search)
        for admin_id in self._admin_ids:
            try:
                await _send(bot, admin_id, report, image, search.media)
            except TelegramAPIError as e:
                await self._failed(bot, admin_id, "search", e)

    async def _failed(self, bot: Bot, admin_id: int, what: str, error: TelegramAPIError) -> None:
        if not isinstance(error, TelegramForbiddenError):
            logger.warning("Could not report the %s to %s: %s", what, admin_id, error)
            return
        # once, not on every report: a bot the admin never started cannot write to them at all
        if (bot.id, admin_id) in self._unreachable:
            return
        self._unreachable.add((bot.id, admin_id))
        username = (await bot.me()).username
        logger.warning("Admin %s gets no reports from @%s until they start it: %s", admin_id, username, error)


async def _send(bot: Bot, chat_id: int, report: _Report, image: InputMediaPhoto | None, media: Media | None) -> None:
    """The rich report, or the plain one if there is no image to embed or Telegram rejects it.

    Sent silently: there is one for every search.
    """
    if image is not None:
        rich = InputRichMessage(html=report.rich(), media=[InputRichMessageMedia(id=IMAGE_ID, media=image)])
        try:
            await bot.send_rich_message(chat_id, rich, disable_notification=True)
            return
        except TelegramForbiddenError:
            raise
        except TelegramAPIError as e:
            # the rich HTML, or an image Telegram could not fetch from /img/ or take as a photo
            logger.warning("Could not send a rich report to %s, sending a plain one: %s", chat_id, e)
    sent = await bot.send_message(chat_id, report.plain(), disable_notification=True)
    if media is not None:
        await _resend(bot, chat_id, media, sent.message_id)


def _image(search: Search) -> InputMediaPhoto | None:
    """The searched image to embed: a photo by its file_id, anything else by its public link."""
    if search.media is not None and search.media.kind == "photo":
        return InputMediaPhoto(media=search.media.file_id)
    # /img/ cannot serve a file Telegram would not give us either
    if search.image_url and search.outcome.status != "invalid_file":
        return InputMediaPhoto(media=search.image_url)
    return None


def _render(search: Search, username: str, bot: Bot) -> _Report:
    outcome = search.outcome
    quote: list[str] = []
    if outcome.status == "found":
        quote = search.answer.split("\n")
    elif outcome.error:
        quote = [f"<code>{html.escape(_hide_token(bot, outcome.error)[:MAX_ERROR_LENGTH])}</code>"]

    footer = [f"via @{username}"]
    if outcome.cached:
        footer.append("💾 from the cache")
    if outcome.key is not None:
        footer.append(KEY_NOTES[outcome.key])
    if search.image_url:
        page = ENGINE_PAGES[search.engine] + urlencode({"url": search.image_url})
        footer.append(_link(page, search.engine))
    return _Report(
        heading=HEADINGS[outcome.status].format(search.engine),
        quote=quote,
        hits=[_hit(hit) for hit in outcome.hits],
        details=[_who(search.user, search.sender_chat), _where(search)],
        footer=" · ".join(footer),
    )


def _hit(hit: Hit) -> str:
    parts = [f"{hit.similarity:.1f}%"]
    if hit.title:
        parts.append(f"<b>{html.escape(hit.title, quote=False)}</b>")
    line = " ".join(parts)
    return " · ".join([line, *(_link(url, site) for site, url in hit.links)])


def _account(account: Account) -> str:
    parts = [account.plan] if account.plan else []
    if account.short_limit is not None:
        parts.append(f"{account.short_limit} per 30 s")
    if account.long_limit is not None:
        parts.append(f"{account.long_limit} a day")
    if account.long_remaining is not None:
        parts.append(f"{account.long_remaining} left today")
    return " · ".join(parts)


def _who(user: User | None, sender_chat: Chat | None) -> str:
    parts = []
    if user is not None:
        parts.append(_link(f"tg://user?id={user.id}", user.full_name))
        if user.username:
            parts.append(f"@{user.username}")
        parts.append(f"<code>{user.id}</code>")
    if sender_chat is not None:
        parts.append(f"as {_chat_title(sender_chat)}")
    return "👤 " + (" · ".join(parts) or "unknown")


def _where(search: Search) -> str:
    chat = search.chat
    if chat is None:
        image = f" · {_link(search.image_url, 'image URL')}" if search.image_url else ""
        return f"📥 inline mode{image}"
    if chat.type == ChatType.PRIVATE:
        return "💬 private chat"
    parts = [_chat_title(chat), f"<code>{chat.id}</code>"]
    if link := _message_link(chat, search.message_id):
        parts.append(_link(link, "message"))
    return "💬 " + " · ".join(parts)


def _chat_title(chat: Chat) -> str:
    if chat.username:
        return _link(f"https://t.me/{chat.username}", chat.full_name)
    return f"<b>{html.escape(chat.full_name, quote=False)}</b>"


def _message_link(chat: Chat, message_id: int | None) -> str | None:
    """t.me link to a group or channel message: public by username, else for members (supergroups only)."""
    if message_id is None:
        return None
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    # supergroup and channel ids are -100<id>; basic groups have no message links
    if str(chat.id).startswith("-100"):
        return f"https://t.me/c/{str(chat.id)[4:]}/{message_id}"
    return None


def _link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text, quote=False)}</a>'


def _hide_token(bot: Bot, text: str) -> str:
    # exception texts from aiohttp can contain Telegram file URLs, which carry the token
    return redact(text.replace(bot.token, "<token>"))


async def _resend(bot: Bot, chat_id: int, media: Media, reply_to: int) -> None:
    """The original media, since Telegram cannot resend thumbnails: the plain report's picture."""
    reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    match media.kind:
        case "photo":
            await bot.send_photo(chat_id, media.original_file_id, reply_parameters=reply, disable_notification=True)
        case "sticker":
            await bot.send_sticker(chat_id, media.original_file_id, reply_parameters=reply, disable_notification=True)
        case "document":
            await bot.send_document(chat_id, media.original_file_id, reply_parameters=reply, disable_notification=True)
        case "video":
            await bot.send_video(chat_id, media.original_file_id, reply_parameters=reply, disable_notification=True)
