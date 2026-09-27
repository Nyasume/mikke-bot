"""Two bots, one process: each update is answered by the bot that received it, everything else is shared."""

import time
from urllib.parse import quote

import pytest
from aiogram.methods import AnswerCallbackQuery, AnswerInlineQuery, EditMessageText, GetFile, SendMessage, SendPhoto

from mikke import texts
from mikke.saucenao import SEARCH_URL
from mikke.tracemoe import SEARCH_URL as TRACE_URL
from payloads import (
    ADMIN_ID,
    ANIME,
    BOT_USERNAME,
    EXTRA_BOT_TOKEN,
    EXTRA_BOT_USERNAME,
    GROUP,
    PHOTO,
    PRIVATE,
    PUBLIC_URL,
    SCENE,
    USER,
    bot_answer,
    button_press,
    message,
    sauce_response,
    trace_response,
    update,
)

# the same picture as PHOTO, as the other bot sees it: file_ids differ per bot, file_unique_id does not
EXTRA_PHOTO = [{**size, "file_id": f"extra-{size['file_id']}"} for size in PHOTO]


@pytest.fixture
def harness(make_harness):
    return make_harness(extra_bot_tokens=[EXTRA_BOT_TOKEN])


@pytest.fixture
def found(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))


def _urls(markup) -> list[str]:
    return [button.url for row in markup.inline_keyboard for button in row if button.url]


async def test_a_search_is_answered_and_reported_by_the_bot_that_received_it(harness, found):
    primary, extra = harness.sessions
    msg = message(photo=EXTRA_PHOTO)

    await harness.feed(update(msg), harness.bots[1])

    placeholder, report = extra.calls(SendMessage)
    assert (placeholder.chat_id, placeholder.text) == (PRIVATE["id"], texts.LOADING)
    assert placeholder.reply_parameters.message_id == msg["message_id"]
    assert [call.file_id for call in extra.calls(GetFile)] == ["extra-photo-large-id"]
    [edit] = extra.calls(EditMessageText)
    assert edit.chat_id == PRIVATE["id"]
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")
    # the owner report links the image through this bot, and resends the file_id only this bot knows
    assert report.chat_id == ADMIN_ID
    assert "img%2F43%2Fextra-photo-large-id" in report.text
    [photo] = extra.calls(SendPhoto)
    assert (photo.chat_id, photo.photo) == (ADMIN_ID, "extra-photo-large-id")
    assert primary.requests == []


async def test_fallback_links_point_at_the_bot_that_has_the_file(harness, respx_mock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))

    await harness.feed(update(message(photo=PHOTO)))
    await harness.feed(update(message(photo=EXTRA_PHOTO)), harness.bots[1])

    # one SauceNAO search: the "no result" is cached for both
    assert route.call_count == 1
    [primary_edit], [extra_edit] = (session.calls(EditMessageText) for session in harness.sessions)
    assert quote(f"{PUBLIC_URL}/img/42/photo-large-id", safe="") in _urls(primary_edit.reply_markup)[0]
    assert quote(f"{PUBLIC_URL}/img/43/extra-photo-large-id", safe="") in _urls(extra_edit.reply_markup)[0]


async def test_results_found_by_one_bot_are_cached_for_the_other(harness, found):
    primary, extra = harness.sessions

    await harness.feed(update(message(photo=PHOTO)))
    await harness.feed(update(message(photo=EXTRA_PHOTO)), harness.bots[1])

    assert found.call_count == 1
    assert extra.calls(GetFile) == []
    [first], [second] = primary.calls(EditMessageText), extra.calls(EditMessageText)
    assert second.text == first.text
    # each bot reports its own answer, with the file it can resend
    assert [call.photo for call in primary.calls(SendPhoto)] == ["photo-large-id"]
    assert [call.photo for call in extra.calls(SendPhoto)] == ["extra-photo-large-id"]


async def test_the_saucenao_limit_is_shared(harness, respx_mock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], short_remaining=0))
    await harness.feed(update(message(photo=PHOTO)))

    other_picture = [{**size, "file_unique_id": "other-u"} for size in EXTRA_PHOTO]
    await harness.feed(update(message(photo=other_picture)), harness.bots[1])

    assert route.call_count == 1
    [edit] = harness.sessions[1].calls(EditMessageText)
    assert edit.text == texts.LIMIT_REACHED
    assert harness.sessions[1].calls(GetFile) == []


async def test_the_scene_button_is_answered_by_the_bot_that_received_the_press(harness, respx_mock):
    route = respx_mock.post(TRACE_URL).respond(json=trace_response([SCENE]))
    primary, extra = harness.sessions
    media_msg = message(GROUP, photo=EXTRA_PHOTO)

    await harness.feed(button_press(bot_answer(GROUP, media_msg)), harness.bots[1])

    assert [call.file_id for call in extra.calls(GetFile)] == ["extra-photo-large-id"]
    [reply] = extra.calls(SendMessage)
    assert (reply.chat_id, reply.reply_parameters.message_id) == (GROUP["id"], media_msg["message_id"])
    assert reply.text.startswith("🎬 <b>Is the Order a Rabbit?</b>")
    [answer] = extra.calls(AnswerCallbackQuery)
    assert answer.show_alert is False
    assert primary.requests == []

    # the scene is cached for the other bot too
    await harness.feed(button_press(bot_answer(GROUP, message(GROUP, photo=PHOTO))))
    assert route.call_count == 1
    assert primary.calls(GetFile) == []
    assert primary.calls(SendMessage)[0].text == reply.text


async def test_inline_mode_goes_through_the_bot_that_was_used(harness, respx_mock):
    respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    primary, extra = harness.sessions
    query = {"id": "iq1", "from": USER, "query": "example.com/pic.jpg", "offset": ""}
    chosen = {"result_id": "url", "from": USER, "query": "example.com/pic.jpg", "inline_message_id": "inline-43"}

    await harness.feed({"update_id": 900, "inline_query": query}, harness.bots[1])
    await harness.feed({"update_id": 901, "chosen_inline_result": chosen}, harness.bots[1])

    [answer] = extra.calls(AnswerInlineQuery)
    assert [result.id for result in answer.results] == ["url"]
    [edit] = extra.calls(EditMessageText)
    assert edit.inline_message_id == "inline-43"
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")
    assert primary.requests == []


@pytest.mark.parametrize(("username", "receiver"), [(EXTRA_BOT_USERNAME, 1), (BOT_USERNAME, 0)])
async def test_a_command_is_taken_by_the_bot_it_names(harness, found, username, receiver):
    """Both bots can sit in one group; `/sauce@<bot>` is for that bot only."""
    command = update(message(GROUP, text=f"/sauce@{username}", reply_to_message=message(GROUP, photo=PHOTO)))

    for bot in harness.bots:
        await harness.feed(command, bot)

    placeholders = [
        [call for call in session.calls(SendMessage) if call.chat_id == GROUP["id"]] for session in harness.sessions
    ]
    assert [len(sent) for sent in placeholders] == [1 - receiver, receiver]
    assert [len(session.calls(EditMessageText)) for session in harness.sessions] == [1 - receiver, receiver]
    assert found.call_count == 1


async def test_each_bot_greets_a_group_only_when_it_is_the_one_added(harness):
    extra_bot = {"id": 43, "is_bot": True, "first_name": "Mikke", "username": EXTRA_BOT_USERNAME}
    joined = update(message(GROUP, new_chat_members=[extra_bot]))

    for bot in harness.bots:
        await harness.feed(joined, bot)

    primary, extra = harness.sessions
    assert primary.calls(SendMessage) == []
    [greeting] = extra.calls(SendMessage)
    assert (greeting.chat_id, greeting.text) == (GROUP["id"], texts.HELP)


async def test_flood_protection_counts_messages_to_every_bot(harness, found):
    now = int(time.time())
    for i in range(21):
        await harness.feed(update(message(photo=PHOTO, date=now)), harness.bots[i % 2])

    placeholders = [
        call for session in harness.sessions for call in session.calls(SendMessage) if call.text == texts.LOADING
    ]
    assert len(placeholders) == 20


async def test_errors_are_reported_through_the_bot_that_failed(harness, found):
    primary, extra = harness.sessions
    extra.errors[EditMessageText] = RuntimeError("telegram exploded")

    await harness.feed(update(message(photo=EXTRA_PHOTO)), harness.bots[1])

    report = extra.calls(SendMessage)[-1]
    assert report.chat_id == ADMIN_ID
    assert "RuntimeError: telegram exploded" in report.text
    assert primary.requests == []
