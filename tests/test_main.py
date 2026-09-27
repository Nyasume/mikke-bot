import asyncio
import logging
import sys
from importlib.metadata import distribution

import mikke.__main__
from mikke.__main__ import RedactingFormatter
from mikke.bot import InFlight


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
