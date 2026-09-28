import asyncio
import logging
import sys
from importlib.metadata import distribution

from aiogram import Dispatcher
from aiogram.methods import DeleteWebhook, GetUpdates, SetWebhook

import mikke.__main__
from mikke.__main__ import RedactingFormatter, run
from mikke.bot import InFlight
from mikke.config import derive_secret
from payloads import EXTRA_BOT_TOKEN, PUBLIC_URL


def test_console_script_is_mikke():
    scripts = {entry.name: entry for entry in distribution("mikke").entry_points if entry.group == "console_scripts"}
    assert list(scripts) == ["mikke"]
    assert scripts["mikke"].load() is mikke.__main__.main


def test_log_formatter_redacts_secrets_in_messages_and_tracebacks():
    formatter = RedactingFormatter("%(message)s", ["42:SECRET", "api-key", ""])
    try:
        raise ValueError("url=https://api.telegram.org/file/bot42:SECRET/a.jpg")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "key %s", ("api-key",), sys.exc_info())
    text = formatter.format(record)
    assert "42:SECRET" not in text
    assert "api-key" not in text
    assert "bot<redacted>/a.jpg" in text


async def test_in_flight_waits_for_running_updates():
    in_flight = InFlight()
    release = asyncio.Event()

    async def handler(event, data):
        await release.wait()

    task = asyncio.create_task(in_flight(handler, object(), {}))
    await asyncio.sleep(0)

    await in_flight.wait(0.01)  # gives up after the grace period
    assert not task.done()

    release.set()
    await in_flight.wait(1)
    await asyncio.sleep(0)
    assert task.done()


async def test_in_flight_returns_at_once_when_idle():
    await asyncio.wait_for(InFlight().wait(10), 0.1)


def _run_with_harness_bots(monkeypatch, harness) -> None:
    """Make run() use the harness bots, with their fake sessions, and return at once instead of waiting for SIGTERM."""
    by_token = {bot.token: bot for bot in harness.bots}
    monkeypatch.setattr(mikke.__main__, "build_bot", lambda token: by_token[token])

    async def no_wait() -> None:
        pass

    monkeypatch.setattr(mikke.__main__, "_wait_for_stop_signal", no_wait)


async def test_webhook_mode_registers_each_bots_webhook(make_harness, monkeypatch):
    harness = make_harness(
        extra_bot_tokens=[EXTRA_BOT_TOKEN], bot_mode="webhook", webhook_secret="hook", web_host="127.0.0.1", web_port=0
    )
    _run_with_harness_bots(monkeypatch, harness)
    built = []
    build_dispatcher = mikke.__main__.build_dispatcher

    def capture(*args):
        built.append(dp := build_dispatcher(*args))
        return dp

    monkeypatch.setattr(mikke.__main__, "build_dispatcher", capture)

    await run(harness.settings)

    primary, extra = harness.sessions
    [primary_hook], [extra_hook] = primary.calls(SetWebhook), extra.calls(SetWebhook)
    assert (primary_hook.url, primary_hook.secret_token) == (f"{PUBLIC_URL}/", "hook")
    assert (extra_hook.url, extra_hook.secret_token) == (f"{PUBLIC_URL}/43/", derive_secret("hook", 43))
    assert extra_hook.allowed_updates == primary_hook.allowed_updates
    assert {"message", "callback_query", "inline_query", "chosen_inline_result"} <= set(primary_hook.allowed_updates)
    assert primary.calls(DeleteWebhook) == extra.calls(DeleteWebhook) == []
    # updates refused during shutdown wait for the next start: nothing drops them
    assert primary_hook.drop_pending_updates is extra_hook.drop_pending_updates is None
    # and from SIGTERM on, webhooks are refused
    [dp] = built
    assert dp["in_flight"].closing is True


async def test_polling_mode_polls_every_bot(make_harness, monkeypatch):
    harness = make_harness(extra_bot_tokens=[EXTRA_BOT_TOKEN], web_host="127.0.0.1", web_port=0)
    _run_with_harness_bots(monkeypatch, harness)
    polled = []

    async def start_polling(self, *bots, **kwargs):
        polled.extend(bots)

    monkeypatch.setattr(Dispatcher, "start_polling", start_polling)

    await run(harness.settings)

    assert polled == harness.bots
    for session in harness.sessions:
        [delete] = session.calls(DeleteWebhook)
        assert delete.drop_pending_updates is True
        assert session.calls(SetWebhook) == session.calls(GetUpdates) == []
