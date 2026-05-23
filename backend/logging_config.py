"""Structured JSON logging configuration for JobPilot.

This module is the single entry point for wiring application logging. It:

  * Honors the ``JOBPILOT_LOG_LEVEL`` environment variable (case-insensitive)
    via ``backend.config.settings.jobpilot_log_level``.
  * Installs a JSON formatter on the root logger so every log record is a
    single JSON object per line (suitable for Loki / ELK / CloudWatch).
  * Adds a ``RotatingFileHandler`` writing to
    ``<DATA_DIR>/logs/jobpilot.log`` (10 MiB per file, 5 backups).
  * Keeps a console (stderr) handler with the same JSON formatter.
  * Is idempotent — calling :func:`configure_logging` twice does not stack
    handlers.

No third-party deps. The JSON formatter is a small ``logging.Formatter``
subclass written inline below.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Sentinel attribute placed on handlers we install, so a second call to
# ``configure_logging`` can recognize and remove them before re-installing.
_JOBPILOT_HANDLER_MARKER = "_jobpilot_managed"

# Standard LogRecord attributes that we either handle explicitly or want to
# exclude from the ``extra`` bag. Everything else attached to a record (via
# ``logger.info("...", extra={...})``) is forwarded under ``extra``.
_RESERVED_LOGRECORD_ATTRS = {
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
    "message",
    "module",
    "msecs",
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


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Format::

        {
            "ts": "<iso8601 UTC>",
            "level": "INFO",
            "logger": "jobpilot.scraper",
            "msg": "rendered message string",
            "module": "scraper",
            "line": 42,
            "extra": { ... user-supplied extras ... },
            "exc_info": "Traceback ..."   # only when exc_info=True
        }
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - simple override
        # Render the message with its args first (matches stdlib behavior).
        message = record.getMessage()

        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
            "module": record.module,
            "line": record.lineno,
        }

        # Collect user-supplied ``extra`` fields. Anything on the record that
        # isn't a built-in LogRecord attribute is treated as caller extras.
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            extras[key] = value
        if extras:
            payload["extra"] = extras

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _parse_level(raw: str | int | None) -> int:
    """Convert a log-level string (case-insensitive) to a stdlib int level.

    Falls back to ``logging.INFO`` when unrecognized.
    """
    if isinstance(raw, int):
        return raw
    if not raw:
        return logging.INFO
    name = str(raw).strip().upper()
    # logging.getLevelName returns either an int (when name is known) or the
    # string "Level X" — we coerce unknown values to INFO.
    candidate = logging.getLevelName(name)
    if isinstance(candidate, int):
        return candidate
    return logging.INFO


def _resolve_data_dir(data_dir: Path | str | None) -> Path:
    """Resolve where ``logs/jobpilot.log`` should live.

    When ``data_dir`` is ``None`` we fall back to ``settings.jobpilot_data_dir``
    (resolved relative to the project root, matching ``backend.config.DATA_DIR``).
    """
    if data_dir is not None:
        return Path(data_dir)
    # Import lazily so tests that monkeypatch settings before importing this
    # module still see the correct value.
    from backend.config import DATA_DIR  # type: ignore

    return Path(DATA_DIR)


def _remove_managed_handlers(logger: logging.Logger) -> None:
    """Strip handlers we previously attached (idempotency)."""
    for handler in list(logger.handlers):
        if getattr(handler, _JOBPILOT_HANDLER_MARKER, False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def configure_logging(
    data_dir: Path | str | None = None,
    level: str | int | None = None,
) -> None:
    """Wire JSON logging + rotating file handler.

    Parameters
    ----------
    data_dir:
        Base data directory. The log file is written to
        ``<data_dir>/logs/jobpilot.log``. When ``None`` we fall back to
        ``backend.config.DATA_DIR`` (which itself derives from
        ``settings.jobpilot_data_dir``).
    level:
        Override log level. When ``None`` we read
        ``settings.jobpilot_log_level``.

    The function is **idempotent**: calling it twice does not stack handlers.
    A sentinel attribute on each handler lets us recognize and replace our
    own handlers without disturbing any others a caller may have attached.
    """
    # Resolve level — prefer explicit arg, else settings.
    if level is None:
        try:
            from backend.config import settings  # type: ignore

            level = settings.jobpilot_log_level
        except Exception:
            level = "info"
    numeric_level = _parse_level(level)

    formatter = JSONFormatter()

    root = logging.getLogger()
    jobpilot_logger = logging.getLogger("jobpilot")

    # Idempotency: tear down any handlers we previously installed.
    _remove_managed_handlers(root)
    _remove_managed_handlers(jobpilot_logger)

    root.setLevel(numeric_level)
    jobpilot_logger.setLevel(numeric_level)
    # Let records bubble up to root so the file handler picks them up too.
    jobpilot_logger.propagate = True

    # ── Console handler (stderr) ────────────────────────────────────────
    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    setattr(console, _JOBPILOT_HANDLER_MARKER, True)
    root.addHandler(console)

    # ── Rotating file handler ───────────────────────────────────────────
    try:
        base_dir = _resolve_data_dir(data_dir)
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "jobpilot.log"

        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MiB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        setattr(file_handler, _JOBPILOT_HANDLER_MARKER, True)
        root.addHandler(file_handler)
    except Exception as exc:  # pragma: no cover - defensive
        # Don't crash the app just because we can't open the log file.
        # The console handler is already installed, so this is logged
        # via the standard path.
        logging.getLogger("jobpilot.logging").warning(
            "Failed to attach rotating file handler: %s", exc
        )
