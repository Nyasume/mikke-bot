from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

from mikke import texts
from mikke.media import Media
from mikke.reports import Outcome
from mikke.saucenao import SEARCH_URL
from mikke.search import CACHE_TTL_SECONDS
from payloads import ANIME, BOT_TOKEN, PUBLIC_URL, sauce_response, telegram_file_error

PHOTO = Media("photo-file-id", "photo-unique", "photo", "photo-file-id")


async def test_found_result_is_rendered(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text.startswith("<b>One Piece (Ep. 12)</b>")
    assert answer.keyboard.inline_keyboard[0][0].text == "View on AniDB"
    # the image went to SauceNAO as bytes, never as a Telegram URL carrying the token
    request = route.calls.last.request
    assert BOT_TOKEN.encode() not in request.content
    assert BOT_TOKEN not in str(request.url)
    # what the owner report needs; the searcher itself sends nothing but getFile
    assert (answer.outcome.status, answer.outcome.cached) == ("found", False)
    [hit] = answer.outcome.hits
    assert (hit.title, hit.similarity) == ("One Piece", 93.1)
    assert {type(call) for call in harness.session.requests} == {GetFile}


async def test_cache_hit_skips_download_and_api(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    first = await harness.searcher.search_file(harness.bot, PHOTO)
    # same image, different file_id (e.g. forwarded again): same file_unique_id
    second = await harness.searcher.search_file(harness.bot, Media("another-id", "photo-unique", "photo", "another-id"))

    assert (second.text, second.keyboard) == (first.text, first.keyboard)
    assert (first.outcome.cached, second.outcome.cached) == (False, True)
    assert second.outcome.hits == first.outcome.hits
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
    assert [button.text for row in answer.keyboard.inline_keyboard for button in row] == [
        "Google Lens",
        "Yandex",
        "Bing",
        "SauceNAO",
        "ascii2d",
        "TinEye",
    ]
    assert (
        f"{PUBLIC_URL}/img/42/photo-file-id".replace(":", "%3A").replace("/", "%2F")
        in answer.keyboard.inline_keyboard[0][0].url
    )
    assert (again.text, again.keyboard) == (answer.text, answer.keyboard)
    assert (answer.outcome, again.outcome) == (Outcome("not_found"), Outcome("not_found", cached=True))
    assert route.call_count == 1


async def test_exhausted_quota_answers_at_once_without_download_or_api(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=0))
    await harness.searcher.search_file(harness.bot, Media("first-id", "first-u", "photo", "first-id"))
    requests_before = len(harness.session.requests)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.LIMIT_REACHED
    assert answer.outcome == Outcome("limit")
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
    assert answer.outcome == Outcome("limit")


async def test_api_error_answers_error(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(500, text="boom")

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.ERROR
    assert answer.outcome.status == "error"
    assert answer.outcome.error.startswith("SauceNaoError: HTTP 500")
    # errors are not cached
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    assert (await harness.searcher.search_file(harness.bot, PHOTO)).text.startswith("<b>One Piece")


async def test_file_telegram_refuses_is_invalid(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.INVALID_FILE
    assert answer.outcome == Outcome("invalid_file", error="file is too big")
    assert route.call_count == 0


async def test_without_public_url_there_are_no_fallback_links(respx_mock, make_harness):
    harness = make_harness(public_url=None)
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.NO_RESULT
    assert answer.keyboard is None


async def test_url_search_uses_the_url_itself(respx_mock, harness):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([]))

    answer = await harness.searcher.search_url("https://example.com/pic.png")

    assert route.calls.last.request.url.params["url"] == "https://example.com/pic.png"
    assert "example.com%2Fpic.png" in answer.keyboard.inline_keyboard[0][0].url
    assert harness.session.requests == []


async def test_download_failure_keeps_the_token_out_of_the_outcome_and_logs(respx_mock, harness, monkeypatch, caplog):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    async def failing_stream(*args, **kwargs):
        raise telegram_file_error(BOT_TOKEN)
        yield b""  # pragma: no cover

    monkeypatch.setattr(harness.session, "stream_content", failing_stream)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.ERROR
    assert route.call_count == 0
    assert answer.outcome.error == "DownloadError: ClientResponseError HTTP 502"
    assert BOT_TOKEN not in caplog.text
