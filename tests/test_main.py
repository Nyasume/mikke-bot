import logging
import sys

from reverse_search_bot.__main__ import RedactingFormatter


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

