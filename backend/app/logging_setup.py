"""Structured logging.

By default this logs plain, readable lines to stdout -- fine for running
`./start.sh` on a laptop and watching the terminal. Set CLINIC_LOG_FORMAT=json
(the Docker image does this by default) to get one JSON object per line
instead, which is what you want once logs are going to a file, journald, or
a log aggregator (Loki, CloudWatch, ELK, ...) rather than a human's eyeballs.

Every request gets a short request_id that is included in every log line
that request produces (including any exception) and is echoed back in the
X-Request-ID response header, so a clinician's bug report ("I got an error
at 14:32") can be tied back to the exact log lines for that request.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    fmt = os.getenv("CLINIC_LOG_FORMAT", "text").lower()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("CLINIC_LOG_LEVEL", "INFO").upper())
    # uvicorn's own loggers otherwise install their own handlers/formatting
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = [handler]
        logging.getLogger(name).propagate = False


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def bind_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()


access_logger = logging.getLogger("clinic.access")
app_logger = logging.getLogger("clinic.app")
