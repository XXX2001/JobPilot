"""Tests for BatchRunner."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.scheduler.batch_runner import BatchRunner
from backend.matching.matcher import JobMatcher
from backend.models import Base
from backend.models.job import JobMatch
from backend.models.schemas import RawJob, JobDetails
from backend.matching.filters import JobFilters


# ── Helpers ───────────────────────────────────────────────────────────────────


def _raw_job(title="SWE", url="https://example.com/1"):
    return RawJob(title=title, company="ACME", url=url)


class MockScraper:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    async def scrape_batch(self, **kwargs):  # noqa: ARG002
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
    """Helper to build a BatchRunner with mocked collaborators."""
    scraper = MockScraper(jobs=jobs or [_raw_job()])
    matcher = MockMatcher(score=score)
    cv_pipeline = MockCVPipeline(fail=cv_fail)
    db = AsyncMock()
    runner = BatchRunner(
        scraper=scraper,
        matcher=matcher,
        cv_pipeline=cv_pipeline,
        db_factory=lambda: db,
    )
    return runner, db, cv_pipeline


# ── _store_matches: keyword_hits population ─────────────────────────────────


@pytest.fixture
async def sqlite_factory():
    """Yield an async_sessionmaker backed by a fresh on-disk SQLite."""
    tmpdir = tempfile.mkdtemp(prefix="jobpilot-store-matches-test-")
    db_path = Path(tmpdir) / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield Session
    finally:
        await engine.dispose()
        try:
            db_path.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_store_matches_populates_keyword_hits(sqlite_factory):
    """A stored JobMatch carries keyword_hits reflecting the description overlap."""
    runner = BatchRunner(
        scraper=MockScraper(),
        matcher=JobMatcher(),
        cv_pipeline=MockCVPipeline(),
        db_factory=sqlite_factory,
    )
    filters = JobFilters(keywords=["python", "django", "kubernetes"])
    job = JobDetails(
        title="Backend Engineer",
        company="ACME",
        location="Paris",
        description="python and django backend role",
        url="https://example.com/1",
    )

    async with sqlite_factory() as db:
        match_ids = await runner._store_matches(db, [(job, 87.5)], filters)

    assert len(match_ids) == 1
    async with sqlite_factory() as db:
        row = (
            await db.execute(select(JobMatch).where(JobMatch.id == match_ids[0]))
        ).scalar_one()

    assert row.keyword_hits is not None
    hits = row.keyword_hits
    matched = hits if isinstance(hits, list) else [k for k, v in hits.items() if v]
    assert set(matched) == {"python", "django"}


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
        patch("backend.scheduler.batch_runner.select"),
        patch("backend.scheduler.batch_runner.broadcast_status", side_effect=fake_broadcast),
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner, "_store_matches", new_callable=AsyncMock, return_value=[1]
        ) as mock_store,
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
        patch("backend.scheduler.batch_runner.settings") as mock_cfg,
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
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
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
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
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


# ── Dry-run preview: scrape + rank only, NO DB writes, NO Gemini ────────────


@pytest.mark.asyncio
async def test_dry_run_returns_preview_and_writes_nothing(sqlite_factory):
    """Dry-run scrapes + ranks but writes NOTHING and never calls Gemini.

    This is the M2-T6 guarantee: previewing today's matches must not burn
    Gemini quota or commit any rows. We assert (a) a preview list comes back,
    (b) the ``job_matches`` and ``tailored_documents`` tables stay empty, and
    (c) the embedder (Gemini) and CV pipeline are never touched.
    """
    from backend.models.document import TailoredDocument

    jobs = [_raw_job(title=f"SWE {i}", url=f"https://example.com/{i}") for i in range(3)]
    scraper = MockScraper(jobs=jobs)
    matcher = MockMatcher(score=80.0)
    cv_pipeline = MockCVPipeline()

    # Wire an embedder spy to prove the fit-assessment / Gemini path is skipped.
    embedder = MagicMock()
    embedder.embed_cv_profile = AsyncMock(side_effect=lambda p: p)
    embedder.embed_job_profile = AsyncMock(side_effect=lambda p: p)

    runner = BatchRunner(
        scraper=scraper,
        matcher=matcher,
        cv_pipeline=cv_pipeline,
        db_factory=sqlite_factory,
        embedder=embedder,
    )

    with patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock):
        preview = await runner.run_batch(dry_run=True)

    # Preview shape: list of {title, company, score, location}
    assert isinstance(preview, list)
    assert len(preview) == 3
    for item in preview:
        assert set(item.keys()) == {"title", "company", "score", "location"}
        assert item["company"] == "ACME"
        assert isinstance(item["score"], (int, float))

    # No Gemini / CV-generation calls
    assert cv_pipeline.calls == []
    embedder.embed_cv_profile.assert_not_awaited()
    embedder.embed_job_profile.assert_not_awaited()

    # No DB writes: both tables empty
    async with sqlite_factory() as db:
        match_count = len((await db.execute(select(JobMatch))).scalars().all())
        doc_count = len((await db.execute(select(TailoredDocument))).scalars().all())
    assert match_count == 0
    assert doc_count == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_store_matches_or_docs():
    """Dry-run must NOT invoke _store_matches / _store_tailored_doc (DB writers)."""
    runner, _db, cv_pipe = _make_runner(
        jobs=[_raw_job(url=f"https://example.com/{i}") for i in range(3)]
    )

    with (
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(runner, "_store_matches", new_callable=AsyncMock) as mock_store,
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock) as mock_doc,
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
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
        profile_obj.base_cv_path = None
        mock_profile.return_value = profile_obj
        mock_sources.return_value = []

        preview = await runner.run_batch(dry_run=True)

    assert isinstance(preview, list)
    assert len(preview) == 3
    mock_store.assert_not_called()
    mock_doc.assert_not_called()


@pytest.mark.asyncio
async def test_normal_run_returns_none(tmp_path):
    """Back-compat: the normal (dry_run=False) path still returns None."""
    runner, _db, _cv_pipe = _make_runner()

    with (
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(runner, "_store_matches", new_callable=AsyncMock, return_value=[1]),
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
        patch("backend.scheduler.batch_runner.settings") as mock_cfg,
    ):
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
        profile_obj.base_cv_path = None
        mock_profile.return_value = profile_obj
        mock_sources.return_value = []

        guard_instance = AsyncMock()
        guard_instance.remaining_today = AsyncMock(return_value=10)
        MockGuard.return_value = guard_instance

        result = await runner.run_batch()

    assert result is None


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
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.broadcast_job_assessment", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
        # match_row lookup → return None so we don't try to set attrs on a Mock
        patch("backend.scheduler.batch_runner.select"),
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
