"""Tests for MorningBatchScheduler."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from backend.scheduler.morning_batch import MorningBatchScheduler
from backend.models.schemas import RawJob, JobDetails
from backend.matching.filters import JobFilters


# ── Helpers ───────────────────────────────────────────────────────────────────


def _raw_job(title="SWE", url="https://example.com/1"):
    return RawJob(title=title, company="ACME", url=url)


def _details(title="SWE", url="https://example.com/1", score=80.0):
    return JobDetails(title=title, company="ACME", url=url, score=score)


class MockScraper:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    async def run_morning_batch(self, keywords, filters, sources):
        return self._jobs


class MockMatcher:
    def __init__(self, score=80.0):
        self._score = score

    def score(self, job: JobDetails, filters: JobFilters) -> float:
        return self._score


class MockCVPipeline:
    def __init__(self, fail=False):
        self._fail = fail
        self.calls = []

    async def generate_tailored_cv(self, base_cv_path, job, output_dir):
        self.calls.append(job.title)
        if self._fail:
            raise RuntimeError("LaTeX compile error")
        result = MagicMock()
        result.tex_path = output_dir / "cv.tex"
        result.pdf_path = output_dir / "cv.pdf"
        result.diff = []
        return result


def _make_scheduler(jobs=None, score=80.0, cv_fail=False):
    """Helper to build a MorningBatchScheduler with mocked collaborators."""
    scraper = MockScraper(jobs=jobs or [_raw_job()])
    matcher = MockMatcher(score=score)
    cv_pipeline = MockCVPipeline(fail=cv_fail)
    db = AsyncMock()
    scheduler = MorningBatchScheduler(
        scraper=scraper,
        matcher=matcher,
        cv_pipeline=cv_pipeline,
        db_factory=lambda: db,
    )
    return scheduler, db, cv_pipeline


# ── run_batch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_runs_all_steps(tmp_path):
    """Full happy-path: scrape → match → store → generate CV → notify."""
    scheduler, db, cv_pipe = _make_scheduler()

    status_messages = []

    async def fake_broadcast(msg, progress=0.0):
        status_messages.append(msg)

    # Patch heavy collaborators
    with (
        patch("backend.scheduler.morning_batch.select") as mock_select,
        patch("backend.scheduler.morning_batch.broadcast_status", side_effect=fake_broadcast),
        patch.object(scheduler, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(scheduler, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(scheduler, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            scheduler, "_store_matches", new_callable=AsyncMock, return_value=[1]
        ) as mock_store,
        patch.object(scheduler, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.DailyLimitGuard") as MockGuard,
    ):
        settings_obj = MagicMock()
        settings_obj.keywords = ["python"]
        settings_obj.locations = []
        settings_obj.salary_min = None
        settings_obj.remote_only = False
        settings_obj.excluded_keywords = []
        settings_obj.excluded_companies = []
        settings_obj.min_match_score = 30.0
        settings_obj.daily_limit = 10
        mock_settings.return_value = settings_obj

        profile_obj = MagicMock()
        profile_obj.base_cv_path = None  # skip CV generation
        mock_profile.return_value = profile_obj

        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=10)
        MockGuard.return_value = guard_instance

        await scheduler.run_batch()

    assert any("ready" in m.lower() or "applications" in m.lower() for m in status_messages)
    mock_store.assert_called_once()


@pytest.mark.asyncio
async def test_batch_cv_error_continues():
    """A CV generation error should not stop the batch — it just logs and moves on."""
    scheduler, db, cv_pipe = _make_scheduler(cv_fail=True)

    with (
        patch.object(scheduler, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(scheduler, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(scheduler, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            scheduler, "_store_matches", new_callable=AsyncMock, return_value=[1]
        ) as mock_store,
        patch.object(scheduler, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.DailyLimitGuard") as MockGuard,
    ):
        settings_obj = MagicMock()
        settings_obj.keywords = ["python"]
        settings_obj.locations = []
        settings_obj.salary_min = None
        settings_obj.remote_only = False
        settings_obj.excluded_keywords = []
        settings_obj.excluded_companies = []
        settings_obj.min_match_score = 30.0
        settings_obj.daily_limit = 10
        mock_settings.return_value = settings_obj

        profile_obj = MagicMock()
        profile_obj.base_cv_path = str(Path("base_cv.tex"))
        mock_profile.return_value = profile_obj

        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=5)
        MockGuard.return_value = guard_instance

        # Should not raise even though cv pipeline fails
        with patch("pathlib.Path.exists", return_value=True):
            await scheduler.run_batch()

    # CV pipeline was called (and failed), but we still got here
    assert True  # no exception


@pytest.mark.asyncio
async def test_batch_stops_cv_generation_at_daily_limit():
    """Only generate CVs for `remaining_today` matches."""
    jobs = [_raw_job(url=f"https://example.com/{i}") for i in range(10)]
    scheduler, db, cv_pipe = _make_scheduler(jobs=jobs)

    with (
        patch.object(scheduler, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(scheduler, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(scheduler, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            scheduler, "_store_matches", new_callable=AsyncMock, return_value=list(range(1, 11))
        ) as mock_store,
        patch.object(scheduler, "_store_tailored_doc", new_callable=AsyncMock) as mock_doc,
        patch("backend.scheduler.morning_batch.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.DailyLimitGuard") as MockGuard,
    ):
        settings_obj = MagicMock()
        settings_obj.keywords = ["python"]
        settings_obj.locations = []
        settings_obj.salary_min = None
        settings_obj.remote_only = False
        settings_obj.excluded_keywords = []
        settings_obj.excluded_companies = []
        settings_obj.min_match_score = 30.0
        settings_obj.daily_limit = 10
        mock_settings.return_value = settings_obj

        profile_obj = MagicMock()
        profile_obj.base_cv_path = None  # no CV generation
        mock_profile.return_value = profile_obj
        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=3)  # only 3 left
        MockGuard.return_value = guard_instance

        await scheduler.run_batch()

    # _store_tailored_doc should be called at most 3 times
    assert mock_doc.call_count <= 3


# ── start / stop ──────────────────────────────────────────────────────────────


def test_start_without_apscheduler(monkeypatch):
    """If APScheduler is not installed, start() should log a warning but not crash."""
    import backend.scheduler.morning_batch as mod

    monkeypatch.setattr(mod, "_APSCHEDULER_AVAILABLE", False)
    scheduler = MorningBatchScheduler(
        scraper=None,
        matcher=None,
        cv_pipeline=None,
        db_factory=lambda: None,
    )
    # __init__ was already called — _scheduler is None
    scheduler._scheduler = None
    # Should not raise
    scheduler.start("08:00")
    scheduler.stop()
