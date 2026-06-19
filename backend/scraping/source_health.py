"""Per-source health tracker for the scraping subsystem.

In-memory only — lives for the lifetime of the orchestrator singleton on
``app.state``. Replaces the deleted ``backend/utils/source_health.py``.

Records, per source name (e.g. ``"linkedin"``, ``"indeed"``, ``"adzuna"``):

* the outcome of the last N scrape attempts (``ok`` / ``empty`` / ``error``)
* the timestamp of the last attempt
* a running count of consecutive failures (``empty`` or ``error``)
* the last error message, if any

The orchestrator calls :meth:`SourceHealthTracker.record` after each per-
source attempt. The new ``GET /api/queue/source-health`` route reads
:meth:`SourceHealthTracker.snapshot` and ships it to the frontend so the
queue sidebar can render a pill per source (green / yellow / red).

This module is deliberately dependency-free — no DB, no httpx, no
file I/O — so it stays cheap and easy to mock in tests.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Iterable, Literal

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "empty", "error"]
Status = Literal["healthy", "degraded", "down", "unknown"]

# Window over which we compute the "degraded" / "down" verdict. Small
# enough that a single bad run is visible, big enough that a one-off blip
# doesn't repaint the UI red.
_HISTORY_WINDOW = 5

# Verdict thresholds. Tuned for the per-batch cadence: a user typically
# triggers 1-3 scans per session, so consecutive failures should escalate
# quickly. These knobs are conservative; tune later if noisy.
_DEGRADED_AFTER = 1   # 1 consecutive non-ok run → degraded
_DOWN_AFTER = 3       # 3 consecutive non-ok runs → down


@dataclass
class SourceRecord:
    """In-memory health record for a single source."""

    source: str
    last_outcome: Outcome | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    last_job_count: int = 0
    total_attempts: int = 0
    total_jobs: int = 0
    history: Deque[Outcome] = field(default_factory=lambda: deque(maxlen=_HISTORY_WINDOW))

    def status(self) -> Status:
        if self.last_outcome is None:
            return "unknown"
        if self.consecutive_failures >= _DOWN_AFTER:
            return "down"
        if self.consecutive_failures >= _DEGRADED_AFTER:
            return "degraded"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "status": self.status(),
            "last_outcome": self.last_outcome,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_job_count": self.last_job_count,
            "total_attempts": self.total_attempts,
            "total_jobs": self.total_jobs,
            "history": list(self.history),
        }


class SourceHealthTracker:
    """In-memory tracker keyed by source name.

    Thread-safety: not thread-safe by design — the orchestrator drives
    Phase 2 sequentially and Phase 1/3 from a single event loop, so all
    writes happen on the asyncio event-loop thread. No lock needed.
    """

    def __init__(self) -> None:
        self._records: dict[str, SourceRecord] = {}

    def record(
        self,
        source: str,
        *,
        outcome: Outcome,
        job_count: int = 0,
        error: str | None = None,
    ) -> SourceRecord:
        """Record the outcome of a scrape attempt.

        Args:
            source: Source name (e.g. ``"linkedin"``, ``"indeed"``, ``"adzuna"``).
            outcome: One of ``"ok"`` (>=1 job), ``"empty"`` (0 jobs, no exception),
                ``"error"`` (exception raised).
            job_count: Number of jobs returned (defaults to 0).
            error: Optional error message — only meaningful when
                ``outcome == "error"``.
        """
        rec = self._records.get(source)
        if rec is None:
            rec = SourceRecord(source=source)
            self._records[source] = rec

        now = datetime.now(timezone.utc)
        rec.last_outcome = outcome
        rec.last_attempt_at = now
        rec.last_job_count = job_count
        rec.total_attempts += 1
        rec.total_jobs += max(0, job_count)
        rec.history.append(outcome)

        if outcome == "ok":
            rec.consecutive_failures = 0
            rec.last_success_at = now
            rec.last_error = None
        else:
            rec.consecutive_failures += 1
            if outcome == "error":
                rec.last_error = (error or "")[:500] or None
            # Don't clobber the last error message on a follow-up "empty"
            # — that often carries the same root cause and we want to
            # surface the most informative line.

        return rec

    def get(self, source: str) -> SourceRecord | None:
        return self._records.get(source)

    def snapshot(self) -> list[dict]:
        """Return a JSON-ready snapshot, sorted by source name."""
        return [self._records[k].to_dict() for k in sorted(self._records)]

    def reset(self, sources: Iterable[str] | None = None) -> None:
        """Clear stats. Mostly for tests."""
        if sources is None:
            self._records.clear()
        else:
            for s in sources:
                self._records.pop(s, None)


__all__ = ["SourceHealthTracker", "SourceRecord", "Outcome", "Status"]
