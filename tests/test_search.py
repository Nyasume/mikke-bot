from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile, SendMessage, SendPhoto, SendSticker

from payloads import ADMIN_ID, ANIME, BOT_TOKEN, PUBLIC_URL, sauce_response
from reverse_search_bot import texts
from reverse_search_bot.media import Media
from reverse_search_bot.saucenao import SEARCH_URL
from reverse_search_bot.search import CACHE_TTL_SECONDS

PHOTO = Media("photo-file-id", "photo-unique", "photo", "photo-file-id")
ANIMATED_STICKER = Media("sticker-thumb-id", "thumb-unique", "sticker", "sticker-file-id")


def _admin_messages(harness) -> list[SendMessage]:
    return [call for call in harness.session.calls(SendMessage) if call.chat_id == ADMIN_ID]


async def test_found_result_is_rendered_and_reported(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text.startswith("<b>One Piece (Ep. 12)</b>")
    assert answer.keyboard.inline_keyboard[0][0].text == "View on AniDB"
    # the image went to SauceNAO as bytes, never as a Telegram URL carrying the token
    request = route.calls.last.request
    assert BOT_TOKEN.encode() not in request.content
    assert BOT_TOKEN not in str(request.url)

    [report] = _admin_messages(harness)
    assert "https://saucenao.com/search.php?url=https%3A%2F%2Fexample.org%2Frsbot%2Fimg%2Fphoto-file-id" in report.text
    assert report.text.endswith(answer.text)
    assert BOT_TOKEN not in report.text
    [photo] = harness.session.calls(SendPhoto)
    assert (photo.chat_id, photo.photo) == (ADMIN_ID, "photo-file-id")


async def test_report_resends_the_original_media_not_the_thumbnail(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    await harness.searcher.search_file(harness.bot, ANIMATED_STICKER)

    assert harness.session.calls(GetFile)[0].file_id == "sticker-thumb-id"
    [sticker] = harness.session.calls(SendSticker)
    assert sticker.sticker == "sticker-file-id"


async def test_reports_can_be_switched_off(respx_mock, make_harness):
    harness = make_harness(report_results=False, report_errors=False)
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    await harness.searcher.search_file(harness.bot, PHOTO)

    respx_mock.post(SEARCH_URL).respond(500)
    answer = await harness.searcher.search_file(harness.bot, Media("other-id", "other-u", "photo", "other-id"))

    assert answer.text == texts.ERROR
    assert {type(call) for call in harness.session.requests} == {GetFile}  # nothing sent to the admin


async def test_cache_hit_skips_download_and_api(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    first = await harness.searcher.search_file(harness.bot, PHOTO)
    # same image, different file_id (e.g. forwarded again): same file_unique_id
    second = await harness.searcher.search_file(harness.bot, Media("another-id", "photo-unique", "photo", "another-id"))

    assert second == first
    assert route.call_count == 1
    assert len(harness.session.calls(GetFile)) == 1


async def test_cache_expires_after_a_day(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    await harness.searcher.search_file(harness.bot, PHOTO)
    harness.clock.now += CACHE_TTL_SECONDS - 1
    await harness.searcher.search_file(harness.bot, PHOTO)
    assert route.call_count == 1

    harness.clock.now += 2
    await harness.searcher.search_file(harness.bot, PHOTO)
    assert route.call_count == 2


async def test_no_result_is_cached_and_offers_fallback_links(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))

    answer = await harness.searcher.search_file(harness.bot, PHOTO)
    again = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.NO_RESULT
    assert [button.text for button in answer.keyboard.inline_keyboard[0]] == ["Google Lens", "SauceNAO", "TinEye"]
    assert f"{PUBLIC_URL}/img/photo-file-id".replace(":", "%3A").replace("/", "%2F") in answer.keyboard.inline_keyboard[0][0].url
    assert again == answer
    assert route.call_count == 1
    assert _admin_messages(harness) == []


async def test_exhausted_quota_answers_at_once_without_download_or_api(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=0))
    await harness.searcher.search_file(harness.bot, Media("first-id", "first-u", "photo", "first-id"))
    requests_before = len(harness.session.requests)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.LIMIT_REACHED
    assert answer.keyboard.inline_keyboard[0][0].text == "Google Lens"
    assert route.call_count == 1
    assert len(harness.session.requests) == requests_before  # not even getFile


async def test_cached_results_are_served_while_quota_is_exhausted(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], short_remaining=0))

    await harness.searcher.search_file(harness.bot, PHOTO)
    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text.startswith("<b>One Piece")


async def test_http_429_answers_limit_reached(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(429)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.LIMIT_REACHED
    assert _admin_messages(harness) == []


async def test_api_error_answers_error_and_reports_it(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(500, text="boom")

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.ERROR
    [report] = _admin_messages(harness)
    assert "SauceNaoError" in report.text
    assert "HTTP 500" in report.text
    # errors are not cached
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    assert (await harness.searcher.search_file(harness.bot, PHOTO)).text.startswith("<b>One Piece")


async def test_file_telegram_refuses_is_invalid(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.INVALID_FILE
    assert route.call_count == 0


async def test_without_public_url_there_are_no_fallback_links(respx_mock, make_harness):
    harness = make_harness(public_url=None)
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.NO_RESULT
    assert answer.keyboard is None


async def test_url_search_uses_the_url_itself(respx_mock, harness):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([]))

    answer = await harness.searcher.search_url(harness.bot, "https://example.com/pic.png")

    assert route.calls.last.request.url.params["url"] == "https://example.com/pic.png"
    assert "example.com%2Fpic.png" in answer.keyboard.inline_keyboard[0][0].url
    assert harness.session.requests == []
