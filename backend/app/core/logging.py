"""Logging estructurado en JSON con redacción de secretos."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SECRET_KEYS = re.compile(r"(secret|password|passwd|token|api[_-]?key|signature|cookie)", re.I)
_SECRET_INLINE = re.compile(r"(?i)(signature|api[_-]?key|secret|password|token)=([^&\s\"']+)")
_DSN_PASSWORD = re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)")

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _SECRET_INLINE.sub(r"\1=***", value)
        return _DSN_PASSWORD.sub(r"\1***\3", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(str(record.msg))
        return super().format(record)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else PlainFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
