import asyncio

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile, SendMessage
from aiohttp import test_utils

from mikke import texts
from mikke.config import derive_secret
from mikke.web import build_app, webhooks
from payloads import BOT_TOKEN, EXTRA_BOT_TOKEN, IMAGE, message, telegram_file_error, update

SECRET = "webhook-secret_1"
EXTRA_SECRET = derive_secret(SECRET, 43)


async def _client(harness, secret: str | None = SECRET) -> test_utils.TestClient:
    client = test_utils.TestClient(test_utils.TestServer(build_app(harness.bots, harness.dp, webhook_secret=secret)))
    await client.start_server()
    return client


@pytest.fixture
async def client(harness):
    client = await _client(harness)
    yield client
    await client.close()


@pytest.fixture
def two_bots(make_harness):
    return make_harness(extra_bot_tokens=[EXTRA_BOT_TOKEN])


@pytest.fixture
async def two_bots_client(two_bots):
    client = await _client(two_bots)
    yield client
    await client.close()


async def _wait_for(predicate, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status == 200
    assert await response.text() == "ok"


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}])
async def test_webhook_rejects_missing_or_wrong_secret(client, harness, headers):
    response = await client.post("/", json=update(message(text="/start")), headers=headers)

    assert response.status == 401
    await asyncio.sleep(0.05)
    assert harness.session.requests == []


async def test_webhook_accepts_the_right_secret(client, harness):
    response = await client.post(
        "/", json=update(message(text="/start")), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )

    assert response.status == 200
    await _wait_for(lambda: harness.session.calls(SendMessage))
    assert harness.session.calls(SendMessage)[0].text == texts.HELP


def test_webhook_paths_and_secrets(two_bots):
    primary, extra = two_bots.bots
    assert [(hook.bot, hook.path, hook.secret) for hook in webhooks(two_bots.bots, SECRET)] == [
        (primary, "/", SECRET),
        (extra, "/43/", EXTRA_SECRET),
    ]
    assert EXTRA_SECRET != SECRET


@pytest.mark.parametrize(("path", "secret", "receiver"), [("/", SECRET, 0), ("/43/", EXTRA_SECRET, 1)])
async def test_each_webhook_feeds_its_own_bot(two_bots_client, two_bots, path, secret, receiver):
    response = await two_bots_client.post(
        path, json=update(message(text="/start")), headers={"X-Telegram-Bot-Api-Secret-Token": secret}
    )

    assert response.status == 200
    receiving, other = two_bots.sessions[receiver], two_bots.sessions[1 - receiver]
    await _wait_for(lambda: receiving.calls(SendMessage))
    assert receiving.calls(SendMessage)[0].text == texts.HELP
    assert other.requests == []


@pytest.mark.parametrize(
    ("path", "secret"),
    [("/43/", SECRET), ("/", EXTRA_SECRET), ("/43/", "wrong"), ("/43/", None)],
    ids=["primary-secret", "extra-secret-on-primary", "wrong", "missing"],
)
async def test_webhooks_reject_another_bots_secret(two_bots_client, two_bots, path, secret):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
    response = await two_bots_client.post(path, json=update(message(text="/start")), headers=headers)

    assert response.status == 401
    await asyncio.sleep(0.05)
    assert [session.requests for session in two_bots.sessions] == [[], []]


async def test_unknown_bot_has_no_webhook(two_bots_client):
    response = await two_bots_client.post(
        "/44/", json=update(message(text="/start")), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}
    )
    assert response.status in (404, 405)


async def test_no_webhook_route_in_polling_mode(harness):
    client = await _client(harness, secret=None)
    try:
        response = await client.post("/", json=update(message(text="/start")))
        assert response.status in (404, 405)
        assert (await client.get("/healthz")).status == 200
    finally:
        await client.close()


@pytest.mark.parametrize(("path", "receiver"), [("/img/42/photo-file-id", 0), ("/img/43/photo-file-id", 1)])
async def test_img_fetches_through_the_bot_in_the_link(two_bots_client, two_bots, path, receiver):
    response = await two_bots_client.get(path)

    assert response.status == 200
    assert await response.read() == IMAGE
    receiving, other = two_bots.sessions[receiver], two_bots.sessions[1 - receiver]
    [get_file] = receiving.calls(GetFile)
    assert get_file.file_id == "photo-file-id"
    token = two_bots.settings.bot_tokens()[receiver]
    assert receiving.streamed == [f"https://api.telegram.org/file/bot{token}/photos/photo-file-id.jpg"]
    assert other.requests == []


async def test_img_without_a_bot_id_is_served_by_the_primary_bot(two_bots_client, two_bots):
    """Fallback links sent before there were several bots have no bot id."""
    response = await two_bots_client.get("/img/photo-file-id")

    assert response.status == 200
    assert await response.read() == IMAGE
    primary, extra = two_bots.sessions
    assert [call.file_id for call in primary.calls(GetFile)] == ["photo-file-id"]
    assert extra.requests == []


@pytest.mark.parametrize("path", ["/img/44/photo-file-id", "/img/0/photo-file-id", f"/img/{'9' * 21}/photo-file-id"])
async def test_img_of_an_unknown_bot_is_404(two_bots_client, two_bots, path):
    response = await two_bots_client.get(path)
    assert response.status == 404
    assert [session.requests for session in two_bots.sessions] == [[], []]


async def test_img_streams_the_telegram_file_without_the_token(client, harness):
    response = await client.get("/img/photo-file-id")

    assert response.status == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    body = await response.read()
    assert body == IMAGE
    assert BOT_TOKEN.encode() not in body
    assert BOT_TOKEN not in str(response.headers)
    [get_file] = harness.session.calls(GetFile)
    assert get_file.file_id == "photo-file-id"
    # the token only appears in the server-side download URL
    assert harness.session.streamed == [f"https://api.telegram.org/file/bot{BOT_TOKEN}/photos/photo-file-id.jpg"]


async def test_img_calls_getfile_on_every_request(client, harness):
    await client.get("/img/photo-file-id")
    await client.get("/img/photo-file-id")
    assert len(harness.session.calls(GetFile)) == 2


async def test_img_content_type_follows_the_file(client, harness):
    harness.session.file_paths["sticker-file-id"] = "stickers/file_7.webp"
    response = await client.get("/img/sticker-file-id")
    assert response.headers["Content-Type"] == "image/webp"


@pytest.mark.parametrize(
    "path", ["/img/bad%2Fid", "/img/short", "/img/has.dots.in.it", "/img/42/short", "/img/42/has.dots.in.it"]
)
async def test_img_rejects_malformed_file_ids(client, harness, path):
    response = await client.get(path)
    assert response.status == 404
    assert harness.session.requests == []


async def test_img_unknown_file_is_404(client, harness):
    harness.session.errors[GetFile] = TelegramBadRequest(method=GetFile(file_id="x"), message="wrong file_id")
    response = await client.get("/img/unknown-file-id")
    assert response.status == 404


async def test_img_serves_only_images(client, harness):
    harness.session.file_paths["video-file-id"] = "videos/file_3.mp4"
    response = await client.get("/img/video-file-id")
    assert response.status == 404
    assert harness.session.streamed == []


async def test_img_download_failure_is_502(client, harness, monkeypatch):
    async def broken_stream(*args, **kwargs):
        raise aiohttp.ClientConnectionError("telegram unreachable")
        yield b""  # pragma: no cover

    monkeypatch.setattr(harness.session, "stream_content", broken_stream)
    response = await client.get("/img/photo-file-id")
    assert response.status == 502


async def test_img_head_does_not_download(client, harness):
    response = await client.head("/img/photo-file-id")
    assert response.status == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert harness.session.streamed == []


async def test_img_download_failure_does_not_log_the_token(client, harness, monkeypatch, caplog):
    async def failing_stream(*args, **kwargs):
        raise telegram_file_error(BOT_TOKEN)
        yield b""  # pragma: no cover

    monkeypatch.setattr(harness.session, "stream_content", failing_stream)
    response = await client.get("/img/photo-file-id")

    assert response.status == 502
    assert BOT_TOKEN not in caplog.text
    assert BOT_TOKEN not in await response.text()
