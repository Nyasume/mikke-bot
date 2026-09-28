import hashlib
import hmac
import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Telegram accepts only these characters in a webhook secret token
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")
# A bot token is `<bot id>:<secret part>`
_TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]+")


def bot_id_of(token: str) -> int:
    """The bot's numeric id, the part of its token before the colon."""
    return int(token.partition(":")[0])


def derive_secret(webhook_secret: str, bot_id: int) -> str:
    """An extra bot's webhook secret, made from WEBHOOK_SECRET and the bot id, so the server needs no new setting."""
    return hmac.new(webhook_secret.encode(), str(bot_id).encode(), hashlib.sha256).hexdigest()


class Settings(BaseSettings):
    """The only place that reads the environment (and `.env`)."""

    # Empty values count as unset, so a blank `KEY=` line in .env falls back to the default.
    # Validation errors are printed at startup: without the input, which holds the tokens.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # The primary bot: its webhook is `<PUBLIC_URL>/`, and old /img/<file_id> links are served through it
    bot_token: SecretStr
    # More bots answered by the same process, comma-separated. They share everything but the token;
    # each one's webhook is `<PUBLIC_URL>/<bot id>/`.
    extra_bot_tokens: Annotated[list[SecretStr], NoDecode] = []
    saucenao_api_key: SecretStr
    # A trace.moe sponsor's key for a higher quota; without it the anime scene search runs as a guest
    trace_moe_api_key: SecretStr | None = None

    # Owner reports go to these users, through the bot that handled the update
    admin_ids: Annotated[list[int], NoDecode] = []
    # An activity report for every search, found or not, SauceNAO and trace.moe alike: the searched image,
    # the outcome, who asked and where, the bot and whether the answer was cached. A failed search is in it.
    report_results: bool = True
    # Every error: handler failures, and failed searches when REPORT_RESULTS is off
    report_errors: bool = True

    # Every photo posted in these chats is searched automatically, always with SAUCENAO_API_KEY
    favourite_groups: Annotated[list[int], NoDecode] = []

    # What must survive restarts: the SauceNAO keys users add with /apikey (mikke.sqlite3). /data in the image.
    data_dir: Path = Path("data")

    bot_mode: Literal["polling", "webhook"] = "polling"
    # Public base URL of the bot's HTTP server, e.g. https://example.org/mikke.
    # Webhooks are under it, and token-free image links are `<PUBLIC_URL>/img/<bot id>/<file_id>`.
    # Required for webhook mode; without it, file searches get no fallback links.
    public_url: str | None = None
    # The primary bot's webhook secret; the extra bots' are derived from it (`derive_secret`)
    webhook_secret: SecretStr | None = None
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    log_level: str = "INFO"

    # Sentry is off without a DSN. The Docker image built by CI sets SENTRY_RELEASE to mikke-bot@<commit sha>.
    sentry_dsn: SecretStr | None = None
    sentry_environment: str = "production"
    sentry_release: str | None = None
    sentry_traces_sample_rate: float = Field(0.0, ge=0.0, le=1.0)

    def bot_tokens(self) -> list[str]:
        """Every bot's token, the primary bot's first."""
        return [token.get_secret_value() for token in (self.bot_token, *self.extra_bot_tokens)]

    def secrets(self) -> list[str]:
        """The values that must never reach the logs or Sentry."""
        keys = (self.saucenao_api_key, self.trace_moe_api_key, self.webhook_secret)
        values = [*self.bot_tokens(), *(key.get_secret_value() for key in keys if key is not None)]
        if self.webhook_secret is not None:
            # the extra bots' webhook secrets
            secret = self.webhook_secret.get_secret_value()
            values += [derive_secret(secret, bot_id_of(token)) for token in self.bot_tokens()[1:]]
        return values

    @field_validator("admin_ids", "favourite_groups", mode="before")
    @classmethod
    def _parse_id_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("extra_bot_tokens", mode="before")
    @classmethod
    def _parse_token_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("public_url", mode="before")
    @classmethod
    def _strip_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/") or None
        return value

    @model_validator(mode="after")
    def _check_tokens(self) -> Self:
        tokens = self.bot_tokens()
        if not all(_TOKEN_RE.fullmatch(token) for token in tokens):
            raise ValueError("BOT_TOKEN and EXTRA_BOT_TOKENS must look like <bot id>:<secret part>")
        if len({bot_id_of(token) for token in tokens}) < len(tokens):
            raise ValueError("BOT_TOKEN and EXTRA_BOT_TOKENS name the same bot twice")
        return self

    @model_validator(mode="after")
    def _check_webhook(self) -> Self:
        if self.bot_mode == "webhook":
            if not self.public_url:
                raise ValueError("PUBLIC_URL is required when BOT_MODE=webhook")
            if self.webhook_secret is None:
                raise ValueError("WEBHOOK_SECRET is required when BOT_MODE=webhook")
        if self.webhook_secret is not None and not _SECRET_RE.fullmatch(self.webhook_secret.get_secret_value()):
            raise ValueError("WEBHOOK_SECRET must be 1-256 characters of A-Z, a-z, 0-9, _ and -")
        return self
