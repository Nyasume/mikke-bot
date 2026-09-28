import asyncio
import logging
import signal
import sys

import httpx
from aiohttp import web
from pydantic import ValidationError

from mikke.bot import InFlight, build_bot, build_dispatcher
from mikke.config import Settings
from mikke.keys import KeyStore
from mikke.observability import init_sentry, redact
from mikke.reports import Reporter
from mikke.saucenao import SauceNao
from mikke.scenes import SceneSearcher
from mikke.search import Searcher
from mikke.tracemoe import TraceMoe
from mikke.web import build_app, webhooks

logger = logging.getLogger(__name__)

# Docker sends SIGKILL 10 s after SIGTERM
SHUTDOWN_GRACE_SECONDS = 8.0
# in DATA_DIR
DATABASE = "mikke.sqlite3"


class RedactingFormatter(logging.Formatter):
    """Keeps secrets out of the logs, tracebacks included (aiohttp errors quote Telegram file URLs)."""

    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        # and the ones only known by their shape, like users' SauceNAO keys in URLs
        return redact(text)


async def _wait_for_stop_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


async def run(settings: Settings) -> None:
    # inside the event loop, which the Sentry asyncio integration patches
    init_sentry(settings)
    # the primary bot first; one dispatcher answers them all
    bots = [build_bot(token) for token in settings.bot_tokens()]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        saucenao = SauceNao(http, settings.saucenao_api_key.get_secret_value())
        reporter = Reporter(settings.admin_ids, results=settings.report_results, errors=settings.report_errors)
        keys = KeyStore(settings.data_dir / DATABASE)
        searcher = Searcher(saucenao, settings.public_url, keys)
        trace_key = settings.trace_moe_api_key.get_secret_value() if settings.trace_moe_api_key else None
        scenes = SceneSearcher(TraceMoe(http, trace_key))
        dp = build_dispatcher(settings, searcher, scenes, reporter, keys)

        webhook = settings.bot_mode == "webhook"
        secret = settings.webhook_secret.get_secret_value() if webhook and settings.webhook_secret else None
        runner = web.AppRunner(build_app(bots, dp, webhook_secret=secret))
        await runner.setup()
        site = web.TCPSite(runner, settings.web_host, settings.web_port)
        await site.start()
        logger.info("HTTP server on %s:%s, %s mode", settings.web_host, settings.web_port, settings.bot_mode)
        try:
            for bot in bots:
                # getMe is cached from here on
                me = await bot.me()
                logger.info("Answering as @%s (id %s)", me.username, me.id)
            if webhook and secret is not None:
                for hook in webhooks(bots, secret):
                    await hook.bot.set_webhook(
                        f"{settings.public_url}{hook.path}",
                        secret_token=hook.secret,
                        allowed_updates=dp.resolve_used_update_types(),
                    )
                await _wait_for_stop_signal()
                # stop taking updates, then let the ones in progress finish their edits
                await site.stop()
                in_flight: InFlight = dp["in_flight"]
                await in_flight.wait(SHUTDOWN_GRACE_SECONDS)
            else:
                for bot in bots:
                    await bot.delete_webhook(drop_pending_updates=True)
                await dp.start_polling(*bots)
        finally:
            await runner.cleanup()
            for bot in bots:
                await bot.session.close()


def main() -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", settings.secrets()))
    logging.basicConfig(level=settings.log_level, handlers=[handler])
    # httpx logs every request URL at INFO, SauceNAO api_key included
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
