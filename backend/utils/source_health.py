"""Basic source health monitoring — tracks success/failure rates per scraping source."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SourceHealthRecord:
    source_name: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_error: Optional[str] = None
    avg_jobs_per_run: float = 0.0
    _job_counts: list[int] = field(default_factory=list, repr=False)

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.successful_runs / self.total_runs

    @property
    def is_healthy(self) -> bool:
        """Source is healthy if success rate > 50% or has never been run."""
        if self.total_runs == 0:
            return True
        if self.total_runs < 3:
            return self.successful_runs > 0
        return self.success_rate > 0.5

    @property
    def consecutive_failures(self) -> int:
        """Check if recent runs are all failures."""
        if not self.last_failure or not self.last_success:
            return self.failed_runs
        if self.last_success > self.last_failure:
            return 0
        # Approximate — exact tracking would need a history list
        return min(self.failed_runs, 3)


class SourceHealthMonitor:
    """In-memory health tracker for scraping sources."""

    def __init__(self) -> None:
        self._records: dict[str, SourceHealthRecord] = {}

    def get(self, source_name: str) -> SourceHealthRecord:
        if source_name not in self._records:
            self._records[source_name] = SourceHealthRecord(source_name=source_name)
        return self._records[source_name]

    def record_success(self, source_name: str, jobs_found: int) -> None:
        rec = self.get(source_name)
        rec.total_runs += 1
        rec.successful_runs += 1
        rec.last_success = datetime.utcnow()
        rec._job_counts.append(jobs_found)
        # Keep only last 10 runs for avg
        if len(rec._job_counts) > 10:
            rec._job_counts = rec._job_counts[-10:]
        rec.avg_jobs_per_run = sum(rec._job_counts) / len(rec._job_counts)
        logger.info("Source %s: success (%d jobs found)", source_name, jobs_found)

    def record_failure(self, source_name: str, error: str) -> None:
        rec = self.get(source_name)
        rec.total_runs += 1
        rec.failed_runs += 1
        rec.last_failure = datetime.utcnow()
        rec.last_error = error[:500]  # truncate long errors
        logger.warning("Source %s: failure — %s", source_name, error[:200])

    def get_all(self) -> list[SourceHealthRecord]:
        return list(self._records.values())

    def get_summary(self) -> dict[str, dict]:
        """Return a JSON-serializable summary of all sources."""
        return {
            name: {
                "total_runs": rec.total_runs,
                "success_rate": round(rec.success_rate, 2),
                "is_healthy": rec.is_healthy,
                "avg_jobs_per_run": round(rec.avg_jobs_per_run, 1),
                "last_error": rec.last_error,
            }
            for name, rec in self._records.items()
        }


# Singleton instance
health_monitor = SourceHealthMonitor()
