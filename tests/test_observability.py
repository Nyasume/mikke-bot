"""Sentry, offline: a recording transport keeps what the SDK would send."""

import asyncio
import logging
from collections.abc import Callable, Iterator
from urllib.parse import quote

import httpx
import pytest
import sentry_sdk
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import EditMessageText, GetFile, SendMessage, SendRichMessage
from aiohttp import test_utils
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from mikke import texts
from mikke.config import Settings, derive_secret
from mikke.observability import FILTERED, Scrubber, init_sentry, redact
from mikke.saucenao import SEARCH_URL, QuotaExceededError
from mikke.tracemoe import SEARCH_URL as TRACE_URL
from mikke.web import build_app
from payloads import (
    ADMIN_ID,
    ANIME,
    API_KEY,
    BOT_TOKEN,
    BOT_USERNAME,
    EXTRA_BOT_TOKEN,
    EXTRA_BOT_USERNAME,
    PHOTO,
    USER_KEY,
    bot_answer,
    button_press,
    message,
    sauce_response,
    telegram_file_error,
    update,
)

DSN = "https://public@sentry.example.invalid/1"
TRACE_KEY = "trace-sponsor-key"
WEBHOOK_SECRET = "webhook-secret_2"
SECRETS = [BOT_TOKEN, API_KEY, TRACE_KEY, WEBHOOK_SECRET]
# a sender with a name, a username and a text that must not reach Sentry
SENDER = {"id": 7, "is_bot": False, "first_name": "Alice-Private", "username": "alice_private"}
TEXT = "my-private-text"


class RecordingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    @property
    def events(self) -> list[dict]:
        return [event for envelope in self.envelopes if (event := envelope.get_event()) is not None]

    @property
    def sent(self) -> str:
        return "".join(envelope.serialize().decode() for envelope in self.envelopes)


@pytest.fixture
def sentry(make_harness) -> Iterator[Callable[..., tuple]]:
    """Start Sentry on a harness: `harness, transport = sentry(**settings)`."""

    def start(**overrides) -> tuple:
        settings = {"sentry_dsn": DSN, "trace_moe_api_key": TRACE_KEY, "webhook_secret": WEBHOOK_SECRET}
        harness = make_harness(**settings | overrides)
        transport = RecordingTransport()
        init_sentry(harness.settings, transport=transport)
        return harness, transport

    yield start
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    sentry_sdk.get_isolation_scope().clear()
    sentry_sdk.get_current_scope().clear()


def _assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text
        assert quote(secret, safe="") not in text


def _photo(**fields) -> dict:
    return update({**message(photo=PHOTO, **fields), "from": SENDER})


@pytest.fixture
def found(respx_mock):
    return respx_mock.post(SEARCH_URL).respond(json=sauce_response([ANIME]))


def _sent(harness) -> list[SendMessage]:
    return harness.session.calls(SendMessage)


def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=EditMessageText(text="x"), message=f"Bad Request: {text}")


async def _client(harness, secret: str | None = None) -> test_utils.TestClient:
    client = test_utils.TestClient(test_utils.TestServer(build_app(harness.bots, harness.dp, webhook_secret=secret)))
    await client.start_server()
    return client


# --- configuration -----------------------------------------------------------


def test_sentry_is_off_without_a_dsn(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("SAUCENAO_API_KEY", API_KEY)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("SENTRY_DSN=\n")
    settings = Settings(_env_file=dotenv)
    assert settings.sentry_dsn is None

    init_sentry(settings)

    assert not sentry_sdk.get_client().is_active()


async def test_release_and_environment_come_from_the_settings(sentry):
    _, transport = sentry(sentry_environment="test", sentry_release="mikke-bot@abc123")
    sentry_sdk.capture_message("hello")

    [event] = transport.events
    assert (event["environment"], event["release"]) == ("test", "mikke-bot@abc123")


async def test_no_release_without_sentry_release(sentry):
    _, transport = sentry()
    sentry_sdk.capture_message("hello")

    [event] = transport.events
    assert "release" not in event
    assert event["environment"] == "production"


# --- scrubbing ---------------------------------------------------------------


def test_scrubber_removes_every_secret_from_every_part_of_an_event():
    telegram = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
    event = {
        "message": f"failed {telegram}",
        "logentry": {"message": "failed %s", "params": [telegram, TRACE_KEY]},
        "exception": {"values": [{"type": "ClientResponseError", "value": f"502, url='{telegram}'"}]},
        "request": {
            "url": f"https://api.telegram.org/file/bot{quote(BOT_TOKEN, safe='')}/photos/file_1.jpg",
            "query_string": f"output_type=2&api_key={API_KEY}",
            "headers": {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET, "x-trace-key": TRACE_KEY},
        },
        "breadcrumbs": {
            "values": [
                {"category": "httpx", "data": {"url": SEARCH_URL, "http.query": f"db=999&api_key={API_KEY}"}},
                {"category": "aiohttp", "data": {"url": telegram}, "message": f"secret {WEBHOOK_SECRET}"},
            ]
        },
        "spans": [{"description": f"POST {telegram}"}],
        "extra": {BOT_TOKEN: "as a key"},
    }

    scrubbed = str(Scrubber(SECRETS)(event))

    _assert_no_secrets(scrubbed)
    assert f"/bot{FILTERED}/getFile" in scrubbed
    assert f"api_key={FILTERED}" in scrubbed


def test_scrubber_catches_tokens_and_keys_it_was_not_given():
    scrub = Scrubber([])
    assert scrub.text("https://api.telegram.org/file/bot123:AAH-x_y/photos/1.jpg") == (
        f"https://api.telegram.org/file/bot{FILTERED}/photos/1.jpg"
    )
    assert scrub.text("https://saucenao.com/search.php?api_key=abc123&db=999") == (
        f"https://saucenao.com/search.php?api_key={FILTERED}&db=999"
    )
    # a user's own key, in the text of their command
    assert scrub.text(f"text='/apikey {USER_KEY}'") == f"text='/apikey {FILTERED}'"
    assert scrub.text(f"/APIKEY@{BOT_USERNAME}  {USER_KEY} x") == f"/APIKEY@{BOT_USERNAME}  {FILTERED} x"


def test_scrubber_keeps_the_searched_images_out_but_not_the_rest():
    scrub = Scrubber([])
    # a file_id opens the user's picture through /img/; the route and the bot stay
    assert scrub.text("getFile failed for /img/4200000001/AgACAgIAAxkBAAI-x_1: wrong file_id") == (
        f"getFile failed for /img/4200000001/{FILTERED}: wrong file_id"
    )
    assert scrub.text("https://example.org/mikke/img/AgACAgIAAxkBAAI") == f"https://example.org/mikke/img/{FILTERED}"
    # an inline search's URL is whatever the user sent
    assert scrub.text("output_type=2&db=999&url=https%3A%2F%2Fexample.com%2Fme.png") == (
        f"output_type=2&db=999&url={FILTERED}"
    )
    assert scrub.text("Search failed for url:https://example.com/me.png") == f"Search failed for url:{FILTERED}"
    # file_unique_ids fetch nothing
    assert scrub.text("Search failed for AQADa8sxG_x") == "Search failed for AQADa8sxG_x"
    # scrubbing twice (the breadcrumb, then the event) changes nothing more
    once = scrub.text("/img/4200000001/AgACAgIAAxkBAAI url:https://a.b/c url=d")
    assert scrub.text(once) == once == f"/img/4200000001/{FILTERED} url:{FILTERED} url={FILTERED}"
    # Sentry only: the logs keep them
    assert redact("getFile failed for /img/42/AgACAgIAAxkBAAI") == "getFile failed for /img/42/AgACAgIAAxkBAAI"


async def test_secrets_never_reach_sentry(sentry, caplog):
    _, transport = sentry()
    caplog.set_level(logging.INFO)
    log = logging.getLogger("mikke.test")
    log.info("GET https://api.telegram.org/bot%s/getMe", BOT_TOKEN)
    sentry_sdk.add_breadcrumb(category="httpx", data={"url": SEARCH_URL, "http.query": f"api_key={API_KEY}"})
    sentry_sdk.add_breadcrumb(category="headers", data={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})

    try:
        raise RuntimeError(f"file/bot{BOT_TOKEN}/a.jpg {API_KEY} {TRACE_KEY} {WEBHOOK_SECRET}")
    except RuntimeError:
        log.exception("failed with %s", TRACE_KEY)

    [event] = transport.events
    assert len(event["breadcrumbs"]["values"]) >= 3
    _assert_no_secrets(transport.sent)
    assert FILTERED in event["exception"]["values"][0]["value"]


async def test_every_bots_token_and_webhook_secret_is_filtered(sentry, caplog):
    _, transport = sentry(extra_bot_tokens=[EXTRA_BOT_TOKEN])
    extra_secret = derive_secret(WEBHOOK_SECRET, 43)
    secrets = [*SECRETS, EXTRA_BOT_TOKEN, extra_secret]
    log = logging.getLogger("mikke.test")
    sentry_sdk.add_breadcrumb(category="headers", data={"X-Telegram-Bot-Api-Secret-Token": extra_secret})
    # shaped so that only the given secrets, not the token pattern, can catch them
    text = " ".join(f"{secret} {quote(secret, safe='')}" for secret in secrets)

    try:
        raise RuntimeError(text)
    except RuntimeError:
        log.exception("failed: %s", text)

    [event] = transport.events
    assert FILTERED in event["exception"]["values"][0]["value"]
    for secret in secrets:
        assert secret not in transport.sent
        assert quote(secret, safe="") not in transport.sent


async def test_searches_leave_no_api_key_in_the_breadcrumbs(sentry, respx_mock):
    harness, transport = sentry()
    respx_mock.post(SEARCH_URL).respond(500, text="Internal Server Error")

    await harness.feed(_photo())

    # the SauceNAO failure is an issue, with the request among its breadcrumbs
    [event] = transport.events
    [request] = [crumb["data"] for crumb in event["breadcrumbs"]["values"] if crumb["type"] == "http"]
    assert request["url"] == SEARCH_URL
    assert f"api_key={FILTERED}" in request["http.query"]
    _assert_no_secrets(transport.sent)


async def test_webhook_error_is_sent_once_without_the_secret_header_or_the_update(sentry, found):
    harness, transport = sentry(sentry_traces_sample_rate=1.0)
    harness.session.errors[EditMessageText] = RuntimeError("telegram exploded")
    client = await _client(harness, secret=WEBHOOK_SECRET)
    try:
        response = await client.post(
            "/", json=_photo(caption=TEXT), headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}
        )
        assert response.status == 200
        # the update is handled in the background; give a second event the time to show up too
        for _ in range(20):
            await asyncio.sleep(0.01)
    finally:
        await client.close()

    [event] = transport.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    assert event["request"]["headers"]["X-Telegram-Bot-Api-Secret-Token"] == FILTERED
    _assert_no_secrets(transport.sent)
    # no client IP, which Sentry would show as the user's
    assert "REMOTE_ADDR" not in transport.sent
    for private in (TEXT, SENDER["first_name"], SENDER["username"]):
        assert private not in transport.sent


async def test_a_users_key_never_reaches_sentry_or_the_owner(sentry, respx_mock):
    harness, transport = sentry()
    respx_mock.get(SEARCH_URL).respond(json=sauce_response([]))
    # the confirmation fails: the update's error goes to Sentry and to the owner
    harness.session.chat_errors[7] = RuntimeError("telegram exploded")
    client = await _client(harness, secret=WEBHOOK_SECRET)
    try:
        body = update({**message(text=f"/apikey {USER_KEY}"), "from": SENDER})
        response = await client.post("/", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})
        assert response.status == 200
        for _ in range(20):
            await asyncio.sleep(0.01)
    finally:
        await client.close()

    [event] = transport.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    assert USER_KEY not in transport.sent
    [report] = [call for call in _sent(harness) if call.chat_id == ADMIN_ID]
    assert "RuntimeError: telegram exploded" in report.text
    assert all(USER_KEY not in call.text for call in _sent(harness))


async def test_a_failed_key_check_reaches_sentry_without_the_key(sentry, respx_mock):
    harness, transport = sentry()
    respx_mock.get(SEARCH_URL).mock(side_effect=httpx.ConnectError(f"GET {SEARCH_URL}?api_key={USER_KEY} ({USER_KEY})"))

    await harness.feed(update(message(text=USER_KEY)))

    [event] = transport.events
    assert event["logentry"]["message"].startswith("Checking a SauceNAO key failed")
    assert USER_KEY not in transport.sent


async def test_a_failed_search_with_a_users_key_keeps_it_out_of_sentry_and_the_report(sentry, respx_mock):
    harness, transport = sentry()
    respx_mock.post(SEARCH_URL).respond(500, text=f"error for api_key={USER_KEY} and {USER_KEY}")
    await harness.keys.set(7, USER_KEY)

    await harness.feed(_photo())

    [event] = transport.events
    assert event["logentry"]["message"].startswith("Search failed")
    assert USER_KEY not in transport.sent
    [report] = harness.session.calls(SendRichMessage)
    assert "SauceNaoError: HTTP 500" in report.rich_message.html
    assert USER_KEY not in report.rich_message.html


# --- what is sent ------------------------------------------------------------


async def test_handler_error_is_sent_once_with_numeric_ids_only(sentry, found):
    harness, transport = sentry()
    harness.session.errors[EditMessageText] = RuntimeError("telegram exploded")

    await harness.feed(_photo(caption=TEXT))

    [event] = transport.events
    assert [value["type"] for value in event["exception"]["values"]] == ["RuntimeError"]
    assert event["user"] == {"id": "7"}
    assert event["tags"]["update_type"] == "message"
    assert event["contexts"]["telegram"]["chat_id"] == 7
    assert all("vars" not in frame for frame in event["exception"]["values"][0]["stacktrace"]["frames"])
    for private in (TEXT, SENDER["first_name"], SENDER["username"]):
        assert private not in transport.sent
    # the owner still gets the report
    assert _sent(harness)[-1].chat_id == ADMIN_ID


@pytest.mark.parametrize(("receiver", "username"), [(0, BOT_USERNAME), (1, EXTRA_BOT_USERNAME)])
async def test_events_name_the_bot_that_received_the_update(sentry, found, respx_mock, receiver, username):
    harness, transport = sentry(extra_bot_tokens=[EXTRA_BOT_TOKEN])
    bot = harness.bots[receiver]
    harness.sessions[receiver].fail_once[EditMessageText] = RuntimeError("telegram exploded")

    await harness.feed(_photo(), bot)  # the error handler's event
    respx_mock.post(SEARCH_URL).respond(500, text="boom")
    await harness.feed(update(message(photo=[{**PHOTO[1], "file_unique_id": "other-u"}])), bot)  # a searcher's

    handler_error, search_error = transport.events
    assert handler_error["tags"] == {"bot": username, "update_type": "message"}
    assert search_error["logentry"]["message"].startswith("Search failed")
    assert search_error["tags"] == {"bot": username}
    # nothing is left over outside the update
    sentry_sdk.capture_message("later")
    assert "bot" not in transport.events[-1].get("tags", {})


@pytest.mark.parametrize(
    ("path", "receiver", "username"),
    [("/img/43/photo-file-id", 1, EXTRA_BOT_USERNAME), ("/img/photo-file-id", 0, BOT_USERNAME)],
)
async def test_img_route_errors_name_the_bot(sentry, monkeypatch, path, receiver, username):
    harness, transport = sentry(extra_bot_tokens=[EXTRA_BOT_TOKEN])

    async def broken_get_file(file_id):
        raise RuntimeError("getFile exploded")

    monkeypatch.setattr(harness.bots[receiver], "get_file", broken_get_file)
    client = await _client(harness)
    try:
        response = await client.get(path)
    finally:
        await client.close()

    assert response.status == 500
    [event] = transport.events
    assert event["tags"]["bot"] == username


async def test_img_route_error_is_sent_once(sentry, monkeypatch):
    harness, transport = sentry()

    async def broken_get_file(file_id):
        raise RuntimeError(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile failed")

    monkeypatch.setattr(harness.bot, "get_file", broken_get_file)
    client = await _client(harness)
    try:
        response = await client.get("/img/photo-file-id")
    finally:
        await client.close()

    assert response.status == 500
    [event] = transport.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    _assert_no_secrets(transport.sent)


async def test_a_failed_inline_search_reaches_sentry_without_the_searched_url(sentry, respx_mock):
    harness, transport = sentry()
    respx_mock.get(SEARCH_URL).respond(500, text="Internal Server Error")

    await harness.searcher.search_url("https://example.com/private-pic.png")

    [event] = transport.events
    assert "private-pic" not in transport.sent
    assert event["logentry"]["formatted"] == f"Search failed for url:{FILTERED}"
    [request] = [crumb["data"] for crumb in event["breadcrumbs"]["values"] if crumb["type"] == "http"]
    assert request["http.query"].endswith(f"&url={FILTERED}")


async def test_img_route_errors_reach_sentry_without_the_file_id(sentry, monkeypatch):
    harness, transport = sentry()

    async def broken_get_file(file_id):
        raise RuntimeError("getFile exploded")

    monkeypatch.setattr(harness.bot, "get_file", broken_get_file)
    client = await _client(harness)
    try:
        response = await client.get("/img/42/private-file-id")
    finally:
        await client.close()

    assert response.status == 500
    [event] = transport.events
    assert "private-file-id" not in transport.sent
    assert event["request"]["url"].endswith(f"/img/42/{FILTERED}")


# --- what is not sent --------------------------------------------------------


async def test_saucenao_limit_and_no_result_are_not_sent(sentry, respx_mock):
    harness, transport = sentry()
    route = respx_mock.post(SEARCH_URL)

    route.respond(429, json={"header": {"status": -2, "message": "Daily Search Limit Exceeded."}})
    await harness.feed(_photo())
    harness.clock.now += 3600
    route.respond(json=sauce_response([]))
    await harness.feed(update(message(photo=[{**PHOTO[1], "file_unique_id": "other-u"}])))

    limit, no_result = (call.text for call in harness.session.calls(EditMessageText))
    assert (limit.startswith(texts.LIMIT_REACHED), no_result) == (True, texts.NO_RESULT)
    assert transport.events == []


async def test_trace_moe_quota_is_not_sent(sentry, respx_mock):
    harness, transport = sentry()
    quota = {"error": "Search quota depleted (quota per 24 hours: 100, used: 100)"}
    respx_mock.post(TRACE_URL).respond(402, json=quota)
    media = message(photo=PHOTO)

    await harness.feed(button_press(bot_answer(media["chat"], media)))
    await harness.feed(button_press(bot_answer(media["chat"], media)))

    assert transport.events == []


async def test_flooding_is_not_sent(sentry, found):
    harness, transport = sentry()
    for _ in range(25):
        await harness.feed(_photo())
    assert transport.events == []


@pytest.mark.parametrize(
    "error",
    [
        _bad_request("message is not modified: specified new message content is exactly the same"),
        _bad_request("message to edit not found"),
    ],
    ids=["not-modified", "deleted"],
)
async def test_failed_edits_are_not_sent(sentry, found, error):
    harness, transport = sentry()
    harness.session.errors[EditMessageText] = error
    await harness.feed(_photo())
    assert transport.events == []


@pytest.mark.parametrize(
    "error",
    [
        TelegramForbiddenError(
            method=SendMessage(chat_id=7, text="x"), message="Forbidden: bot was blocked by the user"
        ),
        TelegramBadRequest(
            method=SendMessage(chat_id=7, text="x"), message="Bad Request: not enough rights to send text messages"
        ),
    ],
    ids=["blocked", "muted"],
)
async def test_a_chat_the_bot_cannot_write_to_is_neither_sent_nor_reported(sentry, found, error):
    harness, transport = sentry()
    harness.session.errors[SendMessage] = error

    await harness.feed(_photo())

    assert transport.events == []
    assert [call.chat_id for call in _sent(harness)] == [7]  # the placeholder only, no owner report


async def test_an_admin_who_never_started_a_bot_is_not_sent(sentry, respx_mock):
    harness, transport = sentry(extra_bot_tokens=[EXTRA_BOT_TOKEN])
    respx_mock.post(SEARCH_URL).respond(500, text="boom")
    extra = harness.sessions[1]
    extra.chat_errors[ADMIN_ID] = TelegramForbiddenError(
        method=SendMessage(chat_id=ADMIN_ID, text="x"), message="Forbidden: bot can't initiate conversation with a user"
    )

    await harness.feed(_photo(), harness.bots[1])

    # the SauceNAO failure is the only event; the report that could not be delivered is not one
    [event] = transport.events
    assert event["logentry"]["message"].startswith("Search failed")
    assert [call.chat_id for call in extra.calls(SendMessage)] == [7]
    assert [call.chat_id for call in extra.calls(SendRichMessage)] == [ADMIN_ID]


async def test_img_failures_are_not_sent(sentry, monkeypatch):
    harness, transport = sentry()

    async def failing_stream(*args, **kwargs):
        raise telegram_file_error(BOT_TOKEN)
        yield b""  # pragma: no cover

    client = await _client(harness)
    try:
        harness.session.fail_once[GetFile] = TelegramBadRequest(
            method=GetFile(file_id="x"), message="Bad Request: wrong file_id"
        )
        assert (await client.get("/img/expired-file-id")).status == 404
        monkeypatch.setattr(harness.session, "stream_content", failing_stream)
        assert (await client.get("/img/photo-file-id")).status == 502
    finally:
        await client.close()
    assert transport.events == []


async def test_expected_errors_logged_as_errors_are_dropped(sentry):
    _, transport = sentry()
    logging.getLogger("mikke.test").error("quota", exc_info=QuotaExceededError())
    assert transport.events == []
