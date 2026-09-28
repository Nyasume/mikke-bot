import asyncio

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

from mikke import texts
from mikke.keys import UserKey
from mikke.media import Media
from mikke.reports import Outcome
from mikke.saucenao import SEARCH_URL
from mikke.search import ADD_KEY_CALLBACK, CACHE_TTL_SECONDS, Asker, KeyLocks
from payloads import (
    ANIME,
    API_KEY,
    BOT_TOKEN,
    DAILY_LIMIT,
    PUBLIC_URL,
    UNKNOWN_KEY,
    USER_KEY,
    sauce_response,
    telegram_file_error,
)

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


async def test_daily_quota_used_up_answers_at_once_without_download_or_api(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], long_remaining=0))
    await harness.searcher.search_file(harness.bot, Media("first-id", "first-u", "photo", "first-id"))
    requests_before = len(harness.session.requests)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text == texts.LIMIT_REACHED
    assert answer.outcome == Outcome("limit")
    assert answer.keyboard.inline_keyboard[0][0].text == "Google Lens"
    assert route.call_count == 1
    assert len(harness.session.requests) == requests_before  # not even getFile


async def test_a_full_30_second_window_waits_and_downloads_only_then(respx_mock, harness, monkeypatch):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], short_remaining=0))
    await harness.searcher.search_file(harness.bot, Media("first-id", "first-u", "photo", "first-id"))
    downloaded_at = []
    get_file = harness.bot.get_file

    async def timed_get_file(file_id):
        downloaded_at.append(harness.clock.now)
        return await get_file(file_id)

    monkeypatch.setattr(harness.bot, "get_file", timed_get_file)

    answer = await harness.searcher.search_file(harness.bot, PHOTO)

    assert answer.text.startswith("<b>One Piece")
    assert route.call_count == 2
    assert downloaded_at == [1_000.0 + 30]


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


@pytest.mark.parametrize("data", [["not", "a", "mapping"], {"title": "Art", "ext_urls": 5}], ids=["list", "ext-urls"])
async def test_a_result_that_cannot_be_shown_is_an_error_and_is_not_cached(respx_mock, harness, data):
    route = respx_mock.post(SEARCH_URL).respond(
        json=sauce_response([{"header": {"similarity": "95.00"}, "data": data}])
    )

    answer = await harness.searcher.search_file(harness.bot, PHOTO)
    await harness.searcher.search_file(harness.bot, PHOTO)

    assert (answer.text, answer.outcome.status) == (texts.ERROR, "error")
    assert route.call_count == 2


# --- the same image, searched at the same time ---------------------------------


async def test_concurrent_searches_of_the_same_image_ask_saucenao_once(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    harness.session.slow = True

    first, second = await asyncio.gather(
        harness.searcher.search_file(harness.bot, PHOTO),
        # forwarded again: another file_id, the same file_unique_id
        harness.searcher.search_file(harness.bot, Media("another-id", "photo-unique", "photo", "another-id")),
    )

    assert route.call_count == 1
    assert len(harness.session.calls(GetFile)) == 1
    assert (first.outcome.cached, second.outcome.cached) == (False, True)
    assert (second.text, second.keyboard) == (first.text, first.keyboard)


@pytest.mark.parametrize(
    ("shared", "fail_download", "status"),
    [
        (httpx.Response(500, text="boom"), False, "error"),
        (httpx.Response(429, json=DAILY_LIMIT), False, "limit"),
        (httpx.Response(200, json=sauce_response([ANIME])), True, "invalid_file"),
    ],
    ids=["error", "limit", "invalid-file"],
)
async def test_a_search_that_waited_for_a_failed_one_runs_its_own_with_its_own_key(
    respx_mock, harness, shared, fail_download, status
):
    route = respx_mock.post(SEARCH_URL).mock(
        side_effect=_by_key({API_KEY: shared, USER_KEY: httpx.Response(200, json=sauce_response([ANIME]))})
    )
    await harness.keys.set(7, USER_KEY)
    harness.session.slow = True
    if fail_download:
        harness.session.fail_once[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big")

    automatic, asked = await asyncio.gather(
        harness.searcher.search_file(harness.bot, PHOTO),
        harness.searcher.search_file(harness.bot, PHOTO, Asker(7)),
    )

    # nothing of the first one was cached, so the second one searched again, with the key of whoever asked
    assert automatic.outcome.status == status
    assert (asked.outcome.status, asked.outcome.cached, asked.outcome.key) == ("found", False, "own")
    assert _keys_used(route)[-1] == USER_KEY


async def test_a_lock_per_image_lives_only_while_it_is_held_or_waited_for():
    locks = KeyLocks()
    order = []

    async def search(name: str) -> None:
        async with locks.hold("photo-unique"):
            order.append(name)
            await asyncio.sleep(0)

    first = asyncio.create_task(search("first"))
    second = asyncio.create_task(search("second"))
    cancelled = asyncio.create_task(search("cancelled"))
    await asyncio.sleep(0)
    assert len(locks) == 1
    cancelled.cancel()
    await asyncio.gather(first, second, cancelled, return_exceptions=True)

    assert order == ["first", "second"]
    assert len(locks) == 0


# --- whose key ---------------------------------------------------------------


def _by_key(responses: dict[str, httpx.Response]):
    """A respx side effect answering each API key its own way."""
    return lambda request: responses[request.url.params["api_key"]]


def _keys_used(route) -> list[str]:
    return [call.request.url.params["api_key"] for call in route.calls]


def _layout(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


async def test_the_askers_own_key_searches_for_them(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))
    await harness.keys.set(7, USER_KEY)

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7))

    assert _keys_used(route) == [USER_KEY]
    assert answer.outcome.key == "own"
    assert answer.text.startswith("<b>One Piece")


async def test_without_an_asker_or_a_key_the_shared_key_searches(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([]))
    await harness.keys.set(7, USER_KEY)

    automatic = await harness.searcher.search_file(harness.bot, PHOTO)
    keyless = await harness.searcher.search_file(harness.bot, Media("b-id", "b-u", "photo", "b-id"), Asker(8))

    assert _keys_used(route) == [API_KEY, API_KEY]
    assert automatic.outcome.key is keyless.outcome.key is None


async def test_an_own_key_used_up_for_the_day_falls_back_to_the_shared_key(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).mock(
        side_effect=_by_key(
            {
                USER_KEY: httpx.Response(429, json=DAILY_LIMIT),
                API_KEY: httpx.Response(200, json=sauce_response([ANIME])),
            }
        )
    )
    await harness.keys.set(7, USER_KEY)

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))
    again = await harness.searcher.search_file(harness.bot, Media("b-id", "b-u", "photo", "b-id"), Asker(7))

    assert answer.text.startswith("<b>One Piece")
    assert (answer.outcome.key, again.outcome.key) == ("used_up", "used_up")
    # the user's key rests; the file was downloaded once per search
    assert _keys_used(route) == [USER_KEY, API_KEY, API_KEY]
    assert len(harness.session.calls(GetFile)) == 2
    # still a valid key, nothing to tell the user
    assert await harness.keys.get(7) == UserKey(USER_KEY, valid=True)


async def test_a_rejected_key_falls_back_and_the_user_is_told_once(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).mock(
        side_effect=_by_key(
            {
                USER_KEY: httpx.Response(403, json=UNKNOWN_KEY),
                API_KEY: httpx.Response(200, json=sauce_response([ANIME])),
            }
        )
    )
    await harness.keys.set(7, USER_KEY)
    pictures = [Media(f"{n}-id", f"{n}-u", "photo", f"{n}-id") for n in range(3)]

    answers = await asyncio.gather(*(harness.searcher.search_file(harness.bot, media, Asker(7)) for media in pictures))

    assert all(answer.text.startswith("<b>One Piece") for answer in answers)
    [told] = [answer for answer in answers if texts.KEY_REJECTED in answer.text]
    assert told.text.endswith(f"\n\n{texts.KEY_REJECTED}")
    assert sorted(str(answer.outcome.key) for answer in answers) == ["None", "None", "rejected"]
    assert _keys_used(route).count(API_KEY) == 3
    assert await harness.keys.get(7) == UserKey(USER_KEY, valid=False)

    # from now on the shared key searches for them, without a word
    later = await harness.searcher.search_file(harness.bot, Media("d-id", "d-u", "photo", "d-id"), Asker(7))
    assert (later.outcome.key, _keys_used(route)[-1]) == (None, API_KEY)
    assert texts.KEY_REJECTED not in later.text


async def test_an_own_keys_30_second_line_does_not_spill_into_the_shared_one(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).mock(
        side_effect=_by_key(
            {
                USER_KEY: httpx.Response(429, text="Search Rate Too High"),
                API_KEY: httpx.Response(200, json=sauce_response([ANIME])),
            }
        )
    )
    await harness.keys.set(7, USER_KEY)

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))

    # someone with a key of their own is not offered one
    assert answer.text == texts.LIMIT_REACHED
    assert answer.outcome == Outcome("limit", key="own")
    assert set(_keys_used(route)) == {USER_KEY}


# --- what a limit answer says ------------------------------------------------


@pytest.fixture
def used_up(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(429, json=DAILY_LIMIT)


async def test_a_limit_in_private_offers_a_key_of_ones_own(used_up, harness):
    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))

    assert answer.text == f"{texts.LIMIT_REACHED}\n\n{texts.KEY_PITCH}"
    assert _layout(answer.keyboard) == [
        ["Google Lens", "Yandex", "Bing"],
        ["SauceNAO", "ascii2d", "TinEye"],
        ["🔑 Add my free key"],
    ]
    assert answer.keyboard.inline_keyboard[-1][0].callback_data == ADD_KEY_CALLBACK


async def test_a_30_second_limit_in_private_offers_it_too(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(429, text="Search Rate Too High")

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))

    assert answer.text == f"{texts.LIMIT_REACHED}\n\n{texts.KEY_PITCH}"


@pytest.mark.parametrize("asker", [Asker(7), None], ids=["group-or-inline", "automatic"])
async def test_a_limit_elsewhere_is_kept_short(used_up, harness, asker):
    answer = await harness.searcher.search_file(harness.bot, PHOTO, asker)

    assert answer.text == texts.LIMIT_REACHED
    assert len(answer.keyboard.inline_keyboard) == 2


async def test_no_pitch_for_someone_whose_own_key_is_used_up_too(used_up, harness):
    await harness.keys.set(7, USER_KEY)

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))

    assert answer.text == texts.LIMIT_REACHED
    assert answer.outcome == Outcome("limit", key="used_up")


async def test_a_rejected_key_in_private_comes_with_the_button(respx_mock, harness):
    respx_mock.post(SEARCH_URL).mock(
        side_effect=_by_key(
            {USER_KEY: httpx.Response(403, json=UNKNOWN_KEY), API_KEY: httpx.Response(429, json=DAILY_LIMIT)}
        )
    )
    await harness.keys.set(7, USER_KEY)

    answer = await harness.searcher.search_file(harness.bot, PHOTO, Asker(7, private=True))

    assert answer.text == f"{texts.LIMIT_REACHED}\n\n{texts.KEY_REJECTED}"
    assert _layout(answer.keyboard)[-1] == ["🔑 Add my free key"]
