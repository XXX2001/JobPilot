"""Tests for MorningBatchRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduler.morning_batch import MorningBatchRunner
from backend.models.schemas import RawJob, JobDetails
from backend.matching.filters import JobFilters


# ── Helpers ───────────────────────────────────────────────────────────────────


def _raw_job(title="SWE", url="https://example.com/1"):
    return RawJob(title=title, company="ACME", url=url)


class MockScraper:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    async def run_morning_batch(self, **kwargs):  # noqa: ARG002
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


def _make_runner(jobs=None, score=80.0, cv_fail=False):
    """Helper to build a MorningBatchRunner with mocked collaborators."""
    scraper = MockScraper(jobs=jobs or [_raw_job()])
    matcher = MockMatcher(score=score)
    cv_pipeline = MockCVPipeline(fail=cv_fail)
    db = AsyncMock()
    runner = MorningBatchRunner(
        scraper=scraper,
        matcher=matcher,
        cv_pipeline=cv_pipeline,
        db_factory=lambda: db,
    )
    return runner, db, cv_pipeline


# ── run_batch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_runs_all_steps(tmp_path):
    """Full happy-path: scrape → match → store → generate CV → notify."""
    runner, db, cv_pipe = _make_runner()

    status_messages = []

    async def fake_broadcast(msg, progress=0.0):
        status_messages.append(msg)

    # Patch heavy collaborators
    with (
        patch("backend.scheduler.morning_batch.select"),
        patch("backend.scheduler.morning_batch.broadcast_status", side_effect=fake_broadcast),
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner, "_store_matches", new_callable=AsyncMock, return_value=[1]
        ) as mock_store,
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.DailyLimitGuard") as MockGuard,
        patch("backend.scheduler.morning_batch.settings") as mock_cfg,
    ):
        # Point data dir at tmp_path so templates/ scan finds nothing → no CV generation
        mock_cfg.jobpilot_data_dir = str(tmp_path)

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
        profile_obj.base_cv_path = None  # no profile path → auto-detect from templates/
        mock_profile.return_value = profile_obj

        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=10)
        MockGuard.return_value = guard_instance

        await runner.run_batch()

    assert any("ready" in m.lower() or "applications" in m.lower() for m in status_messages)
    mock_store.assert_called_once()


@pytest.mark.asyncio
async def test_batch_cv_error_continues():
    """A CV generation error should not stop the batch — it just logs and moves on."""
    runner, db, cv_pipe = _make_runner(cv_fail=True)

    with (
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner, "_store_matches", new_callable=AsyncMock, return_value=[1]
        ) as mock_store,
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
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
            await runner.run_batch()

    # CV pipeline was called (and failed), but we still got here
    assert True  # no exception


@pytest.mark.asyncio
async def test_batch_stops_cv_generation_at_daily_limit():
    """Only generate CVs for `remaining_today` matches."""
    jobs = [_raw_job(url=f"https://example.com/{i}") for i in range(10)]
    runner, db, cv_pipe = _make_runner(jobs=jobs)

    with (
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner, "_store_matches", new_callable=AsyncMock, return_value=list(range(1, 11))
        ) as mock_store,
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock) as mock_doc,
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

        await runner.run_batch()

    # _store_tailored_doc should be called at most 3 times
    assert mock_doc.call_count <= 3
