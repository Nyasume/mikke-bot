"""Users' own SauceNAO keys: /apikey, a bare key in private, the buttons, and where they are used."""

import asyncio
import logging
import time

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage, SendRichMessage

from mikke import texts
from mikke.keys import UserKey
from mikke.saucenao import PROBE_URL, SEARCH_URL, SauceNao
from payloads import (
    ADMIN_ID,
    ANIME,
    API_KEY,
    ASKER,
    DAILY_LIMIT,
    EXTRA_BOT_TOKEN,
    FAV_GROUP,
    GROUP,
    PHOTO,
    PRIVATE,
    UNKNOWN_KEY,
    USER,
    USER_KEY,
    bot_answer,
    button_press,
    message,
    sauce_response,
    update,
)

ACCOUNT = {
    **sauce_response([], short_remaining=3, long_remaining=97)["header"],
    "account_type": "1",
    "user_id": "12345",
}


@pytest.fixture
def checked(respx_mock):
    """SauceNAO's answer to a key check: the key is fine."""
    return respx_mock.get(SEARCH_URL).respond(json={"header": ACCOUNT, "results": []})


def _replies(harness, chat_id: int = PRIVATE["id"]) -> list[SendMessage]:
    return [call for call in harness.session.calls(SendMessage) if call.chat_id == chat_id]


def _admin_texts(harness) -> list[str]:
    return [call.text for call in _replies(harness, ADMIN_ID)]


def _layout(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard] if markup else []


def _everything_sent(harness) -> str:
    return " ".join(str(request) for session in harness.sessions for request in session.requests)


# --- /apikey without a key: the steps -----------------------------------------


async def test_apikey_explains_the_steps(harness):
    await harness.feed(update(message(text="/apikey")))

    [reply] = _replies(harness)
    assert reply.text == texts.KEY_STEPS
    assert "1. Sign up for free" in reply.text
    assert _layout(reply.reply_markup) == [["Open the SauceNAO API page"]]
    assert reply.reply_markup.inline_keyboard[0][0].url == "https://saucenao.com/user.php?page=search-api"


@pytest.mark.parametrize(("valid", "status"), [(True, texts.KEY_STATUS_SAVED), (False, texts.KEY_STATUS_INVALID)])
async def test_apikey_shows_the_saved_key_by_its_tail_only(harness, valid, status):
    await harness.keys.set(7, USER_KEY)
    if not valid:
        await harness.keys.invalidate(7, USER_KEY)

    await harness.feed(update(message(text="/apikey")))

    [reply] = _replies(harness)
    assert reply.text == f"{texts.KEY_STEPS}\n\n{status.format('…a1b2')}"
    assert _layout(reply.reply_markup) == [["Open the SauceNAO API page"], ["🗑 Remove my key"]]
    assert USER_KEY not in _everything_sent(harness)


async def test_the_button_under_a_limit_answer_shows_the_steps(harness):
    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO)), data="key:add"))

    [answer] = harness.session.calls(AnswerCallbackQuery)
    assert not answer.show_alert
    [reply] = _replies(harness)
    assert reply.text == texts.KEY_STEPS


# --- adding a key ------------------------------------------------------------


@pytest.mark.parametrize(
    "text", [f"/apikey {USER_KEY}", USER_KEY, f"  {USER_KEY}\n"], ids=["command", "bare", "spaces"]
)
async def test_a_key_is_checked_saved_and_explained(harness, checked, text):
    await harness.feed(update(message(text=text)))

    request = checked.calls.last.request
    assert (request.url.params["api_key"], request.url.params["url"]) == (USER_KEY, PROBE_URL)
    assert await harness.keys.get(7) == UserKey(USER_KEY, valid=True)
    [reply] = _replies(harness)
    assert reply.text == (
        "✅ <b>Yay, got it!</b> From now on your searches use your own SauceNAO key <code>…a1b2</code> "
        "instead of Mikke's shared one.\n\n"
        "<b>Account:</b> Basic (free)\n"
        "<b>Your limits:</b> 100 searches a day, 4 per 30 seconds\n"
        "<b>Left today:</b> 97\n\n"
        f"{texts.KEY_REMOVE_HINT}"
    )
    assert _layout(reply.reply_markup) == [["🗑 Remove my key"]]
    # the owner hears who added one, and never the key
    [report] = _admin_texts(harness)
    assert report.startswith("<b>🔑 SauceNAO key added</b>\nBasic (free) · 4 per 30 s · 100 a day · 97 left today\n👤 ")
    assert USER_KEY not in _everything_sent(harness)


async def test_a_key_saucenao_does_not_know_is_not_saved(harness, respx_mock):
    respx_mock.get(SEARCH_URL).respond(403, json=UNKNOWN_KEY)

    await harness.feed(update(message(text=USER_KEY)))

    assert await harness.keys.get(7) is None
    [reply] = _replies(harness)
    assert reply.text == texts.KEY_REFUSED
    assert _layout(reply.reply_markup) == [["Open the SauceNAO API page"]]
    [report] = _admin_texts(harness)
    assert report.startswith("<b>🔑 SauceNAO refused a new key</b>\n👤 ")


async def test_a_key_used_up_for_today_is_saved(harness, respx_mock):
    respx_mock.get(SEARCH_URL).respond(429, json=DAILY_LIMIT)

    await harness.feed(update(message(text=USER_KEY)))

    assert await harness.keys.get(7) == UserKey(USER_KEY, valid=True)
    [reply] = _replies(harness)
    assert texts.KEY_USED_UP_TODAY in reply.text


async def test_a_failed_check_saves_nothing_and_logs_no_key(harness, respx_mock, caplog):
    respx_mock.get(SEARCH_URL).mock(side_effect=RuntimeError(f"GET {SEARCH_URL}?api_key={USER_KEY} {USER_KEY}"))

    await harness.feed(update(message(text=USER_KEY)))

    assert await harness.keys.get(7) is None
    assert [reply.text for reply in _replies(harness)] == [texts.KEY_CHECK_FAILED]
    [record] = [record for record in caplog.records if record.levelname == "ERROR"]
    assert record.getMessage().startswith("Checking a SauceNAO key failed: RuntimeError")
    assert USER_KEY not in caplog.text


async def test_saucenao_down_during_a_check_is_a_warning(harness, respx_mock, caplog):
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=httpx.ConnectError(f"GET {SEARCH_URL}?api_key={USER_KEY} {USER_KEY}")
    )

    await harness.feed(update(message(text=USER_KEY)))

    assert await harness.keys.get(7) is None
    assert [reply.text for reply in _replies(harness)] == [texts.KEY_CHECK_FAILED]
    assert route.call_count == 2
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    [record] = [record for record in caplog.records if record.name == "mikke.apikey"]
    assert record.levelname == "WARNING"
    assert record.getMessage().startswith("Checking a SauceNAO key failed: UnavailableError: ConnectError")
    assert USER_KEY not in caplog.text


@pytest.mark.parametrize("argument", ["hello world", "short", "key with <b>html</b>"])
async def test_something_else_after_apikey_is_not_sent_to_saucenao(harness, checked, argument):
    await harness.feed(update(message(text=f"/apikey {argument}")))

    assert checked.call_count == 0
    assert [reply.text for reply in _replies(harness)] == [texts.KEY_MALFORMED]


@pytest.mark.parametrize(
    "fields",
    [
        {"text": USER_KEY[:-1]},
        {"text": USER_KEY + "0"},
        {"text": USER_KEY.upper()},
        {"text": f"my key is {USER_KEY}"},
        {"text": "hello"},
    ],
    ids=["39", "41", "uppercase", "in-a-sentence", "text"],
)
async def test_other_private_messages_are_not_taken_for_a_key(harness, checked, fields):
    await harness.feed(update(message(**fields)))

    assert checked.call_count == 0
    assert harness.session.requests == []


async def test_a_picture_is_searched_not_taken_for_a_key(harness, checked, respx_mock):
    found = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    await harness.feed(update(message(photo=PHOTO, caption=USER_KEY)))

    assert (checked.call_count, found.call_count) == (0, 1)
    assert await harness.keys.get(7) is None


@pytest.mark.parametrize("text", [USER_KEY, f"/apikey {USER_KEY}"], ids=["bare", "command"])
async def test_key_checks_are_flood_limited(harness, checked, text):
    now = int(time.time())
    for _ in range(6):
        await harness.feed(update(message(text=text, date=now)))

    assert checked.call_count == 5


async def test_the_steps_and_the_removal_are_not_flood_limited(harness, checked):
    await harness.keys.set(7, USER_KEY)
    now = int(time.time())
    for _ in range(5):
        await harness.feed(update(message(text="/apikey", date=now)))

    await harness.feed(update(message(text="/apikey remove", date=now)))

    # neither is a SauceNAO search
    assert await harness.keys.get(7) is None
    assert checked.call_count == 0


async def test_a_removal_sent_while_a_key_is_checked_comes_after_it(harness, checked, monkeypatch):
    release = asyncio.Event()
    account = SauceNao.account

    async def slow_account(self):
        await release.wait()
        return await account(self)

    monkeypatch.setattr(SauceNao, "account", slow_account)
    adding = asyncio.create_task(harness.feed(update(message(text=USER_KEY))))
    await asyncio.sleep(0)
    removing = asyncio.create_task(harness.feed(update(message(text="/apikey remove"))))
    await asyncio.wait({removing}, timeout=0.1)

    release.set()
    await asyncio.gather(adding, removing)

    # in the order the user sent them: saved, then forgotten
    assert await harness.keys.get(7) is None
    assert [reply.text for reply in _replies(harness)][-1] == texts.KEY_REMOVED


# --- removing it -------------------------------------------------------------


async def test_apikey_remove(harness):
    await harness.keys.set(7, USER_KEY)

    await harness.feed(update(message(text="/apikey remove")))
    await harness.feed(update(message(text="/apikey remove")))

    assert await harness.keys.get(7) is None
    assert [reply.text for reply in _replies(harness)] == [texts.KEY_REMOVED, texts.KEY_NOTHING_TO_REMOVE]
    [report] = _admin_texts(harness)
    assert report.startswith("<b>🔑 SauceNAO key removed</b>\n👤 ")


async def test_the_remove_button(harness):
    await harness.keys.set(7, USER_KEY)

    await harness.feed(button_press(message(text="✅ Yay", **{"from": {**USER, "is_bot": True}}), data="key:remove"))

    assert await harness.keys.get(7) is None
    assert [reply.text for reply in _replies(harness)] == [texts.KEY_REMOVED]
    assert len(harness.session.calls(AnswerCallbackQuery)) == 1


# --- a key in a group -------------------------------------------


async def test_a_key_in_a_group_is_deleted_and_never_saved(harness, checked):
    await harness.feed(update(message(GROUP, text=f"/apikey {USER_KEY}", **{"from": ASKER})))

    assert checked.call_count == 0
    assert await harness.keys.get(ASKER["id"]) is None
    [delete] = harness.session.calls(DeleteMessage)
    assert delete.chat_id == GROUP["id"]
    [warning] = _replies(harness, GROUP["id"])
    assert warning.text == texts.KEY_IN_CHAT_DELETED.format(', <a href="tg://user?id=1234567">Test User</a>')
    # not a reply: that would quote the key
    assert warning.reply_parameters is None
    assert USER_KEY not in _everything_sent(harness)


async def test_a_key_in_a_group_that_cannot_be_deleted(harness, checked):
    harness.session.errors[DeleteMessage] = TelegramBadRequest(
        method=DeleteMessage(chat_id=GROUP["id"], message_id=1), message="Bad Request: message can't be deleted"
    )

    await harness.feed(update(message(GROUP, text=f"/apikey {USER_KEY}")))

    assert await harness.keys.get(7) is None
    [warning] = _replies(harness, GROUP["id"])
    assert warning.text.startswith("Psst, <a")
    assert "please delete it yourself" in warning.text


async def test_a_key_in_a_group_is_deleted_however_many_checks_came_before(harness, checked):
    now = int(time.time())
    for _ in range(5):
        await harness.feed(update(message(text=USER_KEY, date=now)))

    await harness.feed(update(message(GROUP, text=f"/apikey {USER_KEY}", date=now)))

    # only the checks are limited: a key left in a group for everyone to see is not
    assert checked.call_count == 5
    [delete] = harness.session.calls(DeleteMessage)
    assert delete.chat_id == GROUP["id"]


def test_channel_posts_are_not_asked_for(harness):
    # /apikey is not handled in channels: that would bring every post of every channel the bot is in
    assert "channel_post" not in harness.dp.resolve_used_update_types()


@pytest.mark.parametrize("text", ["/apikey", "/apikey remove"])
async def test_apikey_in_a_group_points_to_the_private_chat(harness, text):
    await harness.feed(update(message(GROUP, text=text)))

    [reply] = _replies(harness, GROUP["id"])
    assert reply.text == texts.KEY_PRIVATE_ONLY.format("MikkeTestBot")
    assert harness.session.calls(DeleteMessage) == []


# --- whose key a search uses -------------------------------------------------


def _keys_used(route) -> list[str]:
    return [call.request.url.params["api_key"] for call in route.calls]


@pytest.fixture
def found(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))


async def test_a_private_search_uses_the_senders_key(harness, found):
    await harness.keys.set(7, USER_KEY)

    await harness.feed(update(message(photo=PHOTO)))

    assert _keys_used(found) == [USER_KEY]
    [report] = harness.session.calls(SendRichMessage)
    assert "🔑 own key" in report.rich_message.html
    assert USER_KEY not in report.rich_message.html


async def test_sauce_in_a_group_uses_the_key_of_the_one_who_asked(harness, found):
    await harness.keys.set(ASKER["id"], USER_KEY)
    media_msg = message(GROUP, photo=PHOTO)

    await harness.feed(update(message(GROUP, text="/sauce", reply_to_message=media_msg, **{"from": ASKER})))

    assert _keys_used(found) == [USER_KEY]


async def test_favourite_group_photos_use_the_shared_key(harness, found):
    await harness.keys.set(7, USER_KEY)

    await harness.feed(update(message(FAV_GROUP, photo=PHOTO)))

    assert _keys_used(found) == [API_KEY]


async def test_an_inline_search_uses_the_senders_key(harness, respx_mock):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    await harness.keys.set(7, USER_KEY)
    chosen = {"result_id": "url", "from": USER, "query": "example.com/pic.jpg", "inline_message_id": "inline-42"}

    await harness.feed({"update_id": 901, "chosen_inline_result": chosen})

    assert _keys_used(route) == [USER_KEY]


async def test_both_bots_share_the_keys(make_harness, checked, found):
    harness = make_harness(extra_bot_tokens=[EXTRA_BOT_TOKEN])

    await harness.feed(update(message(text=USER_KEY)), harness.bots[1])
    await harness.feed(update(message(photo=PHOTO)), harness.bots[0])

    assert _keys_used(found) == [USER_KEY]


# --- limit answers -----------------------------------------------------------


@pytest.fixture
def used_up(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(429, json=DAILY_LIMIT)


async def test_a_limit_answer_in_private_offers_a_key(harness, used_up):
    await harness.feed(update(message(photo=PHOTO)))

    [edit] = harness.session.calls(EditMessageText)
    assert edit.text == f"{texts.LIMIT_REACHED}\n\n{texts.KEY_PITCH}"
    assert _layout(edit.reply_markup)[-2:] == [["🔑 Add my free key"], ["🎬 Anime scene"]]


async def test_a_limit_answer_in_a_group_is_short(harness, used_up):
    media_msg = message(GROUP, photo=PHOTO)

    await harness.feed(update(message(GROUP, text="/sauce", reply_to_message=media_msg)))

    [edit] = harness.session.calls(EditMessageText)
    assert edit.text == texts.LIMIT_REACHED
    assert ["🔑 Add my free key"] not in _layout(edit.reply_markup)


async def test_help_mentions_apikey_once(harness):
    await harness.feed(update(message(text="/help")))

    [reply] = _replies(harness)
    assert reply.text.count("/apikey") == 1
