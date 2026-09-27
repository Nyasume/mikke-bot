import re
from typing import Annotated, Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Telegram accepts only these characters in a webhook secret token
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


class Settings(BaseSettings):
    """The only place that reads the environment (and `.env`)."""

    # empty values count as unset, so a blank `KEY=` line in .env falls back to the default
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    bot_token: SecretStr
    saucenao_api_key: SecretStr

    # Owner reports: every found result with the searched image, and every error
    admin_ids: Annotated[list[int], NoDecode] = []
    report_results: bool = True
    report_errors: bool = True

    # Every photo posted in these chats is searched automatically
    favourite_groups: Annotated[list[int], NoDecode] = []

    bot_mode: Literal["polling", "webhook"] = "polling"
    # Public base URL of the bot's HTTP server, e.g. https://anybyte.org/telebot/rsbot.
    # The webhook is `<PUBLIC_URL>/` and token-free image links are `<PUBLIC_URL>/img/<file_id>`.
    # Required for webhook mode; without it, file searches get no fallback links.
    public_url: str | None = None
    webhook_secret: SecretStr | None = None
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    log_level: str = "INFO"

    @field_validator("admin_ids", "favourite_groups", mode="before")
    @classmethod
    def _parse_id_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("public_url", mode="before")
    @classmethod
    def _strip_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/") or None
        return value

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
