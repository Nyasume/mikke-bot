import time

import pytest
from aiogram.methods import AnswerInlineQuery, EditMessageText, GetFile, SendMessage

from payloads import (
    ADMIN_ID,
    ANIME,
    BOT_TOKEN,
    BOT_USERNAME,
    FAV_GROUP,
    GROUP,
    PHOTO,
    PRIVATE,
    USER,
    message,
    sauce_response,
    update,
)
from reverse_search_bot import texts
from reverse_search_bot.saucenao import SEARCH_URL

STATIC_STICKER = {
    "file_id": "sticker-id",
    "file_unique_id": "sticker-u",
    "type": "regular",
    "width": 512,
    "height": 512,
    "is_animated": False,
    "is_video": False,
}
THUMB = {"file_id": "thumb-id", "file_unique_id": "thumb-u", "width": 320, "height": 320}


@pytest.fixture
def found(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))


def _sent(harness) -> list[SendMessage]:
    return harness.session.calls(SendMessage)


def _searched_file_ids(harness) -> list[str]:
    return [call.file_id for call in harness.session.calls(GetFile)]


async def test_private_photo_is_searched_via_placeholder(harness, found):
    msg = message(photo=PHOTO)
    await harness.feed(update(msg))

    placeholder, *_reports = _sent(harness)
    assert placeholder.chat_id == PRIVATE["id"]
    assert placeholder.text == texts.LOADING
    assert placeholder.reply_parameters.message_id == msg["message_id"]
    assert placeholder.reply_markup.inline_keyboard[0][0].text == "🍝"
    assert _searched_file_ids(harness) == ["photo-large-id"]
    assert found.call_count == 1

    [edit] = harness.session.calls(EditMessageText)
    assert edit.chat_id == PRIVATE["id"]
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")
    assert edit.reply_markup.inline_keyboard[0][0].text == "View on AniDB"


@pytest.mark.parametrize(
    ("fields", "searched"),
    [
        ({"sticker": STATIC_STICKER}, "sticker-id"),
        ({"sticker": {**STATIC_STICKER, "is_animated": True, "thumbnail": THUMB}}, "thumb-id"),
        ({"sticker": {**STATIC_STICKER, "is_video": True, "thumbnail": THUMB}}, "thumb-id"),
        ({"document": {"file_id": "doc-id", "file_unique_id": "doc-u", "file_name": "art.PNG"}}, "doc-id"),
        (
            {
                "animation": {"file_id": "gif-id", "file_unique_id": "gif-u", "width": 1, "height": 1, "duration": 1},
                "document": {
                    "file_id": "gif-id",
                    "file_unique_id": "gif-u",
                    "file_name": "funny.mp4",
                    "mime_type": "video/mp4",
                    "thumbnail": THUMB,
                },
            },
            "thumb-id",
        ),
        (
            {
                "video": {
                    "file_id": "video-id",
                    "file_unique_id": "video-u",
                    "width": 1,
                    "height": 1,
                    "duration": 5,
                    "thumbnail": THUMB,
                }
            },
            "thumb-id",
        ),
    ],
)
async def test_private_media_kinds(harness, found, fields, searched):
    await harness.feed(update(message(**fields)))
    assert _searched_file_ids(harness) == [searched]


@pytest.mark.parametrize(
    "fields",
    [
        {"document": {"file_id": "doc-id", "file_unique_id": "doc-u", "file_name": "notes.txt"}},
        {"sticker": {**STATIC_STICKER, "is_animated": True}},  # no thumbnail
        {"text": "hello"},
    ],
)
async def test_private_unsupported_messages_are_ignored(harness, found, fields):
    await harness.feed(update(message(**fields)))
    assert harness.session.requests == []


async def test_group_photo_is_not_searched(harness, found):
    await harness.feed(update(message(GROUP, photo=PHOTO)))
    assert harness.session.requests == []


async def test_favourite_group_photo_is_searched(harness, found):
    msg = message(FAV_GROUP, photo=PHOTO)
    await harness.feed(update(msg))

    placeholder = _sent(harness)[0]
    assert (placeholder.chat_id, placeholder.reply_parameters.message_id) == (FAV_GROUP["id"], msg["message_id"])
    assert _searched_file_ids(harness) == ["photo-large-id"]


async def test_favourite_group_sticker_is_not_searched(harness, found):
    await harness.feed(update(message(FAV_GROUP, sticker=STATIC_STICKER)))
    assert harness.session.requests == []


@pytest.mark.parametrize("text", ["/sauce", "/source", f"/sauce@{BOT_USERNAME}", f"/source@{BOT_USERNAME}", "sauce", "SOURCE", "What?"])
async def test_group_reply_trigger_searches_the_replied_media(harness, found, text):
    media_msg = message(GROUP, photo=PHOTO)
    await harness.feed(update(message(GROUP, text=text, reply_to_message=media_msg)))

    placeholder = _sent(harness)[0]
    assert placeholder.chat_id == GROUP["id"]
    assert placeholder.reply_parameters.message_id == media_msg["message_id"]
    assert _searched_file_ids(harness) == ["photo-large-id"]
    assert len(harness.session.calls(EditMessageText)) == 1


async def test_command_for_another_bot_is_ignored(harness, found):
    await harness.feed(update(message(GROUP, text="/sauce@OtherBot", reply_to_message=message(GROUP, photo=PHOTO))))
    assert _sent(harness) == []
    assert found.call_count == 0


async def test_keyword_must_be_the_whole_message(harness, found):
    await harness.feed(update(message(GROUP, text="sauce pls", reply_to_message=message(GROUP, photo=PHOTO))))
    assert harness.session.requests == []


@pytest.mark.parametrize("reply_to", [None, message(GROUP, text="just text")])
async def test_command_without_media_reply_gets_usage_hint(harness, found, reply_to):
    fields = {"reply_to_message": reply_to} if reply_to else {}
    msg = message(GROUP, text="/sauce", **fields)
    await harness.feed(update(msg))

    [hint] = _sent(harness)
    assert hint.text == texts.USAGE
    assert hint.reply_parameters.message_id == msg["message_id"]
    assert found.call_count == 0


async def test_keyword_without_media_reply_is_silent(harness, found):
    await harness.feed(update(message(GROUP, text="sauce")))
    assert harness.session.requests == []


@pytest.mark.parametrize("text", ["/start", "/help", f"/help@{BOT_USERNAME}"])
async def test_start_and_help(harness, text):
    await harness.feed(update(message(text=text)))
    [reply] = _sent(harness)
    assert reply.text == texts.HELP


async def test_added_to_group_sends_help(harness):
    bot_user = {"id": harness.bot.id, "is_bot": True, "first_name": "Sauce", "username": BOT_USERNAME}
    await harness.feed(update(message(GROUP, new_chat_members=[bot_user])))
    [reply] = _sent(harness)
    assert (reply.chat_id, reply.text) == (GROUP["id"], texts.HELP)


async def test_someone_else_joining_is_ignored(harness):
    await harness.feed(update(message(GROUP, new_chat_members=[{"id": 99, "is_bot": False, "first_name": "New"}])))
    assert _sent(harness) == []


def _inline_query(query: str) -> dict:
    return {"update_id": 900, "inline_query": {"id": "iq1", "from": USER, "query": query, "offset": ""}}


@pytest.mark.parametrize("query", ["https://example.com/pic.jpg", "example.com/pic.jpg"])
async def test_inline_url_query_offers_a_search(harness, query):
    await harness.feed(_inline_query(query))

    [answer] = harness.session.calls(AnswerInlineQuery)
    [article] = answer.results
    assert article.id == "url"
    assert article.title == "Tap for reverse search by URL"
    assert article.input_message_content.message_text == texts.LOADING
    assert article.reply_markup is not None


@pytest.mark.parametrize("query", ["", "just words", "notaurl"])
async def test_inline_non_url_query_gets_no_results(harness, query):
    await harness.feed(_inline_query(query))
    [answer] = harness.session.calls(AnswerInlineQuery)
    assert answer.results == []


async def test_chosen_inline_result_searches_the_url_and_edits_the_inline_message(harness, respx_mock):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    await harness.feed(
        {
            "update_id": 901,
            "chosen_inline_result": {
                "result_id": "url",
                "from": USER,
                "query": "example.com/pic.jpg",
                "inline_message_id": "inline-42",
            },
        }
    )

    assert route.calls.last.request.url.params["url"] == "https://example.com/pic.jpg"
    [edit] = harness.session.calls(EditMessageText)
    assert edit.inline_message_id == "inline-42"
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")


async def test_flood_protection_ignores_more_than_20_messages_in_3_seconds(harness, found):
    now = int(time.time())
    for _ in range(21):
        await harness.feed(update(message(photo=PHOTO, date=now)))
    placeholders = [call for call in _sent(harness) if call.text == texts.LOADING]
    assert len(placeholders) == 20

    # 3 seconds later the window has moved on
    await harness.feed(update(message(photo=PHOTO, date=now + 3)))
    assert len([call for call in _sent(harness) if call.text == texts.LOADING]) == 21


async def test_flood_protection_is_per_user(harness, found):
    now = int(time.time())
    for _ in range(21):
        await harness.feed(update(message(photo=PHOTO, date=now)))
    other = {"id": 8, "is_bot": False, "first_name": "Other"}
    await harness.feed(update({**message({"id": 8, "type": "private"}, photo=PHOTO, date=now), "from": other}))
    assert len([call for call in _sent(harness) if call.text == texts.LOADING]) == 21


async def test_unexpected_handler_error_is_reported(harness, found):
    harness.session.errors[EditMessageText] = RuntimeError("telegram exploded")

    await harness.feed(update(message(photo=PHOTO)))

    report = _sent(harness)[-1]
    assert report.chat_id == ADMIN_ID
    assert "RuntimeError: telegram exploded" in report.text


async def test_error_reports_can_be_switched_off(make_harness, found):
    harness = make_harness(report_errors=False, report_results=False)
    harness.session.errors[EditMessageText] = RuntimeError("telegram exploded")

    await harness.feed(update(message(photo=PHOTO)))

    assert [call.chat_id for call in _sent(harness)] == [PRIVATE["id"]]


async def test_error_report_hides_the_token(harness, found):
    harness.session.errors[EditMessageText] = RuntimeError(f"GET https://api.telegram.org/file/bot{BOT_TOKEN}/x.jpg")

    await harness.feed(update(message(photo=PHOTO)))

    report = _sent(harness)[-1]
    assert BOT_TOKEN not in report.text
    assert "/file/bot&lt;token&gt;/x.jpg" in report.text

