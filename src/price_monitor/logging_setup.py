"""Structured logging: key=value text or JSON lines, with ``extra`` fields preserved."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD}


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [stamp, f"{record.levelname:<7}", record.name, record.getMessage()]
        parts += [f"{k}={v}" for k, v in _extras(record).items()]
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else KeyValueFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
