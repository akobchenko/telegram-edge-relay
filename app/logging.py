from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.core.request_id import get_request_id

_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(secret|token|authorization|signature|password|cookie)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = redact_log_value(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class PlainTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return super().format(record)


def configure_logging(level: str, json_logs: bool) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            PlainTextFormatter(
                "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
            )
        )
    root_logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact_log_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    if isinstance(value, dict):
        return {
            nested_key: redact_log_value(str(nested_key), nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [redact_log_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_log_value(key, item) for item in value)
    return value


def build_log_extra(
    *,
    direction: str,
    route: str,
    target: str,
    elapsed_ms: float | None,
    status: int | None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "direction": direction,
        "route": route,
        "target": target,
        "elapsed_ms": elapsed_ms,
        "status": status,
    }
    payload.update(extra)
    return payload
