import asyncio

import httpx
import pytest

from mikke.saucenao import (
    LONG_WINDOW_RETRY_SECONDS,
    MAX_WAIT_SECONDS,
    PROBE_URL,
    SEARCH_URL,
    SHORT_WINDOW_SECONDS,
    Account,
    InvalidKeyError,
    QuotaExceededError,
    SauceNao,
    SauceNaoError,
)
from payloads import ANIME, API_KEY, DAILY_LIMIT, IMAGE, UNKNOWN_KEY, Clock, sauce_response

START = 1_000.0


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def saucenao(http: httpx.AsyncClient, clock: Clock) -> SauceNao:
    return SauceNao(http, API_KEY, clock=clock, sleep=clock.sleep)


class Uploads:
    """Upload callables for `search`, noting when each image was fetched."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.times: list[float] = []

    def __call__(self, filename: str = "image.jpg"):
        async def upload() -> tuple[bytes, str]:
            self.times.append(self._clock.now)
            return IMAGE, filename

        return upload


@pytest.fixture
def uploads(clock: Clock) -> Uploads:
    return Uploads(clock)


async def _burst(saucenao: SauceNao, uploads: Uploads, count: int) -> list:
    return await asyncio.gather(*(saucenao.search(upload=uploads()) for _ in range(count)), return_exceptions=True)


async def test_uploads_image_bytes_with_api_params(respx_mock, saucenao, uploads):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    results = await saucenao.search(upload=uploads("file_1.jpg"))

    assert results == [ANIME]
    request = route.calls.last.request
    assert dict(request.url.params) == {
        "output_type": "2",
        "api_key": API_KEY,
        "db": "999",
        "numres": "5",
        "testmode": "1",
    }
    body = request.content
    assert b'name="file"; filename="file_1.jpg"' in body
    assert IMAGE in body
    assert b"api.telegram.org" not in body


async def test_searches_public_url_by_get(respx_mock, saucenao):
    route = respx_mock.get(SEARCH_URL).respond(json=sauce_response([]))

    assert await saucenao.search(url="https://example.com/a.png") == []
    assert route.calls.last.request.url.params["url"] == "https://example.com/a.png"


# --- the 30-second window: a line, not a refusal ---------------------------------


async def test_a_burst_of_ten_waits_in_line_and_every_search_goes_out(respx_mock, saucenao, uploads, clock):
    # every response still reports quota left, as when requests overlap
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], short_remaining=3))

    results = await _burst(saucenao, uploads, 10)

    assert results == [[ANIME]] * 10
    assert route.call_count == 10
    # four at a time, 30 seconds apart, and nobody's file fetched before their turn
    assert uploads.times == [START] * 4 + [START + 30] * 4 + [START + 60] * 2
    assert clock.now == START + 60


async def test_a_search_that_would_wait_too_long_ends_as_limit_at_once(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=3))
    refused_at = []

    async def search():
        try:
            return await saucenao.search(upload=uploads())
        except QuotaExceededError as e:
            refused_at.append(clock.now)
            return e

    results = await asyncio.gather(*(search() for _ in range(13)))

    # 4 go now, 4 in 30 s, 4 in 60 s; the 13th would wait 90 s
    [refused] = [result for result in results if isinstance(result, QuotaExceededError)]
    assert refused.daily is False
    assert refused_at == [START]
    assert route.call_count == len(uploads.times) == 12
    assert MAX_WAIT_SECONDS == 60


async def test_a_full_window_makes_the_next_search_wait_for_it(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=0))

    await saucenao.search(upload=uploads())
    await saucenao.search(upload=uploads())

    assert route.call_count == 2
    assert uploads.times == [START, START + SHORT_WINDOW_SECONDS]


async def test_a_short_429_puts_the_search_back_in_line(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL)
    route.side_effect = [
        httpx.Response(429, text="Search Rate Too High"),
        httpx.Response(200, json=sauce_response([ANIME])),
    ]

    assert await saucenao.search(upload=uploads()) == [ANIME]

    assert route.call_count == 2
    # the image was fetched once and sent again
    assert uploads.times == [START]
    assert IMAGE in route.calls.last.request.content
    assert clock.now == START + SHORT_WINDOW_SECONDS


async def test_short_429s_until_the_wait_runs_out_end_as_limit(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(429, text="Search Rate Too High")

    with pytest.raises(QuotaExceededError) as raised:
        await saucenao.search(upload=uploads())

    assert raised.value.daily is False
    assert route.call_count == 3  # now, in 30 s and in 60 s
    assert clock.now == START + MAX_WAIT_SECONDS


async def test_short_limit_follows_the_account(respx_mock, saucenao, uploads):
    response = sauce_response([], short_remaining=5)
    response["header"]["short_limit"] = "6"
    respx_mock.post(SEARCH_URL).respond(json=response)

    await saucenao.search(upload=uploads())
    await _burst(saucenao, uploads, 6)

    assert uploads.times == [START] * 6 + [START + 30]


async def test_a_failed_download_gives_its_slot_back(respx_mock, saucenao, uploads):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=3))

    async def failing_upload() -> tuple[bytes, str]:
        raise RuntimeError("download failed")

    with pytest.raises(RuntimeError):
        await saucenao.search(upload=failing_upload)
    await _burst(saucenao, uploads, 4)

    assert route.call_count == 4
    assert uploads.times == [START] * 4


# --- the daily window --------------------------------------------------------


async def test_daily_window_used_up_rests_the_key_without_calling_the_api(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=3, long_remaining=0))

    await saucenao.search(upload=uploads())
    clock.now += SHORT_WINDOW_SECONDS + 1
    with pytest.raises(QuotaExceededError) as raised:
        await saucenao.search(upload=uploads())
    assert raised.value.daily is True
    assert route.call_count == len(uploads.times) == 1

    clock.now += LONG_WINDOW_RETRY_SECONDS
    await saucenao.search(upload=uploads())
    assert route.call_count == 2


async def test_http_429_daily_limit_rests_the_key(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(429, json=DAILY_LIMIT)

    for _ in range(2):
        with pytest.raises(QuotaExceededError) as raised:
            await saucenao.search(upload=uploads())
        assert raised.value.daily is True
        clock.now += SHORT_WINDOW_SECONDS + 1
    assert route.call_count == 1


async def test_searches_in_line_give_up_when_the_daily_window_runs_out(respx_mock, saucenao, uploads):
    left = [5]

    def respond(request: httpx.Request) -> httpx.Response:
        # the fifth search uses up the day; one sent at the same time would get a 429
        if not left[0]:
            return httpx.Response(429, json=DAILY_LIMIT)
        left[0] -= 1
        return httpx.Response(200, json=sauce_response([ANIME], long_remaining=left[0]))

    route = respx_mock.post(SEARCH_URL).mock(side_effect=respond)

    results = await _burst(saucenao, uploads, 8)

    assert results[:5] == [[ANIME]] * 5
    assert [result.daily for result in results[5:]] == [True, True, True]
    # the searches still in line never fetched their files
    assert route.call_count == len(uploads.times) < 8


# --- the key -----------------------------------------------------------------


async def test_an_unknown_key_is_rejected_and_rests(respx_mock, saucenao, uploads, clock):
    route = respx_mock.post(SEARCH_URL).respond(403, json=UNKNOWN_KEY)

    with pytest.raises(InvalidKeyError):
        await saucenao.search(upload=uploads())
    with pytest.raises(InvalidKeyError):
        await saucenao.search(upload=uploads())
    assert route.call_count == 1

    clock.now += LONG_WINDOW_RETRY_SECONDS
    with pytest.raises(InvalidKeyError):
        await saucenao.search(upload=uploads())
    assert route.call_count == 2


async def test_cloudflares_403_page_is_an_error_not_a_rejected_key(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(403, text="<!DOCTYPE html><title>Just a moment...</title>")

    with pytest.raises(SauceNaoError):
        await saucenao.search(upload=uploads())


async def test_account_checks_the_key_with_a_search_by_url(respx_mock, saucenao):
    response = sauce_response([], short_remaining=3, long_remaining=97)
    response["header"]["account_type"] = "1"
    route = respx_mock.get(SEARCH_URL).respond(json=response)

    account = await saucenao.account()

    assert account == Account(type=1, short_limit=4, long_limit=100, long_remaining=97)
    assert account.plan == "Basic (free)"
    assert route.calls.last.request.url.params["url"] == PROBE_URL


async def test_each_key_has_its_own_limits(respx_mock, saucenao, uploads, http, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=0, long_remaining=0))
    other = saucenao.with_key("another-key")

    await saucenao.search(upload=uploads())
    await other.search(upload=uploads())

    assert [call.request.url.params["api_key"] for call in route.calls] == [API_KEY, "another-key"]
    assert uploads.times == [START, START]
    assert saucenao.with_key(API_KEY) is saucenao


async def test_error_texts_hide_the_key(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(500, text=f"Internal error for key {API_KEY}")

    with pytest.raises(SauceNaoError) as raised:
        await saucenao.search(upload=uploads())

    assert API_KEY not in str(raised.value)
    assert "[key]" in str(raised.value)


# --- answers -----------------------------------------------------------------


async def test_server_error_raises(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(503, text="down")
    with pytest.raises(SauceNaoError):
        await saucenao.search(upload=uploads())


async def test_failed_search_status_without_results_raises(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([], status=1))
    with pytest.raises(SauceNaoError):
        await saucenao.search(upload=uploads())


async def test_partial_failure_still_returns_results(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], status=1))
    assert await saucenao.search(upload=uploads()) == [ANIME]


async def test_client_side_status_is_no_result(respx_mock, saucenao, uploads):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([], status=-3))
    assert await saucenao.search(upload=uploads()) == []
