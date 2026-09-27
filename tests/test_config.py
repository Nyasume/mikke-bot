import pytest
from pydantic import ValidationError

from reverse_search_bot.config import Settings

REQUIRED = {"BOT_TOKEN": "42:TEST", "SAUCENAO_API_KEY": "key"}


@pytest.fixture
def env(monkeypatch):
    for name in ("ADMIN_IDS", "FAVOURITE_GROUPS", "BOT_MODE", "PUBLIC_URL", "WEBHOOK_SECRET", "REPORT_RESULTS", "REPORT_ERRORS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_defaults(env):
    settings = Settings(_env_file=None)
    assert settings.admin_ids == []
    assert settings.favourite_groups == []
    assert settings.report_results is True
    assert settings.report_errors is True
    assert settings.bot_mode == "polling"
    assert (settings.web_host, settings.web_port) == ("0.0.0.0", 8080)


def test_id_lists_are_comma_separated(env):
    env.setenv("ADMIN_IDS", "1, 22")
    env.setenv("FAVOURITE_GROUPS", "-1001;-1002")
    settings = Settings(_env_file=None)
    assert settings.admin_ids == [1, 22]
    assert settings.favourite_groups == [-1001, -1002]


def test_public_url_trailing_slash_is_dropped(env):
    env.setenv("PUBLIC_URL", "https://anybyte.org/telebot/rsbot/")
    assert Settings(_env_file=None).public_url == "https://anybyte.org/telebot/rsbot"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"PUBLIC_URL": "https://anybyte.org/telebot/rsbot"},
        {"WEBHOOK_SECRET": "abc"},
        {"PUBLIC_URL": "https://anybyte.org/telebot/rsbot", "WEBHOOK_SECRET": "not allowed!"},
    ],
)
def test_webhook_mode_needs_public_url_and_valid_secret(env, extra):
    env.setenv("BOT_MODE", "webhook")
    for name, value in extra.items():
        env.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_webhook_mode_ok(env):
    env.setenv("BOT_MODE", "webhook")
    env.setenv("PUBLIC_URL", "https://anybyte.org/telebot/rsbot")
    env.setenv("WEBHOOK_SECRET", "Secret_123-abc")
    assert Settings(_env_file=None).webhook_secret.get_secret_value() == "Secret_123-abc"


def test_blank_values_count_as_unset(env, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ADMIN_IDS=\nPUBLIC_URL=\nWEBHOOK_SECRET=\nFAVOURITE_GROUPS=\n")
    settings = Settings(_env_file=dotenv)
    assert settings.admin_ids == []
    assert settings.public_url is None
    assert settings.webhook_secret is None


def test_blank_token_is_an_error(env):
    env.setenv("BOT_TOKEN", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
