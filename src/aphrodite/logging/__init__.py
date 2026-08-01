"""Logging module for Aphrodite Agent.

Honors the ``[logging]`` section of the config: level, JSON-vs-text format,
and debug mode. All aphrodite.* loggers inherit from the ``aphrodite`` root
logger configured here, so the module-level ``get_logger`` helper stays the
single entry point for new modules.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from ..config import Config


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aphrodite.{name}")


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record for machine-friendly logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(config: Config | None = None, debug: bool = False) -> logging.Logger:
    """Configure the ``aphrodite`` root logger from config (idempotent)."""
    root = logging.getLogger("aphrodite")
    root.handlers.clear()
    root.propagate = False

    if debug or (config is not None and config.logging.debug_mode):
        level = logging.DEBUG
    elif config is not None:
        level = getattr(logging, str(config.logging.level).upper(), logging.INFO)
    else:
        level = logging.INFO

    formatter: logging.Formatter
    if config is not None and config.logging.format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
    return root
