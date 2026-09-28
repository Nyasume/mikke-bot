"""Users' own SauceNAO keys: /apikey, a bare key sent in private, and the buttons that lead there.

A key is checked with one real SauceNAO search before it is saved. It never
goes to the logs or the owner reports, and in a group it is never saved:
Mikke deletes the message if she may.
"""

import contextlib
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, invert_f
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from mikke import texts
from mikke.flood import FloodMiddleware
from mikke.keys import KeyStore, UserKey
from mikke.observability import redact
from mikke.reports import Reporter
from mikke.saucenao import Account, InvalidKeyError, QuotaExceededError, UnavailableError
from mikke.search import ADD_KEY_CALLBACK, KeyLocks, Searcher

logger = logging.getLogger(__name__)

# What SauceNAO hands out: a private message that is just this is taken as a key
BARE_KEY = re.compile(r"\s*[0-9a-f]{40}\s*")
# After /apikey, anything shaped like a key goes to SauceNAO to decide
KEY_ARGUMENT = re.compile(r"[A-Za-z0-9_-]{8,128}")
REMOVE_WORDS = frozenset({"remove", "delete"})
REMOVE_KEY_CALLBACK = "key:remove"
# Every key check is a real SauceNAO search
CHECK_FLOOD_LIMIT = 5
CHECK_FLOOD_WINDOW = 60

# One key change at a time per user: a check takes a while, and what the user sent later must land later
CHANGES = KeyLocks()

API_PAGE_ROW = [InlineKeyboardButton(text=texts.API_PAGE_BUTTON, url=texts.API_PAGE)]
REMOVE_KEY_ROW = [InlineKeyboardButton(text=texts.REMOVE_KEY_BUTTON, callback_data=REMOVE_KEY_CALLBACK)]


def build_router() -> Router:
    router = Router(name="apikey")
    private = F.chat.type == ChatType.PRIVATE
    # never flood limited: a key left in a group is there for everyone to see
    router.message.register(on_apikey_in_chat, Command("apikey"), ~private)
    # the steps, the removal and a malformed key cost no SauceNAO search: never flood limited either
    router.message.register(on_apikey, Command("apikey"), private, invert_f(_key_to_check))
    router.callback_query.register(on_add_key, F.data == ADD_KEY_CALLBACK)
    router.callback_query.register(on_remove_key, F.data == REMOVE_KEY_CALLBACK)

    # Where a key gets checked: every check is a real SauceNAO search
    checks = Router(name="apikey-checks")
    checks.message.middleware(FloodMiddleware(CHECK_FLOOD_LIMIT, CHECK_FLOOD_WINDOW))
    checks.message.register(on_apikey_key, Command("apikey"), private, _key_to_check)
    checks.message.register(on_bare_key, private, F.text.regexp(BARE_KEY, mode="fullmatch"))
    router.include_router(checks)
    return router


def _key_to_check(message: Message, command: CommandObject) -> dict[str, str] | bool:
    """Passes `/apikey <key>`, the form that costs a SauceNAO search, and hands the key over as `api_key`."""
    argument = (command.args or "").strip()
    if KEY_ARGUMENT.fullmatch(argument) and argument.lower() not in REMOVE_WORDS:
        return {"api_key": argument}
    return False


async def on_apikey(message: Message, command: CommandObject, *, bot: Bot, keys: KeyStore, reporter: Reporter) -> None:
    """/apikey in private without a key to check: the steps and the saved key, `/apikey remove`, or a malformed key."""
    user = message.from_user
    if user is None:
        return
    argument = (command.args or "").strip()
    if not argument:
        await _send_steps(bot, user.id, keys)
    elif argument.lower() in REMOVE_WORDS:
        await _remove(bot, user, keys, reporter)
    else:
        await bot.send_message(user.id, texts.KEY_MALFORMED, reply_markup=_markup(API_PAGE_ROW))


async def on_apikey_key(
    message: Message, api_key: str, *, bot: Bot, searcher: Searcher, keys: KeyStore, reporter: Reporter
) -> None:
    """`/apikey <key>` in private."""
    if message.from_user is not None:
        await _add(bot, message.from_user, api_key, searcher=searcher, keys=keys, reporter=reporter)


async def on_bare_key(message: Message, bot: Bot, searcher: Searcher, keys: KeyStore, reporter: Reporter) -> None:
    """A private message that is nothing but a SauceNAO key."""
    if message.from_user is not None and message.text:
        await _add(bot, message.from_user, message.text.strip(), searcher=searcher, keys=keys, reporter=reporter)


async def on_apikey_in_chat(message: Message, command: CommandObject, bot: Bot) -> None:
    """/apikey in a group: keys only go to Mikke in private.

    Channels are left out: that would need channel_post updates, every post of every channel the bot is in.
    """
    argument = (command.args or "").strip()
    if not argument or argument.lower() in REMOVE_WORDS:
        await message.reply(texts.KEY_PRIVATE_ONLY.format((await bot.me()).username))
        return
    # Anything else may be a key, now visible to the whole chat: never saved, deleted if Mikke may
    try:
        deleted = await message.delete()
    except TelegramAPIError as e:
        logger.info("Could not delete a key sent in chat %s: %s", message.chat.id, e.message)
        deleted = False
    user = message.from_user if message.sender_chat is None else None
    who = f', <a href="tg://user?id={user.id}">{html.escape(user.full_name, quote=False)}</a>' if user else ""
    # not a reply, which would quote the key
    await message.answer((texts.KEY_IN_CHAT_DELETED if deleted else texts.KEY_IN_CHAT_KEPT).format(who))


async def on_add_key(callback: CallbackQuery, bot: Bot, keys: KeyStore) -> None:
    """🔑 under a limit answer in private."""
    await _answer(callback)
    await _send_steps(bot, callback.from_user.id, keys)


async def on_remove_key(callback: CallbackQuery, bot: Bot, keys: KeyStore, reporter: Reporter) -> None:
    await _answer(callback)
    await _remove(bot, callback.from_user, keys, reporter)


async def _send_steps(bot: Bot, user_id: int, keys: KeyStore) -> None:
    """How to get a key, and the one saved, if any."""
    stored = await keys.get(user_id)
    text, rows = texts.KEY_STEPS, [API_PAGE_ROW]
    if stored is not None:
        status = texts.KEY_STATUS_SAVED if stored.valid else texts.KEY_STATUS_INVALID
        text += "\n\n" + status.format(stored.masked)
        rows.append(REMOVE_KEY_ROW)
    await bot.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _add(bot: Bot, user: User, api_key: str, *, searcher: Searcher, keys: KeyStore, reporter: Reporter) -> None:
    """Check the key with SauceNAO, then save it and tell the user what it gives them."""
    async with CHANGES.hold(str(user.id)):
        await _check_and_save(bot, user, api_key, searcher=searcher, keys=keys, reporter=reporter)


async def _check_and_save(
    bot: Bot, user: User, api_key: str, *, searcher: Searcher, keys: KeyStore, reporter: Reporter
) -> None:
    account: Account | None = None
    try:
        account = await searcher.saucenao(api_key).account()
    except InvalidKeyError:
        await bot.send_message(user.id, texts.KEY_REFUSED, reply_markup=_markup(API_PAGE_ROW))
        await reporter.key(bot, user, "refused")
        return
    except QuotaExceededError as e:
        if not e.daily:
            await bot.send_message(user.id, texts.KEY_BUSY)
            return
        # SauceNAO knows the key, it is just used up for today
    except Exception as e:
        # an error text may quote the request URL, and with it the key; SauceNAO being down is not our bug
        log = logger.warning if isinstance(e, UnavailableError) else logger.error
        log("Checking a SauceNAO key failed: %s", redact(f"{type(e).__name__}: {e}".replace(api_key, "[key]")))
        await bot.send_message(user.id, texts.KEY_CHECK_FAILED)
        return
    await keys.set(user.id, api_key)
    await bot.send_message(user.id, _saved(UserKey(api_key, valid=True), account), reply_markup=_markup(REMOVE_KEY_ROW))
    await reporter.key(bot, user, "added", account)


async def _remove(bot: Bot, user: User, keys: KeyStore, reporter: Reporter) -> None:
    async with CHANGES.hold(str(user.id)):
        if await keys.remove(user.id):
            await bot.send_message(user.id, texts.KEY_REMOVED)
            await reporter.key(bot, user, "removed")
        else:
            await bot.send_message(user.id, texts.KEY_NOTHING_TO_REMOVE)


def _saved(key: UserKey, account: Account | None) -> str:
    details = []
    if account is None:
        details.append(texts.KEY_USED_UP_TODAY)
    else:
        if account.plan:
            details.append(texts.KEY_ACCOUNT.format(account.plan))
        limits = []
        if account.long_limit is not None:
            limits.append(f"{account.long_limit} searches a day")
        if account.short_limit is not None:
            limits.append(f"{account.short_limit} per 30 seconds")
        if limits:
            details.append(texts.KEY_LIMITS.format(", ".join(limits)))
        if account.long_remaining is not None:
            details.append(texts.KEY_LEFT_TODAY.format(account.long_remaining))
    blocks = [texts.KEY_SAVED.format(key.masked), "\n".join(details), texts.KEY_REMOVE_HINT]
    return "\n\n".join(block for block in blocks if block)


def _markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


async def _answer(callback: CallbackQuery) -> None:
    # "query is too old" changes nothing here
    with contextlib.suppress(TelegramBadRequest):
        await callback.answer()
