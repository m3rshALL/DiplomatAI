from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping


request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)


@dataclass(frozen=True)
class LogContext:
    request_id: str | None
    user_id: int | None


def get_log_context() -> LogContext:
    return LogContext(request_id=request_id_var.get(), user_id=user_id_var.get())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = get_log_context()
        if ctx.request_id:
            payload["request_id"] = ctx.request_id
        if ctx.user_id is not None:
            payload["user_id"] = ctx.user_id

        # structured extras (best-effort)
        extras: Mapping[str, Any] = getattr(record, "extra", {}) or {}
        for k, v in extras.items():
            if k not in payload:
                payload[k] = v

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    log_level = level or os.getenv("LOG_LEVEL", "INFO")
    root = logging.getLogger()
    root.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


