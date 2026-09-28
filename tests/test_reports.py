"""Activity reports: one rich message to the owner per search, a plain one when Telegram rejects it."""

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetFile,
    SendMessage,
    SendPhoto,
    SendRichMessage,
    SendSticker,
)

from mikke.saucenao import SEARCH_URL
from mikke.tracemoe import SEARCH_URL as TRACE_URL
from payloads import (
    ADMIN_ID,
    ANIME,
    API_KEY,
    ASKER,
    BASIC_GROUP,
    BOT_TOKEN,
    BOT_USERNAME,
    CLOUDFLARE_DOWN,
    PHOTO,
    PRIVATE,
    PRIVATE_SUPERGROUP,
    PUBLIC_GROUP,
    SCENE,
    UNKNOWN_KEY,
    USER_KEY,
    bot_answer,
    button_press,
    message,
    sauce_response,
    trace_response,
    update,
)

STICKER = {
    "file_id": "sticker-id",
    "file_unique_id": "sticker-u",
    "type": "regular",
    "width": 512,
    "height": 512,
    "is_animated": False,
    "is_video": False,
}
THUMB = {"file_id": "thumb-id", "file_unique_id": "thumb-u", "width": 320, "height": 320}
IMG = "https%3A%2F%2Fexample.org%2Fmikke%2Fimg%2F42%2F"
ANIME_HITS = (
    '<ul><li>93.1% <b>One Piece</b> · <a href="https://anidb.net/anime/69">AniDB</a>'
    ' · <a href="https://myanimelist.net/anime/21/">MAL</a> · <a href="https://anilist.co/anime/21/">AniList</a></li></ul>'
)
REJECTED = TelegramBadRequest(
    method=SendRichMessage(chat_id=ADMIN_ID, rich_message={"html": "x"}), message="Bad Request"
)


@pytest.fixture
def found(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))


def _reports(harness) -> list[SendRichMessage]:
    return harness.session.calls(SendRichMessage)


def _report(harness) -> str:
    [report] = _reports(harness)
    assert report.chat_id == ADMIN_ID
    return report.rich_message.html


def _admin_messages(harness) -> list[SendMessage]:
    return [call for call in harness.session.calls(SendMessage) if call.chat_id == ADMIN_ID]


def _sauce(chat: dict, photo: list[dict] = PHOTO, **fields) -> tuple[dict, dict]:
    """A photo in `chat`, and ASKER's /sauce in reply to it."""
    media_msg = message(chat, photo=photo)
    return media_msg, update(message(chat, text="/sauce", reply_to_message=media_msg, **{"from": ASKER}, **fields))


async def test_found_in_a_public_group(harness, found):
    media_msg, command = _sauce(PUBLIC_GROUP)

    await harness.feed(command)

    [report] = _reports(harness)
    assert report.chat_id == ADMIN_ID
    assert report.disable_notification is True
    [image] = report.rich_message.media
    assert (image.id, image.media.type, image.media.media) == ("searched", "photo", "photo-large-id")
    assert report.rich_message.html == (
        '<img src="tg://photo?id=searched"/>'
        "<h3>✅ SauceNAO: found</h3>"
        "<blockquote><p><b>One Piece (Ep. 12)</b></p><p><b>Year: </b>1999-1999</p>"
        "<p><b>Time: </b>00:12:33 / 00:24:40</p></blockquote>"
        f"{ANIME_HITS}"
        # the one who asked, not the one who posted the picture
        '<p>👤 <a href="tg://user?id=1234567">Test User</a> · @test_user · <code>1234567</code></p>'
        '<p>💬 <a href="https://t.me/example_art">Anime Art &lt;Fans&gt;</a> · <code>-1001234567890</code>'
        f' · <a href="https://t.me/example_art/{media_msg["message_id"]}">message</a></p>'
        "<p>📊 SauceNAO: 10/100 used (24 h) · shared key</p>"
        f'<footer>via @{BOT_USERNAME} · <a href="https://saucenao.com/search.php?url={IMG}photo-large-id">SauceNAO</a>'
        "</footer>"
    )
    # one report, after the answer
    assert _admin_messages(harness) == []
    assert harness.session.requests.index(report) > harness.session.requests.index(
        harness.session.calls(EditMessageText)[0]
    )


async def test_nothing_found_in_private(harness, respx_mock):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert "<h3>🤷 SauceNAO: nothing found</h3>" in html
    assert "<blockquote>" not in html
    assert "<ul>" not in html
    assert '<p>👤 <a href="tg://user?id=7">User</a> · <code>7</code></p><p>💬 private chat</p>' in html


async def test_quota_used_up(harness, respx_mock):
    respx_mock.post(SEARCH_URL).respond(429)

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert "<h3>⏳ SauceNAO: quota used up</h3>" in html
    # SauceNAO has not told the numbers yet
    assert "📊" not in html


async def test_error_is_in_the_activity_report_alone(harness, respx_mock):
    respx_mock.post(SEARCH_URL).respond(400, text="boom")

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert "<h3>⚠ SauceNAO: error</h3><blockquote><p><code>SauceNaoError: HTTP 400</code>" in html
    assert _admin_messages(harness) == []


async def test_saucenao_down_is_reported_in_short(harness, respx_mock):
    respx_mock.post(SEARCH_URL).respond(521, html=CLOUDFLARE_DOWN)

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert (
        "<h3>⚠ SauceNAO: error</h3><blockquote><p>"
        "<code>UnavailableError: HTTP 521 (Cloudflare: web server is down)</code></p></blockquote>"
    ) in html
    assert "DOCTYPE" not in html
    assert _admin_messages(harness) == []


async def test_error_text_hides_the_token(harness, respx_mock):
    respx_mock.post(SEARCH_URL).mock(
        side_effect=RuntimeError(f"GET https://api.telegram.org/file/bot{BOT_TOKEN}/x.jpg")
    )

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert BOT_TOKEN not in html
    assert "/file/bot&lt;token&gt;/x.jpg" in html


async def test_file_telegram_refuses_is_reported_with_the_reason(harness, found):
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert "<h3>🚫 SauceNAO: Telegram would not give the file</h3>" in html
    assert "<code>file is too big</code>" in html
    # a photo is still embedded by its file_id
    assert _reports(harness)[0].rich_message.media[0].media.media == "photo-large-id"


async def test_cached_answers_say_so(harness, found):
    await harness.feed(update(message(photo=PHOTO)))
    await harness.feed(update(message(photo=PHOTO)))

    first, second = (report.rich_message.html for report in _reports(harness))
    assert "💾 from the cache" not in first
    assert f"<footer>via @{BOT_USERNAME} · 💾 from the cache · " in second
    assert found.call_count == 1


@pytest.mark.parametrize(
    ("chat", "where"),
    [
        (
            PRIVATE_SUPERGROUP,
            '<p>💬 <b>Secret Club</b> · <code>-1009876543210</code> · <a href="https://t.me/c/9876543210/{}">message</a></p>',
        ),
        # basic groups have no message links
        (BASIC_GROUP, "<p>💬 <b>Old Group</b> · <code>-4001234</code></p>"),
    ],
    ids=["private-supergroup", "basic-group"],
)
async def test_where_without_a_public_link(harness, found, chat, where):
    media_msg, command = _sauce(chat)

    await harness.feed(command)

    assert where.format(media_msg["message_id"]) in _report(harness)


async def test_a_channel_or_an_anonymous_admin_is_named(harness, found):
    anonymous = {"id": 1087968824, "is_bot": True, "first_name": "Group", "username": "GroupAnonymousBot"}
    channel = {"id": -1005550001, "type": "channel", "title": "Art Channel", "username": "example_channel"}
    media_msg = message(PUBLIC_GROUP, photo=PHOTO)

    await harness.feed(
        update(
            message(PUBLIC_GROUP, text="/sauce", reply_to_message=media_msg, sender_chat=channel, **{"from": anonymous})
        )
    )

    assert ' · as <a href="https://t.me/example_channel">Art Channel</a></p>' in _report(harness)


async def test_names_are_escaped(harness, found):
    user = {"id": 99, "is_bot": False, "first_name": "<b>Evil</b> & Co"}

    await harness.feed(update({**message({"id": 99, "type": "private"}, photo=PHOTO), "from": user}))

    assert '<a href="tg://user?id=99">&lt;b&gt;Evil&lt;/b&gt; &amp; Co</a>' in _report(harness)


@pytest.mark.parametrize(
    ("fields", "embedded"),
    [
        ({"sticker": STICKER}, "sticker-id"),
        ({"sticker": {**STICKER, "is_animated": True, "thumbnail": THUMB}}, "thumb-id"),
        ({"video": {"file_id": "video-id", "file_unique_id": "video-u", "width": 1, "height": 1, "duration": 5}}, None),
    ],
    ids=["sticker", "animated-sticker", "video"],
)
async def test_other_media_embed_the_searched_still_by_its_public_link(harness, found, fields, embedded):
    if embedded is None:  # a video: its thumbnail
        fields = {"video": {**fields["video"], "thumbnail": THUMB}}
        embedded = "thumb-id"

    await harness.feed(update(message(**fields)))

    [report] = _reports(harness)
    [image] = report.rich_message.media
    assert (image.media.type, image.media.media) == ("photo", f"https://example.org/mikke/img/42/{embedded}")


async def test_scene_search(harness, respx_mock):
    respx_mock.post(TRACE_URL).respond(json=trace_response([SCENE]))
    media_msg = message(PUBLIC_GROUP, photo=PHOTO)

    await harness.feed(button_press(bot_answer(PUBLIC_GROUP, media_msg), user=ASKER))

    html = _report(harness)
    assert html.startswith('<img src="tg://photo?id=searched"/><h3>✅ trace.moe: found</h3>')
    assert "<blockquote><p>🎬 <b>Is the Order a Rabbit?</b></p><p><i>Gochuumon wa Usagi Desu ka?</i></p>" in html
    assert (
        '<ul><li>96.1% <b>Is the Order a Rabbit?</b> · <a href="https://anilist.co/anime/20517">AniList</a>'
        ' · <a href="https://myanimelist.net/anime/21273">MyAnimeList</a></li></ul>'
    ) in html
    assert '<p>👤 <a href="tg://user?id=1234567">Test User</a>' in html
    assert f'<a href="https://t.me/example_art/{media_msg["message_id"]}">message</a>' in html
    assert f'<a href="https://trace.moe/?url={IMG}photo-large-id">trace.moe</a></footer>' in html
    # quotaUsed 1 before this search
    assert "<p>📊 trace.moe: 2/100 used (24 h) · guest</p><footer>" in html
    # the popup spinner stopped before the report went out
    [answer] = harness.session.calls(AnswerCallbackQuery)
    assert harness.session.requests.index(answer) < harness.session.requests.index(_reports(harness)[0])


async def test_scene_quota_used_up(harness, respx_mock):
    quota = {"quota": 100, "quotaUsed": 100, "error": "Search quota depleted (quota per 24 hours: 100, used: 100)"}
    respx_mock.post(TRACE_URL).respond(402, json=quota)

    await harness.feed(button_press(bot_answer(PRIVATE, message(photo=PHOTO))))

    html = _report(harness)
    assert "<h3>⏳ trace.moe: quota used up</h3>" in html
    assert "<p>📊 trace.moe: 100/100 used (24 h) · guest</p>" in html


async def test_scene_press_without_media_is_no_search(harness, respx_mock):
    await harness.feed(button_press(bot_answer(PRIVATE, None)))

    assert _reports(harness) == []


async def test_inline_search(harness, respx_mock):
    respx_mock.get(SEARCH_URL).respond(json=sauce_response([ANIME]))
    chosen = {"result_id": "url", "from": ASKER, "query": "example.com/pic.jpg", "inline_message_id": "inline-42"}

    await harness.feed({"update_id": 901, "chosen_inline_result": chosen})

    [report] = _reports(harness)
    assert report.rich_message.media[0].media.media == "https://example.com/pic.jpg"
    html = report.rich_message.html
    assert '<p>📥 inline mode · <a href="https://example.com/pic.jpg">image URL</a></p>' in html
    assert '<p>👤 <a href="tg://user?id=1234567">Test User</a> · @test_user · <code>1234567</code></p>' in html


# --- the quota -----------------------------------------------------------------


async def test_the_quota_shown_is_the_one_of_the_key_that_searched(harness, respx_mock):
    responses = {
        USER_KEY: httpx.Response(200, json=sauce_response([ANIME], long_remaining=97)),
        API_KEY: httpx.Response(200, json=sauce_response([ANIME], long_remaining=43)),
    }
    respx_mock.post(SEARCH_URL).mock(side_effect=lambda request: responses[request.url.params["api_key"]])
    await harness.keys.set(7, USER_KEY)
    other_photo = [{"file_id": "other-id", "file_unique_id": "other-u", "width": 90, "height": 90}]

    await harness.feed(update(message(photo=PHOTO)))
    # ASKER has no key: the cache, before and after the shared key has answered once
    for photo in (PHOTO, other_photo, PHOTO):
        await harness.feed(_sauce(PUBLIC_GROUP, photo)[1])

    own, unknown, shared, cached = (report.rich_message.html for report in _reports(harness))
    assert "<p>📊 SauceNAO: 3/100 used (24 h) · own key</p>" in own
    assert USER_KEY not in own
    assert "💾 from the cache" in unknown
    assert "📊" not in unknown
    assert "<p>📊 SauceNAO: 57/100 used (24 h) · shared key</p>" in shared
    assert "💾 from the cache" in cached
    assert "<p>📊 SauceNAO: 57/100 used (24 h) · shared key</p>" in cached


async def test_a_used_up_quota_is_shown_in_full(harness, respx_mock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], long_remaining=0))

    await harness.feed(update(message(photo=PHOTO)))
    await harness.feed(update(message(sticker=STICKER)))

    found, limit = (report.rich_message.html for report in _reports(harness))
    assert "<p>📊 SauceNAO: 100/100 used (24 h) · shared key</p>" in found
    assert "<h3>⏳ SauceNAO: quota used up</h3>" in limit
    assert "<p>📊 SauceNAO: 100/100 used (24 h) · shared key</p>" in limit
    assert route.call_count == 1


# --- the plain report, when the rich one is rejected --------------------------


@pytest.mark.parametrize(
    "error",
    [REJECTED, TelegramNotFound(method=SendRichMessage(chat_id=1, rich_message={"html": "x"}), message="Not Found")],
    ids=["rejected", "unknown-method"],
)
async def test_rejected_rich_report_falls_back_to_a_plain_one(harness, found, error):
    harness.session.errors[SendRichMessage] = error
    media_msg, command = _sauce(PUBLIC_GROUP)

    await harness.feed(command)

    [plain] = _admin_messages(harness)
    assert plain.disable_notification is True
    assert plain.text == (
        "<b>✅ SauceNAO: found</b>\n"
        "<blockquote><b>One Piece (Ep. 12)</b>\n<b>Year: </b>1999-1999\n<b>Time: </b>00:12:33 / 00:24:40</blockquote>\n"
        '• 93.1% <b>One Piece</b> · <a href="https://anidb.net/anime/69">AniDB</a>'
        ' · <a href="https://myanimelist.net/anime/21/">MAL</a> · <a href="https://anilist.co/anime/21/">AniList</a>\n'
        '👤 <a href="tg://user?id=1234567">Test User</a> · @test_user · <code>1234567</code>\n'
        '💬 <a href="https://t.me/example_art">Anime Art &lt;Fans&gt;</a> · <code>-1001234567890</code>'
        f' · <a href="https://t.me/example_art/{media_msg["message_id"]}">message</a>\n'
        "📊 SauceNAO: 10/100 used (24 h) · shared key\n"
        f'via @{BOT_USERNAME} · <a href="https://saucenao.com/search.php?url={IMG}photo-large-id">SauceNAO</a>'
    )
    # the picture, as a reply to its report
    [photo] = harness.session.calls(SendPhoto)
    assert (photo.chat_id, photo.photo) == (ADMIN_ID, "photo-large-id")
    [sent] = [sent for method, sent in harness.session.sent if method is plain]
    assert photo.reply_parameters.message_id == sent.message_id
    assert photo.disable_notification is True


async def test_plain_report_resends_the_original_media_not_the_thumbnail(harness, found):
    harness.session.errors[SendRichMessage] = REJECTED

    await harness.feed(update(message(sticker={**STICKER, "is_animated": True, "thumbnail": THUMB})))

    [sticker] = harness.session.calls(SendSticker)
    assert (sticker.chat_id, sticker.sticker) == (ADMIN_ID, "sticker-id")


async def test_without_a_public_url_other_media_get_the_plain_report(make_harness, found):
    harness = make_harness(public_url=None)

    await harness.feed(update(message(sticker=STICKER)))

    assert _reports(harness) == []
    assert len(_admin_messages(harness)) == 1
    [sticker] = harness.session.calls(SendSticker)
    assert sticker.sticker == "sticker-id"


async def test_a_sticker_telegram_refuses_gets_the_plain_report(harness, found):
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    await harness.feed(update(message(sticker=STICKER)))

    # /img/ could not serve it either
    assert _reports(harness) == []
    [plain] = _admin_messages(harness)
    assert "<code>file is too big</code>" in plain.text


# --- reports never get in the way of the answer ------------------------------


async def test_a_failing_report_does_not_touch_the_answer(harness, found, caplog):
    harness.session.chat_errors[ADMIN_ID] = TelegramBadRequest(
        method=SendMessage(chat_id=ADMIN_ID, text="x"), message="Bad Request: chat not found"
    )

    await harness.feed(update(message(photo=PHOTO)))

    [edit] = harness.session.calls(EditMessageText)
    assert edit.text.startswith("<b>One Piece (Ep. 12)</b>")
    # rich, then plain: both tried, both failed, only logged
    assert len(_reports(harness)) == 1
    assert len(_admin_messages(harness)) == 1
    assert [record.levelname for record in caplog.records if "report" in record.getMessage()] == ["WARNING", "WARNING"]


async def test_a_broken_report_is_logged_and_the_update_does_not_fail(harness, found, caplog):
    harness.session.errors[SendRichMessage] = RuntimeError("report exploded")

    await harness.feed(update(message(photo=PHOTO)))

    assert len(harness.session.calls(EditMessageText)) == 1
    [record] = [record for record in caplog.records if record.levelname == "ERROR"]
    assert record.getMessage() == "Could not report a SauceNAO search"
    # the error handler did not run: no error report
    assert _admin_messages(harness) == []


# --- switches ----------------------------------------------------------------


async def test_without_activity_reports_a_failed_search_is_an_error_report(make_harness, respx_mock):
    harness = make_harness(report_results=False)
    respx_mock.post(SEARCH_URL).respond(400, text="boom")

    await harness.feed(update(message(photo=PHOTO)))

    assert _reports(harness) == []
    [report] = _admin_messages(harness)
    assert report.text == (
        "⚠ <b>Error</b>\n<code>SauceNAO search failed for https://example.org/mikke/img/42/photo-large-id\n"
        "SauceNaoError: HTTP 400</code>"
    )


async def test_without_activity_reports_other_searches_are_not_reported(make_harness, found):
    harness = make_harness(report_results=False)

    await harness.feed(update(message(photo=PHOTO)))

    assert {type(call) for call in harness.session.requests} == {SendMessage, GetFile, EditMessageText}
    assert _admin_messages(harness) == []


async def test_reports_can_be_switched_off(make_harness, respx_mock):
    harness = make_harness(report_results=False, report_errors=False)
    respx_mock.post(SEARCH_URL).respond(500, text="boom")

    await harness.feed(update(message(photo=PHOTO)))

    assert [call.chat_id for call in harness.session.calls(SendMessage)] == [PRIVATE["id"]]
    assert _reports(harness) == []


# --- users' own SauceNAO keys ------------------------------------------------


async def test_a_search_with_the_users_own_key_says_so(harness, found):
    await harness.keys.set(7, USER_KEY)

    await harness.feed(update(message(photo=PHOTO)))

    html = _report(harness)
    assert f"<footer>via @{BOT_USERNAME} · 🔑 own key · " in html
    assert USER_KEY not in html


async def test_a_rejected_key_is_noted_in_the_report_and_as_an_event(harness, respx_mock):
    responses = {USER_KEY: httpx.Response(403, json=UNKNOWN_KEY), API_KEY: httpx.Response(200, json=sauce_response([]))}
    respx_mock.post(SEARCH_URL).mock(side_effect=lambda request: responses[request.url.params["api_key"]])
    await harness.keys.set(7, USER_KEY)

    await harness.feed(update(message(photo=PHOTO)))

    assert "🔑 own key rejected, shared key" in _report(harness)
    [event] = _admin_messages(harness)
    assert event.text == (
        f'<b>🔑 SauceNAO rejected a saved key</b>\n👤 <a href="tg://user?id=7">User</a> · <code>7</code>\nvia @{BOT_USERNAME}'
    )
    assert event.disable_notification is True


async def test_key_events_follow_the_activity_switch(make_harness, respx_mock):
    harness = make_harness(report_results=False)
    respx_mock.get(SEARCH_URL).respond(json=sauce_response([]))

    await harness.feed(update(message(text=USER_KEY)))

    assert await harness.keys.get(7) is not None
    assert _admin_messages(harness) == []
