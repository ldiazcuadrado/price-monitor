from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from price_monitor.logging_setup import JsonFormatter, KeyValueFormatter, configure_logging
from price_monitor.settings import Settings


def record(**extra: object) -> logging.LogRecord:
    rec = logging.LogRecord(
        "price_monitor.x", logging.WARNING, __file__, 1, "hello %s", ("world",), None
    )
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_json_formatter_includes_extras() -> None:
    payload = json.loads(JsonFormatter().format(record(site="books", count=3)))
    assert payload["message"] == "hello world"
    assert payload["level"] == "WARNING"
    assert payload["site"] == "books"
    assert payload["count"] == 3
    assert "taskName" not in payload


def test_key_value_formatter() -> None:
    line = KeyValueFormatter().format(record(site="books"))
    assert "WARNING" in line and "price_monitor.x hello world site=books" in line


def test_formatters_render_exceptions() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        rec = record()
        rec.exc_info = sys.exc_info()
    assert "ValueError: bad" in KeyValueFormatter().format(rec)
    assert "ValueError: bad" in json.loads(JsonFormatter().format(rec))["exception"]


def test_configure_logging_selects_formatter() -> None:
    configure_logging("DEBUG", "json")
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG
    configure_logging("INFO", "text")
    assert isinstance(root.handlers[0].formatter, KeyValueFormatter)


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.config_path == Path("sites.yaml")
    assert s.db_path == Path("data/price_monitor.db")
    assert s.user_agent.startswith("price-monitor/")
    assert s.telegram_bot_token is None


def test_settings_from_env_and_empty_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRICE_MONITOR_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("PRICE_MONITOR_SMTP_TO", "a@x.com,b@x.com")
    monkeypatch.setenv("PRICE_MONITOR_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("PRICE_MONITOR_SMTP_PORT", "")
    s = Settings(_env_file=None)
    assert s.db_path == tmp_path / "x.db"
    assert s.smtp_to == ["a@x.com", "b@x.com"]
    assert s.telegram_bot_token is None
    assert s.smtp_port == 587


def test_secrets_are_masked() -> None:
    s = Settings(_env_file=None, smtp_password="hunter2")
    assert "hunter2" not in repr(s)
    assert "hunter2" not in s.model_dump_json()


def test_invalid_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_MONITOR_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
