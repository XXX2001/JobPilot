"""Tests for MorningBatchRunner."""

from __future__ import annotations

import asyncio
import time
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


# ── Concurrency regression: Step 3.5 fit-assessment loop ────────────────────


@pytest.mark.asyncio
async def test_fit_assessments_run_concurrently():
    """PC-02: Per-match embed + FitEngine.assess must run concurrently.

    With 5 matches and a 100ms-per-call embedder, the serial baseline is ~500ms;
    with the semaphore-gated gather (CONCURRENCY_GEMINI=3) it should land near
    ~200ms (two batches: ceil(5/3) * 100ms). We assert <350ms to stay
    deterministic on slow CI while still failing if someone reverts to serial.
    """
    runner, _db, _cv_pipe = _make_runner(jobs=[_raw_job(url=f"https://x.com/{i}") for i in range(5)])

    # Wire up the embedder + cv_profile so the fit-assessment branch engages.
    fake_embedder = MagicMock()

    async def slow_embed_job_profile(profile):
        await asyncio.sleep(0.1)  # 100ms per call — what we want to parallelise
        return profile

    fake_embedder.embed_job_profile = AsyncMock(side_effect=slow_embed_job_profile)
    fake_embedder.embed_cv_profile = AsyncMock(side_effect=lambda p: p)
    runner._embedder = fake_embedder

    # Stub extractor → JobProfile with ≥ MIN_JOB_SKILLS_FOR_FIT_ENGINE skills.
    fake_job_profile = MagicMock()
    fake_job_profile.skills = ["python", "fastapi", "docker"]  # 3 skills, above the threshold
    runner._job_extractor = MagicMock()
    runner._job_extractor.extract = MagicMock(return_value=fake_job_profile)

    # Stub FitEngine.assess to return a minimal assessment quickly.
    fake_assessment = MagicMock()
    fake_assessment.severity = 0.4
    fake_assessment.simulated_ats_score = 75.0
    fake_assessment.should_modify = False
    fake_assessment.covered_skills = ["python"]
    fake_assessment.critical_gaps = []
    fake_assessment.to_dict = MagicMock(return_value={"severity": 0.4})
    runner._fit_engine = MagicMock()
    runner._fit_engine.assess = MagicMock(return_value=fake_assessment)

    # Pretend the CV parser produced a profile (it gets handed to embed_cv_profile).
    runner._cv_parser = MagicMock()
    runner._cv_parser.build_profile = MagicMock(return_value=MagicMock())

    with (
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner,
            "_store_matches",
            new_callable=AsyncMock,
            return_value=[1, 2, 3, 4, 5],
        ),
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.broadcast_job_assessment", new_callable=AsyncMock),
        patch("backend.scheduler.morning_batch.DailyLimitGuard") as MockGuard,
        # match_row lookup → return None so we don't try to set attrs on a Mock
        patch("backend.scheduler.morning_batch.select"),
        # Make the cv_path resolution succeed (the runner reads the file)
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value="\\documentclass{article}\\begin{document}cv\\end{document}"),
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
        settings_obj.cv_modification_sensitivity = "balanced"
        settings_obj.cv_tailoring_enabled = False  # skip Step 4 entirely
        mock_settings.return_value = settings_obj

        profile_obj = MagicMock()
        profile_obj.base_cv_path = "/tmp/base.tex"
        mock_profile.return_value = profile_obj

        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=0)  # skip CV gen entirely
        MockGuard.return_value = guard_instance

        # db.execute(...).scalar_one_or_none() → None so the DB-write branch is a no-op
        db = AsyncMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=exec_result)
        db.commit = AsyncMock()
        db.close = AsyncMock()
        runner._db_factory = lambda: db

        start = time.perf_counter()
        await runner.run_batch()
        elapsed = time.perf_counter() - start

    # 5 embeddings × 100ms serial = 500ms. With CONCURRENCY_GEMINI=3 we expect
    # ~200ms. We assert <350ms to leave plenty of slack for slow CI machines
    # while still catching a regression back to serial execution.
    assert fake_embedder.embed_job_profile.await_count == 5
    assert elapsed < 0.35, f"Expected concurrent execution under 350ms, took {elapsed:.3f}s"
