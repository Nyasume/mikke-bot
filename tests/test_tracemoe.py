import asyncio

import httpx
import pytest

from mikke.tracemoe import QUOTA_RETRY_SECONDS, SEARCH_URL, QuotaExceededError, TraceMoe, TraceMoeError
from payloads import IMAGE, SCENE, Clock, trace_response

QUOTA_DEPLETED = {"quota": 100, "quotaUsed": 100, "error": "Search quota depleted (quota per 24 hours: 100, used: 100)"}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tracemoe(http: httpx.AsyncClient, clock: Clock) -> TraceMoe:
    return TraceMoe(http, clock=clock)


def _image(filename: str = "image.jpg"):
    """An upload callable for `search`."""

    async def upload() -> tuple[bytes, str]:
        return IMAGE, filename

    return upload


async def test_uploads_image_bytes_with_anilist_info_and_cut_borders(respx_mock, tracemoe):
    route = respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE]))

    assert await tracemoe.search(_image("file_1.jpg")) == [SCENE]

    request = route.calls.last.request
    # both are flags: trace.moe only checks that the parameter is there
    assert dict(request.url.params) == {"anilistInfo": "", "cutBorders": ""}
    assert b'name="image"; filename="file_1.jpg"' in request.content
    assert IMAGE in request.content
    assert "x-trace-key" not in request.headers


async def test_api_key_goes_into_the_x_trace_key_header(respx_mock, http, clock):
    route = respx_mock.post(SEARCH_URL).respond(json=trace_response([]))

    assert await TraceMoe(http, "sponsor-key", clock=clock).search(_image()) == []

    request = route.calls.last.request
    assert request.headers["x-trace-key"] == "sponsor-key"
    assert "sponsor-key" not in str(request.url)


async def test_quota_depleted_blocks_without_calling_the_api(respx_mock, tracemoe, clock):
    route = respx_mock.post(SEARCH_URL).respond(402, json=QUOTA_DEPLETED)

    with pytest.raises(QuotaExceededError):
        await tracemoe.search(_image())
    assert tracemoe.exhausted
    with pytest.raises(QuotaExceededError):
        tracemoe.check_quota()
    with pytest.raises(QuotaExceededError):
        await tracemoe.search(_image())
    assert route.call_count == 1

    clock.now += QUOTA_RETRY_SECONDS
    assert not tracemoe.exhausted
    respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE]))
    assert await tracemoe.search(_image()) == [SCENE]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (402, {"error": "Concurrency limit exceeded"}, "HTTP 402: Concurrency limit exceeded"),
        (503, {"error": "Error: Search queue is full"}, "HTTP 503: Error: Search queue is full"),
        (400, {"error": "Failed to process image"}, "HTTP 400: Failed to process image"),
        (500, None, "HTTP 500: boom"),
        (200, {"error": "something odd", "result": []}, "HTTP 200: something odd"),
    ],
)
async def test_errors_raise_without_blocking(respx_mock, tracemoe, status, body, expected):
    if body is None:
        respx_mock.post(SEARCH_URL).respond(status, text="boom")
    else:
        respx_mock.post(SEARCH_URL).respond(status, json=body)

    with pytest.raises(TraceMoeError, match=expected):
        await tracemoe.search(_image())
    assert not tracemoe.exhausted


async def test_non_json_answer_is_an_error(respx_mock, tracemoe):
    respx_mock.post(SEARCH_URL).respond(200, text="<html>")
    with pytest.raises(TraceMoeError, match="not JSON"):
        await tracemoe.search(_image())


async def test_one_search_at_a_time(respx_mock, tracemoe):
    running = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return httpx.Response(200, json=trace_response([SCENE]))

    route = respx_mock.post(SEARCH_URL).mock(side_effect=slow)

    results = await asyncio.gather(*(tracemoe.search(_image()) for _ in range(3)))

    assert results == [[SCENE]] * 3
    assert route.call_count == 3
    assert peak == 1


async def test_searches_waiting_in_line_stop_once_the_quota_runs_out(respx_mock, tracemoe):
    route = respx_mock.post(SEARCH_URL).respond(402, json=QUOTA_DEPLETED)

    results = await asyncio.gather(*(tracemoe.search(_image()) for _ in range(3)), return_exceptions=True)

    assert all(isinstance(result, QuotaExceededError) for result in results)
    assert route.call_count == 1


# --- the quota, for the owner reports -----------------------------------------


async def test_usage_counts_the_search_it_came_with(respx_mock, tracemoe):
    respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE], quota_used=11))
    assert tracemoe.usage is None

    await tracemoe.search(_image())

    # quotaUsed is from before this search, which trace.moe counts once it has answered
    assert tracemoe.usage == (12, 100)


async def test_a_depleted_quota_is_taken_as_it_is(respx_mock, tracemoe):
    respx_mock.post(SEARCH_URL).respond(402, json=QUOTA_DEPLETED)

    with pytest.raises(QuotaExceededError):
        await tracemoe.search(_image())

    assert tracemoe.usage == (100, 100)


async def test_answers_without_the_quota_leave_the_usage_alone(respx_mock, tracemoe):
    respx_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=trace_response([SCENE], quota_used=11)),
            httpx.Response(402, json={"error": "Concurrency limit exceeded"}),
        ]
    )

    await tracemoe.search(_image())
    with pytest.raises(TraceMoeError):
        await tracemoe.search(_image())

    assert tracemoe.usage == (12, 100)


def test_a_sponsors_key_is_no_guest(http):
    assert TraceMoe(http, "sponsor-key").sponsored
    assert not TraceMoe(http).sponsored
