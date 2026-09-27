"""Sentry: unexpected errors become issues, while secrets and user data stay in the process.

Call `init_sentry(settings)` once, inside the event loop: the asyncio integration
patches the running loop. Without SENTRY_DSN the SDK is never initialised.

Each unexpected error reaches Sentry once:
- a handler error goes to aiogram's error handler, `handlers.on_error`, whose
  `logger.error` record the logging integration turns into the event (aiogram has
  no Sentry integration, and the error handler swallows the exception);
- an error in an aiohttp route (/img/, the webhook) is caught by the aiohttp
  integration, which also ignores aiohttp's own "Error handling request" log;
- anything else logged at ERROR, like `logger.exception` in the searchers.
Expected situations (`is_expected`) are logged at INFO or WARNING, and dropped
here too in case one is ever logged as an error.
"""

import logging
import re
from typing import Any
from urllib.parse import quote

import sentry_sdk
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST, EventScrubber
from sentry_sdk.transport import Transport
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from mikke import saucenao, tracemoe
from mikke.config import Settings

logger = logging.getLogger(__name__)

FILTERED = "[Filtered]"
# The secrets in text we did not write, even cut short or in another bot's URL:
# Telegram URLs carry the bot token (api.telegram.org/bot<token>/..., /file/bot<token>/...),
# SauceNAO URLs the api_key
SECRET_PATTERNS = (
    re.compile(r"(?<=/bot)\d+(?::|%3A)[\w-]+", re.IGNORECASE),
    re.compile(r"(?<=api_key=)[^&\s\"']+"),
)
# Header values that never leave: the webhook secret, the trace.moe key, client IPs behind Cloudflare
DENYLIST = [*DEFAULT_DENYLIST, "x-telegram-bot-api-secret-token", "x-trace-key"]
PII_DENYLIST = [*DEFAULT_PII_DENYLIST, "cf-connecting-ip", "true-client-ip"]

# Telegram refusing because a user was faster than the bot: an edit that changes
# nothing, a message deleted before the answer, a button pressed long ago, a chat
# that took away the bot's right to write
EXPECTED_BAD_REQUESTS = (
    "message is not modified",
    "message to edit not found",
    "message to be replied not found",
    "message_id_invalid",
    "query is too old",
    "not enough rights",
)


def is_expected(error: BaseException) -> bool:
    """Part of normal operation, not a bug: a used up quota, a user who blocked the bot or deleted a message."""
    if isinstance(error, saucenao.QuotaExceededError | tracemoe.QuotaExceededError | TelegramForbiddenError):
        return True
    return isinstance(error, TelegramBadRequest) and any(
        text in error.message.lower() for text in EXPECTED_BAD_REQUESTS
    )


class Scrubber:
    """Replaces the secrets in every string of an event or a breadcrumb, dictionary keys included."""

    def __init__(self, secrets: list[str]) -> None:
        values = {secret for secret in secrets if secret}
        # also as they look inside a URL: the token's ":" becomes %3A
        values |= {quote(secret, safe="") for secret in values}
        # longest first, so no secret is left half-replaced after a shorter one inside it
        self._secrets = sorted(values, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, FILTERED)
        for pattern in SECRET_PATTERNS:
            value = pattern.sub(FILTERED, value)
        return value

    def __call__(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self(key): self(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self(item) for item in value]
        return value


def init_sentry(settings: Settings, *, transport: Transport | None = None) -> None:
    if settings.sentry_dsn is None:
        logger.info("Sentry is off: no SENTRY_DSN")
        return
    scrub = Scrubber(settings.secrets())

    def clean(event: Event) -> Event:
        # the aiohttp integration sends REMOTE_ADDR even without PII, and Sentry would make it the user's IP
        event.get("request", {}).pop("env", None)
        return scrub(event)

    def before_send(event: Event, hint: Hint) -> Event | None:
        exc_info = hint.get("exc_info")
        if exc_info and is_expected(exc_info[1]):
            return None
        return clean(event)

    def before_send_transaction(event: Event, _hint: Hint) -> Event:
        return clean(event)

    def before_breadcrumb(crumb: Breadcrumb, _hint: BreadcrumbHint) -> Breadcrumb:
        return scrub(crumb)

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[
            # 404 and 502 from /img/ are answers, not failures; exceptions are still captured
            AioHttpIntegration(failed_request_status_codes=set()),
            AsyncioIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        send_default_pii=False,
        # locals hold messages with names and texts, and Telegram file URLs with the token;
        # webhook bodies are whole updates
        include_local_variables=False,
        max_request_body_size="never",
        # no sentry-trace and baggage headers for Telegram, SauceNAO and trace.moe
        trace_propagation_targets=[],
        event_scrubber=EventScrubber(denylist=DENYLIST, pii_denylist=PII_DENYLIST, recursive=True),
        before_send=before_send,
        before_send_transaction=before_send_transaction,
        before_breadcrumb=before_breadcrumb,
        transport=transport,
    )
    if settings.sentry_release is None:
        # the SDK guessed one from `git rev-parse HEAD`, which says nothing about a checkout with local changes
        sentry_sdk.get_client().options["release"] = None
    logger.info("Sentry is on: environment %s, release %s", settings.sentry_environment, settings.sentry_release)
