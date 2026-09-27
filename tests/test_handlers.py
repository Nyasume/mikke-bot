import time

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, AnswerInlineQuery, EditMessageText, GetFile, SendMessage

from mikke import texts
from mikke.saucenao import SEARCH_URL
from mikke.tracemoe import SEARCH_URL as TRACE_URL
from payloads import (
    ADMIN_ID,
    ANIME,
    BOT_TOKEN,
    BOT_USERNAME,
    FAV_GROUP,
    GROUP,
    PHOTO,
    PRIVATE,
    SCENE,
    USER,
    bot_answer,
    button_press,
    message,
    sauce_response,
    trace_response,
    update,
)

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


def _layout(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


async def test_private_photo_is_searched_via_placeholder(harness, found):
    msg = message(photo=PHOTO)
    await harness.feed(update(msg))

    placeholder, *_reports = _sent(harness)
    assert placeholder.chat_id == PRIVATE["id"]
    assert placeholder.text == "<i>Mikke is looking...</i>"
    assert placeholder.reply_parameters.message_id == msg["message_id"]
    assert placeholder.reply_markup.inline_keyboard[0][0].text == "🔍"
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
    assert reply.text.startswith("Hi, I'm Mikke!")


async def test_added_to_group_sends_help(harness):
    bot_user = {"id": harness.bot.id, "is_bot": True, "first_name": "Mikke", "username": BOT_USERNAME}
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
    assert article.title == texts.INLINE_TITLE
    assert article.input_message_content.message_text == texts.LOADING
    assert article.reply_markup is not None


@pytest.mark.parametrize("query", ["", "just words", "notaurl"])
async def test_inline_non_url_query_gets_no_results(harness, query):
    await harness.feed(_inline_query(query))
    [answer] = harness.session.calls(AnswerInlineQuery)
    assert answer.results == []


def _chosen_inline_result(query: str) -> dict:
    return {
        "update_id": 901,
        "chosen_inline_result": {"result_id": "url", "from": USER, "query": query, "inline_message_id": "inline-42"},
    }


async def test_chosen_inline_result_searches_the_url_and_edits_the_inline_message(harness, respx_mock):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    await harness.feed(_chosen_inline_result("example.com/pic.jpg"))

    assert route.calls.last.request.url.params["url"] == "https://example.com/pic.jpg"
    [edit] = harness.session.calls(EditMessageText)
    assert edit.inline_message_id == "inline-42"
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")
    # trace.moe is for chats only
    assert _layout(edit.reply_markup) == [["View on AniDB", "MAL", "AniList"]]


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


async def test_rejected_link_buttons_fall_back_to_the_text_and_the_scene_button(harness, found):
    harness.session.fail_once[EditMessageText] = TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: BUTTON_URL_INVALID"
    )

    await harness.feed(update(message(photo=PHOTO)))

    first, second = harness.session.calls(EditMessageText)
    assert _layout(first.reply_markup) == [["View on AniDB", "MAL", "AniList"], ["🎬 Anime scene"]]
    assert _layout(second.reply_markup) == [["🎬 Anime scene"]]
    assert second.text == first.text


async def test_rejected_buttons_on_an_inline_answer_fall_back_to_the_text_alone(harness, respx_mock):
    respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    harness.session.fail_once[EditMessageText] = TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: BUTTON_URL_INVALID"
    )

    await harness.feed(_chosen_inline_result("example.com/pic.jpg"))

    first, second = harness.session.calls(EditMessageText)
    assert first.reply_markup is not None
    assert second.reply_markup is None


async def test_rate_limited_edit_is_retried(harness, found):
    harness.session.fail_once[EditMessageText] = TelegramRetryAfter(
        method=EditMessageText(text="x"), message="Too Many Requests", retry_after=0
    )

    await harness.feed(update(message(photo=PHOTO)))

    first, second = harness.session.calls(EditMessageText)
    assert second.reply_markup == first.reply_markup


# --- 🎬 Anime scene -------------------------------------------------------------

SCENE_ROW = ["🎬 Anime scene"]
QUOTA_DEPLETED = {"quota": 100, "quotaUsed": 100, "error": "Search quota depleted (quota per 24 hours: 100, used: 100)"}


@pytest.fixture
def scene_found(respx_mock):
    return respx_mock.post(TRACE_URL).respond(json=trace_response([SCENE]))


@pytest.mark.parametrize(
    ("response", "text", "first_row"),
    [
        ({"json": sauce_response([ANIME])}, "<b>One Piece (Ep. 12)</b>", ["View on AniDB", "MAL", "AniList"]),
        ({"json": sauce_response([])}, texts.NO_RESULT, ["Google Lens", "Yandex", "Bing"]),
        ({"status_code": 429}, texts.LIMIT_REACHED, ["Google Lens", "Yandex", "Bing"]),
        ({"status_code": 500}, texts.ERROR, ["Google Lens", "Yandex", "Bing"]),
    ],
    ids=["result", "no-result", "limit", "error"],
)
async def test_chat_answers_end_with_the_scene_button(harness, respx_mock, response, text, first_row):
    respx_mock.post(SEARCH_URL).respond(**response)

    await harness.feed(update(message(photo=PHOTO)))

    [edit] = harness.session.calls(EditMessageText)
    assert edit.text.startswith(text)
    layout = _layout(edit.reply_markup)
    assert layout[0] == first_row
    assert layout[-1] == SCENE_ROW
    assert edit.reply_markup.inline_keyboard[-1][0].callback_data == "scene"


async def test_file_telegram_refuses_gets_no_scene_button(harness, found):
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    await harness.feed(update(message(photo=PHOTO)))

    [edit] = harness.session.calls(EditMessageText)
    assert edit.text == texts.INVALID_FILE
    assert edit.reply_markup is None


async def test_scene_press_searches_the_replied_media_and_replies_to_it(harness, scene_found):
    media_msg = message(GROUP, photo=PHOTO)
    await harness.feed(button_press(bot_answer(GROUP, media_msg)))

    assert _searched_file_ids(harness) == ["photo-large-id"]
    request = scene_found.calls.last.request
    assert BOT_TOKEN.encode() not in request.content
    assert BOT_TOKEN not in str(request.url)

    [reply] = _sent(harness)
    assert (reply.chat_id, reply.reply_parameters.message_id) == (GROUP["id"], media_msg["message_id"])
    assert reply.text.startswith("🎬 <b>Is the Order a Rabbit?</b>\n<i>Gochuumon wa Usagi Desu ka?</i>")
    assert _layout(reply.reply_markup) == [["AniList", "MyAnimeList"]]
    [answer] = harness.session.calls(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (None, False)


async def test_scene_press_on_a_video_searches_its_thumbnail(harness, scene_found):
    video = {"file_id": "video-id", "file_unique_id": "video-u", "width": 1, "height": 1, "duration": 5, "thumbnail": THUMB}
    await harness.feed(button_press(bot_answer(PRIVATE, message(video=video))))

    assert _searched_file_ids(harness) == ["thumb-id"]
    assert scene_found.call_count == 1


async def test_scene_results_are_cached_per_file(harness, scene_found):
    media_msg = message(GROUP, photo=PHOTO)
    await harness.feed(button_press(bot_answer(GROUP, media_msg)))
    # the same picture forwarded again, answered in another message
    await harness.feed(button_press(bot_answer(GROUP, message(GROUP, photo=PHOTO))))

    assert scene_found.call_count == 1
    assert len(harness.session.calls(GetFile)) == 1
    first, second = _sent(harness)
    assert second.text == first.text


async def test_scene_press_without_the_media_gets_an_alert(harness, scene_found):
    await harness.feed(button_press(bot_answer(GROUP, None)))

    [answer] = harness.session.calls(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (texts.SCENE_NO_MEDIA, True)
    assert scene_found.call_count == 0
    assert _sent(harness) == []


async def test_scene_quota_used_up_gets_an_alert_and_no_more_api_calls(harness, respx_mock):
    route = respx_mock.post(TRACE_URL).respond(402, json=QUOTA_DEPLETED)

    await harness.feed(button_press(bot_answer(GROUP, message(GROUP, photo=PHOTO))))
    requests_before = len(harness.session.requests)
    await harness.feed(button_press(bot_answer(GROUP, message(GROUP, sticker=STATIC_STICKER))))

    first, second = harness.session.calls(AnswerCallbackQuery)
    assert (first.text, first.show_alert) == (texts.SCENE_LIMIT, True)
    assert (second.text, second.show_alert) == (texts.SCENE_LIMIT, True)
    assert route.call_count == 1
    # the second press did not even download the file
    assert len(harness.session.requests) == requests_before + 1
    assert _sent(harness) == []


async def test_scene_error_gets_an_alert_and_is_reported(harness, respx_mock):
    respx_mock.post(TRACE_URL).respond(503, json={"error": "Error: Search queue is full"})

    await harness.feed(button_press(bot_answer(GROUP, message(GROUP, photo=PHOTO))))

    [answer] = harness.session.calls(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (texts.SCENE_ERROR, True)
    [report] = _sent(harness)
    assert report.chat_id == ADMIN_ID
    assert "TraceMoeError: HTTP 503: Error: Search queue is full" in report.text


async def test_scene_error_is_not_cached(harness, respx_mock):
    respx_mock.post(TRACE_URL).respond(500, text="boom")
    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO))))

    route = respx_mock.post(TRACE_URL).respond(json=trace_response([SCENE]))
    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO))))

    assert route.call_count == 2
    assert _sent(harness)[-1].text.startswith("🎬 <b>Is the Order a Rabbit?</b>")


async def test_scene_answer_after_telegram_stopped_waiting_is_not_an_error(harness, scene_found):
    harness.session.errors[AnswerCallbackQuery] = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="x"), message="Bad Request: query is too old"
    )

    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO))))

    # the scene reply went out and nothing was reported to the owner
    [reply] = _sent(harness)
    assert reply.chat_id == PRIVATE["id"]


async def test_other_buttons_do_not_search(harness, scene_found):
    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO)), data="noop"))

    assert scene_found.call_count == 0
    assert harness.session.requests == []


async def test_flood_protection_limits_scene_presses_harder(harness, scene_found):
    answer = bot_answer(PRIVATE, message(photo=PHOTO))
    for _ in range(6):
        await harness.feed(button_press(answer))

    assert len(harness.session.calls(AnswerCallbackQuery)) == 5
    assert scene_found.call_count == 1
