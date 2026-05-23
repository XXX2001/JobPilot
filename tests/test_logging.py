"""Tests for backend.logging_config.

Covers:
  * JSON file output is valid JSON with the documented fields.
  * Lowercase log-level strings (the format used in env / .env files) are
    honored — "debug" -> logging.DEBUG, "info" -> logging.INFO, etc.
  * Idempotency: calling configure_logging twice does not stack handlers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from backend.logging_config import (
    _JOBPILOT_HANDLER_MARKER,
    _parse_level,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Snapshot + restore root logger state around each test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    jp = logging.getLogger("jobpilot")
    jp_saved_handlers = list(jp.handlers)
    jp_saved_level = jp.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            if h not in saved_handlers:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        root.setLevel(saved_level)
        for h in list(jp.handlers):
            if h not in jp_saved_handlers:
                jp.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        jp.setLevel(jp_saved_level)


def test_json_line_written_to_rotating_file(tmp_path: Path):
    configure_logging(data_dir=tmp_path, level="info")

    logger = logging.getLogger("jobpilot.test")
    logger.info("hello world", extra={"job_id": 42})

    # Flush all handlers so the file is on disk.
    for h in logging.getLogger().handlers:
        h.flush()

    log_path = tmp_path / "logs" / "jobpilot.log"
    assert log_path.exists(), "rotating file handler did not create log file"

    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "no log lines were written"

    record = None
    for ln in lines:
        parsed = json.loads(ln)
        if parsed.get("msg") == "hello world":
            record = parsed
            break
    assert record is not None, f"target log line not found in {lines}"

    # Documented fields are present.
    for key in ("ts", "level", "logger", "msg", "module", "line"):
        assert key in record, f"missing field {key!r} in {record}"

    assert record["level"] == "INFO"
    assert record["logger"] == "jobpilot.test"
    assert record["msg"] == "hello world"
    assert isinstance(record["line"], int)

    # Caller extras land under "extra".
    assert record.get("extra", {}).get("job_id") == 42


def test_lowercase_level_strings_are_honored(tmp_path: Path):
    """`JOBPILOT_LOG_LEVEL=debug` (lowercase) must map to logging.DEBUG."""
    # _parse_level is the function the env value flows through.
    assert _parse_level("debug") == logging.DEBUG
    assert _parse_level("info") == logging.INFO
    assert _parse_level("warning") == logging.WARNING
    assert _parse_level("error") == logging.ERROR
    # Mixed case + unknown fallback.
    assert _parse_level("Debug") == logging.DEBUG
    assert _parse_level("nonsense") == logging.INFO

    # And end-to-end: configure_logging(level="debug") sets the root logger
    # to DEBUG.
    configure_logging(data_dir=tmp_path, level="debug")
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("jobpilot").level == logging.DEBUG


def test_configure_logging_is_idempotent(tmp_path: Path):
    """A second call must not stack additional handlers."""
    configure_logging(data_dir=tmp_path, level="info")
    first_count = sum(
        1 for h in logging.getLogger().handlers if getattr(h, _JOBPILOT_HANDLER_MARKER, False)
    )

    configure_logging(data_dir=tmp_path, level="info")
    second_count = sum(
        1 for h in logging.getLogger().handlers if getattr(h, _JOBPILOT_HANDLER_MARKER, False)
    )

    assert first_count == second_count == 2, (
        "expected exactly one console + one file handler tagged as managed; "
        f"first={first_count}, second={second_count}"
    )


def test_exc_info_is_serialized_as_string(tmp_path: Path):
    configure_logging(data_dir=tmp_path, level="info")
    logger = logging.getLogger("jobpilot.test.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("caught it")

    for h in logging.getLogger().handlers:
        h.flush()

    log_path = tmp_path / "logs" / "jobpilot.log"
    payloads = [
        json.loads(ln)
        for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    target = next((p for p in payloads if p.get("msg") == "caught it"), None)
    assert target is not None
    assert "exc_info" in target
    assert "ValueError" in target["exc_info"]
    assert "boom" in target["exc_info"]
