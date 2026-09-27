import httpx
import pytest

from mikke.saucenao import (
    LONG_WINDOW_RETRY_SECONDS,
    SEARCH_URL,
    SHORT_WINDOW_SECONDS,
    QuotaExceededError,
    SauceNao,
    SauceNaoError,
)
from payloads import ANIME, API_KEY, IMAGE, Clock, sauce_response


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def saucenao(http: httpx.AsyncClient, clock: Clock) -> SauceNao:
    return SauceNao(http, API_KEY, clock=clock)


async def test_uploads_image_bytes_with_api_params(respx_mock, saucenao):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))

    results = await saucenao.search(image=IMAGE, filename="file_1.jpg")

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


async def test_short_window_exhausted_blocks_without_calling_the_api(respx_mock, saucenao, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=0))

    await saucenao.search(image=IMAGE)
    assert saucenao.exhausted
    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    assert route.call_count == 1

    clock.now += SHORT_WINDOW_SECONDS + 1
    assert not saucenao.exhausted
    await saucenao.search(image=IMAGE)
    assert route.call_count == 2


async def test_daily_window_exhausted_blocks_longer(respx_mock, saucenao, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=3, long_remaining=0))

    await saucenao.search(image=IMAGE)
    clock.now += SHORT_WINDOW_SECONDS + 1
    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    assert route.call_count == 1

    clock.now += LONG_WINDOW_RETRY_SECONDS
    assert not saucenao.exhausted


async def test_http_429_counts_as_limit_reached(respx_mock, saucenao, clock):
    route = respx_mock.post(SEARCH_URL).respond(429, text="Search Rate Too High")

    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    assert route.call_count == 1

    clock.now += SHORT_WINDOW_SECONDS + 1
    assert not saucenao.exhausted


async def test_http_429_daily_limit_blocks_for_the_long_window(respx_mock, saucenao, clock):
    respx_mock.post(SEARCH_URL).respond(
        429, json={"header": {"status": -2, "message": "Daily Search Limit Exceeded."}}
    )

    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    clock.now += SHORT_WINDOW_SECONDS + 1
    assert saucenao.exhausted


async def test_server_error_raises(respx_mock, saucenao):
    respx_mock.post(SEARCH_URL).respond(503, text="down")
    with pytest.raises(SauceNaoError):
        await saucenao.search(image=IMAGE)


async def test_failed_search_status_without_results_raises(respx_mock, saucenao):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([], status=1))
    with pytest.raises(SauceNaoError):
        await saucenao.search(image=IMAGE)


async def test_partial_failure_still_returns_results(respx_mock, saucenao):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME], status=1))
    assert await saucenao.search(image=IMAGE) == [ANIME]


async def test_client_side_status_is_no_result(respx_mock, saucenao):
    respx_mock.post(SEARCH_URL).respond(json=sauce_response([], status=-3))
    assert await saucenao.search(image=IMAGE) == []


async def test_concurrent_burst_is_capped_before_the_headers_say_so(respx_mock, saucenao, clock):
    # every response still reports quota left, as when requests overlap
    route = respx_mock.post(SEARCH_URL).respond(json=sauce_response([], short_remaining=3))

    for _ in range(4):
        await saucenao.search(image=IMAGE)
    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    assert route.call_count == 4

    clock.now += SHORT_WINDOW_SECONDS
    await saucenao.search(image=IMAGE)
    assert route.call_count == 5


async def test_short_limit_follows_the_account(respx_mock, saucenao):
    response = sauce_response([], short_remaining=5)
    response["header"]["short_limit"] = "6"
    route = respx_mock.post(SEARCH_URL).respond(json=response)

    for _ in range(6):
        await saucenao.search(image=IMAGE)
    with pytest.raises(QuotaExceededError):
        await saucenao.search(image=IMAGE)
    assert route.call_count == 6
