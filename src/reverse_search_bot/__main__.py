import asyncio
import logging
import signal
import sys

import httpx
from aiohttp import web
from pydantic import ValidationError

from reverse_search_bot.bot import build_bot, build_dispatcher
from reverse_search_bot.config import Settings
from reverse_search_bot.reports import Reporter
from reverse_search_bot.saucenao import SauceNao
from reverse_search_bot.search import Searcher
from reverse_search_bot.web import build_app

logger = logging.getLogger(__name__)


async def _wait_for_stop_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


async def run(settings: Settings) -> None:
    bot = build_bot(settings)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        saucenao = SauceNao(http, settings.saucenao_api_key.get_secret_value())
        reporter = Reporter(settings.admin_ids, results=settings.report_results, errors=settings.report_errors)
        searcher = Searcher(saucenao, reporter, settings.public_url)
        dp = build_dispatcher(settings, searcher, reporter)

        webhook = settings.bot_mode == "webhook"
        secret = settings.webhook_secret.get_secret_value() if webhook and settings.webhook_secret else None
        runner = web.AppRunner(build_app(bot, dp, webhook_secret=secret))
        await runner.setup()
        await web.TCPSite(runner, settings.web_host, settings.web_port).start()
        logger.info("HTTP server on %s:%s, %s mode", settings.web_host, settings.web_port, settings.bot_mode)
        try:
            if webhook:
                await bot.set_webhook(
                    f"{settings.public_url}/",
                    secret_token=secret,
                    allowed_updates=dp.resolve_used_update_types(),
                )
                await _wait_for_stop_signal()
            else:
                await bot.delete_webhook(drop_pending_updates=True)
                await dp.start_polling(bot)
        finally:
            await runner.cleanup()
            await bot.session.close()


def main() -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and the SauceNAO api_key is in the query string
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
