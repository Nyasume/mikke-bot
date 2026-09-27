import pytest
from pydantic import ValidationError

from mikke.config import Settings

REQUIRED = {"BOT_TOKEN": "42:TEST", "SAUCENAO_API_KEY": "key"}


@pytest.fixture
def env(monkeypatch):
    for name in (
        "ADMIN_IDS",
        "FAVOURITE_GROUPS",
        "BOT_MODE",
        "PUBLIC_URL",
        "WEBHOOK_SECRET",
        "REPORT_RESULTS",
        "REPORT_ERRORS",
        "TRACE_MOE_API_KEY",
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_RELEASE",
        "SENTRY_TRACES_SAMPLE_RATE",
    ):
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
    assert settings.trace_moe_api_key is None
    assert settings.sentry_dsn is None
    assert (settings.sentry_environment, settings.sentry_release) == ("production", None)
    assert settings.sentry_traces_sample_rate == 0.0


def test_trace_moe_api_key_is_optional(env, tmp_path):
    env.setenv("TRACE_MOE_API_KEY", "sponsor-key")
    assert Settings(_env_file=None).trace_moe_api_key.get_secret_value() == "sponsor-key"

    env.delenv("TRACE_MOE_API_KEY")
    dotenv = tmp_path / ".env"
    dotenv.write_text("TRACE_MOE_API_KEY=\n")
    assert Settings(_env_file=dotenv).trace_moe_api_key is None


def test_id_lists_are_comma_separated(env):
    env.setenv("ADMIN_IDS", "1, 22")
    env.setenv("FAVOURITE_GROUPS", "-1001;-1002")
    settings = Settings(_env_file=None)
    assert settings.admin_ids == [1, 22]
    assert settings.favourite_groups == [-1001, -1002]


def test_public_url_trailing_slash_is_dropped(env):
    env.setenv("PUBLIC_URL", "https://mikke.example.org/bot/")
    assert Settings(_env_file=None).public_url == "https://mikke.example.org/bot"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"PUBLIC_URL": "https://mikke.example.org/bot"},
        {"WEBHOOK_SECRET": "abc"},
        {"PUBLIC_URL": "https://mikke.example.org/bot", "WEBHOOK_SECRET": "not allowed!"},
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
    env.setenv("PUBLIC_URL", "https://mikke.example.org/bot")
    env.setenv("WEBHOOK_SECRET", "Secret_123-abc")
    assert Settings(_env_file=None).webhook_secret.get_secret_value() == "Secret_123-abc"


def test_blank_values_count_as_unset(env, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ADMIN_IDS=\nPUBLIC_URL=\nWEBHOOK_SECRET=\nFAVOURITE_GROUPS=\nSENTRY_DSN=\nSENTRY_RELEASE=\n")
    settings = Settings(_env_file=dotenv)
    assert settings.admin_ids == []
    assert settings.public_url is None
    assert settings.webhook_secret is None
    assert settings.sentry_dsn is None
    assert settings.sentry_release is None


def test_blank_token_is_an_error(env):
    env.setenv("BOT_TOKEN", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("rate", ["-0.1", "1.5"])
def test_traces_sample_rate_is_a_fraction(env, rate):
    env.setenv("SENTRY_TRACES_SAMPLE_RATE", rate)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_are_the_set_secret_values(env):
    assert Settings(_env_file=None).secrets() == ["42:TEST", "key"]
    env.setenv("TRACE_MOE_API_KEY", "sponsor-key")
    env.setenv("WEBHOOK_SECRET", "hook")
    env.setenv("SENTRY_DSN", "https://public@sentry.example.invalid/1")
    assert Settings(_env_file=None).secrets() == ["42:TEST", "key", "sponsor-key", "hook"]
