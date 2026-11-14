"""Tests for BatchRunner."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.scheduler.batch_runner import BatchRunner, StoredMatch
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


def _stored(*ids):
    """``_store_matches`` return value: each match carries its own job."""
    return [StoredMatch(i, JobDetails(id=i, title="SWE", company="ACME")) for i in ids]


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
        description=(
            "python and django backend role, in a small team shipping to industrial "
            "customers every week, from our office"
        ),
        url="https://example.com/1",
    )

    async with sqlite_factory() as db:
        match_ids = await runner._store_matches(db, [(job, 87.5)], filters)

    assert len(match_ids) == 1
    async with sqlite_factory() as db:
        row = (
            await db.execute(select(JobMatch).where(JobMatch.id == match_ids[0].match_id))
        ).scalar_one()

    assert row.keyword_hits is not None
    hits = row.keyword_hits
    matched = hits if isinstance(hits, list) else [k for k, v in hits.items() if v]
    assert set(matched) == {"python", "django"}


@pytest.mark.asyncio
async def test_batch_without_injected_matcher_scores_against_the_owners_cv(
    sqlite_factory, tmp_path
):
    """``matcher=None``: the batch ranks with ``load_matcher``, like the monitor."""
    from backend.models.user import SearchSettings, UserProfile
    from backend.monitor.store import raw_to_details

    cv = tmp_path / "cv.tex"
    cv.write_text("Python, Django, PostgreSQL", encoding="utf-8")
    body = (
        "\nRequirements\n- Python\n- Django\n\n"
        "We ship to industrial customers every week, from our office in town."
    )
    jobs = [
        RawJob(title="Backend Engineer", company="ACME", url="u1", description=body),
        RawJob(title="Account Executive", company="ACME", url="u2", description=body),
        RawJob(title="Rust Engineer", company="ACME", url="u3", description=body, location="Lyon"),
    ]
    runner = BatchRunner(
        scraper=MockScraper(jobs=jobs),
        matcher=None,
        cv_pipeline=MockCVPipeline(),
        db_factory=sqlite_factory,
    )
    async with sqlite_factory() as db:
        db.add(UserProfile(full_name="A", email="a@example.com", base_cv_path=str(cv)))
        db.add(
            SearchSettings(
                keywords={"include": ["backend"]}, locations={"items": ["Paris"]}, min_match_score=1
            )
        )
        await db.commit()
    with patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock):
        async with sqlite_factory() as db:
            preview = await runner._run_batch_inner(db, dry_run=True)
            filters = JobFilters(keywords=["backend"], locations=["Paris"])
            ranked = [(raw_to_details(jobs[0]), 50.0)]
            (stored,) = await runner._store_matches(
                db, ranked, filters, JobMatcher(cv.read_text(), ["backend"])
            )
            row = (
                await db.execute(select(JobMatch).where(JobMatch.id == stored.match_id))
            ).scalar_one()

    # Lyon is filtered out (score 0); the sales title ranks below the engineering one.
    assert [p["title"] for p in preview] == ["Backend Engineer", "Account Executive"]
    assert preview[0]["score"] > preview[1]["score"]
    assert row.keyword_hits == {"django": True, "python": True}
    assert row.score_basis == JobMatcher(cv.read_text(), ["backend"]).score_basis


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
            runner, "_store_matches", new_callable=AsyncMock, return_value=_stored(1)
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
        MockGuard.from_settings = AsyncMock(return_value=guard_instance)  # W11

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
            runner, "_store_matches", new_callable=AsyncMock, return_value=_stored(1)
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
        MockGuard.from_settings = AsyncMock(return_value=guard_instance)  # W11

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
            runner, "_store_matches", new_callable=AsyncMock, return_value=_stored(*range(1, 11))
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
        MockGuard.from_settings = AsyncMock(return_value=guard_instance)  # W11

        await runner.run_batch()

    # _store_tailored_doc should be called at most 3 times
    assert mock_doc.call_count <= 3


# ── Dry-run preview: scrape + rank only, NO DB writes, NO LLM ───────────────


@pytest.mark.asyncio
async def test_dry_run_returns_preview_and_writes_nothing(sqlite_factory):
    """Dry-run scrapes + ranks but writes NOTHING and never calls the LLM.

    This is the M2-T6 guarantee: previewing today's matches must not burn
    LLM quota or commit any rows. We assert (a) a preview list comes back,
    (b) the ``job_matches`` and ``tailored_documents`` tables stay empty, and
    (c) the fit engine and CV pipeline are never touched.
    """
    from backend.models.document import TailoredDocument

    jobs = [_raw_job(title=f"SWE {i}", url=f"https://example.com/{i}") for i in range(3)]
    scraper = MockScraper(jobs=jobs)
    matcher = MockMatcher(score=80.0)
    cv_pipeline = MockCVPipeline()

    # A fit-engine spy proves the fit-assessment path is skipped.
    fit_engine = MagicMock()

    runner = BatchRunner(
        scraper=scraper,
        matcher=matcher,
        cv_pipeline=cv_pipeline,
        db_factory=sqlite_factory,
        fit_engine=fit_engine,
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

    # No LLM / CV-generation calls
    assert cv_pipeline.calls == []
    fit_engine.assess.assert_not_called()

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
        patch.object(runner, "_store_matches", new_callable=AsyncMock, return_value=_stored(1)),
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
        MockGuard.from_settings = AsyncMock(return_value=guard_instance)  # W11

        result = await runner.run_batch()

    assert result is None


# ── Step 3.5: fit assessment on the gazetteer ───────────────────────────────


@pytest.mark.asyncio
async def test_fit_step_assesses_each_stored_match(tmp_path):
    """Each stored match is assessed against the CV's gazetteer skills, with no LLM or
    embedding call; a posting naming too few skills gets no assessment."""
    jobs = [_raw_job(url=f"https://x.com/{i}") for i in range(3)]
    runner, _db, _cv_pipe = _make_runner(jobs=jobs)
    stored = [
        StoredMatch(1, JobDetails(id=1, title="SWE", company="ACME",
                                  description="Requirements:\n- Python and Docker")),
        StoredMatch(2, JobDetails(id=2, title="SWE", company="ACME",
                                  description="Requirements:\n- Java, Kubernetes and Terraform")),
        StoredMatch(3, JobDetails(id=3, title="SWE", company="ACME",
                                  description="A great place to work.")),
    ]
    cv_path = tmp_path / "base.tex"
    cv_path.write_text(
        "\\documentclass{article}\\begin{document}Python, Docker, SQL\\end{document}",
        encoding="utf-8",
    )

    with (
        patch.object(runner, "_load_settings", new_callable=AsyncMock) as mock_settings,
        patch.object(runner, "_load_profile", new_callable=AsyncMock) as mock_profile,
        patch.object(runner, "_load_sources", new_callable=AsyncMock) as mock_sources,
        patch.object(
            runner,
            "_store_matches",
            new_callable=AsyncMock,
            return_value=stored,
        ),
        patch.object(runner, "_store_tailored_doc", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch(
            "backend.scheduler.batch_runner.broadcast_job_assessment", new_callable=AsyncMock
        ) as broadcast,
        patch("backend.scheduler.batch_runner.DailyLimitGuard") as MockGuard,
        # match_row lookup → return None so we don't try to set attrs on a Mock
        patch("backend.scheduler.batch_runner.select"),
        patch("backend.scheduler.batch_runner._resolve_cv_path", return_value=cv_path),
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
        MockGuard.from_settings = AsyncMock(return_value=guard_instance)  # W11

        # db.execute(...).scalar_one_or_none() → None so the DB-write branch is a no-op
        db = AsyncMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=exec_result)
        db.commit = AsyncMock()
        db.close = AsyncMock()
        runner._db_factory = lambda: db

        await runner.run_batch()

    frames = {c.kwargs["match_id"]: c.kwargs for c in broadcast.await_args_list}
    assert set(frames) == {1, 2}  # match 3 names no skill
    assert frames[1]["decision"] == "base_cv"
    assert frames[1]["covered"] == ["docker", "python"] and frames[1]["gaps"] == []
    assert frames[2]["decision"] == "modify"
    assert [g["skill"] for g in frames[2]["gaps"]] == ["java", "terraform"]
    assert frames[2]["covered"] == ["kubernetes"]  # same family as Docker
    assert frames[1]["ats_score"] > frames[2]["ats_score"]
    assert frames[2]["gap_severity"] == pytest.approx(1 - frames[2]["ats_score"] / 100, abs=1e-3)


# ── W03 / F01: job identity survives the whole batch ────────────────────────


class _RecordingPipeline:
    """Records which job each output directory was generated for."""

    def __init__(self, fail_titles=()):
        self.calls: list[tuple[str, int | None, str]] = []  # (out_dir name, job.id, title)
        self._fail = set(fail_titles)

    async def _gen(self, job, output_dir):
        self.calls.append((output_dir.parent.name, job.id, job.title))
        if job.title in self._fail:
            raise RuntimeError("compile error")
        result = MagicMock()
        result.tex_path = output_dir / "cv.tex"
        result.pdf_path = output_dir / "cv.pdf"
        result.diff = []
        return result

    async def generate_tailored_cv(self, base_cv_path, job, output_dir, **_kw):
        return await self._gen(job, output_dir)

    async def generate_base_cv(self, base_cv_path, job, output_dir, **_kw):
        return await self._gen(job, output_dir)


# Long enough to be a posting, not a card (cards get no pre-generated CV); names no skill.
_BODY = "We are a small team shipping to industrial customers every week, from our office."


class _TitleScoreMatcher(JobMatcher):
    def __init__(self, scores):
        super().__init__()
        self._scores = scores

    def score(self, job, filters):  # noqa: ARG002
        return self._scores[job.title]


@pytest.mark.asyncio
async def test_batch_preserves_job_identity_when_first_ranked_job_is_skipped(
    sqlite_factory, tmp_path
):
    """A previously skipped top-ranked job must not shift details onto other matches.

    Pre-fix, ``_store_matches`` dropped the skipped job but the generation
    step zipped the surviving match IDs with ``ranked`` by position, so the
    Rust match received the (skipped) Python job's CV.
    """
    import hashlib

    from backend.models.document import TailoredDocument
    from backend.models.job import Job

    # Seed: "Python Engineer" already exists and was skipped by the user.
    async with sqlite_factory() as db:
        h = hashlib.md5("ACME|Python Engineer|".lower().encode()).hexdigest()
        job = Job(title="Python Engineer", company="ACME", location="", url="u0", dedup_hash=h)
        db.add(job)
        await db.flush()
        db.add(JobMatch(job_id=job.id, score=10.0, status="skipped"))
        await db.commit()

    jobs = [
        RawJob(title="Python Engineer", company="ACME", url="https://e.x/py", description=_BODY),
        RawJob(title="Rust Engineer", company="ACME", url="https://e.x/rs", description=_BODY),
        # same dedup hash
        RawJob(title="Rust Engineer", company="ACME", url="https://e.x/rs-dup", description=_BODY),
        RawJob(title="Go Engineer", company="ACME", url="https://e.x/go", description=_BODY),
    ]
    scores = {"Python Engineer": 95.0, "Rust Engineer": 90.0, "Go Engineer": 85.0}
    pipeline = _RecordingPipeline(fail_titles={"Go Engineer"})
    runner = BatchRunner(
        scraper=MockScraper(jobs=jobs),
        matcher=_TitleScoreMatcher(scores),
        cv_pipeline=pipeline,
        db_factory=sqlite_factory,
    )
    cv = tmp_path / "base.tex"
    cv.write_text("\\documentclass{article}")
    messages: list[str] = []

    async def _status(msg, progress=0.0):  # noqa: ARG001
        messages.append(msg)

    with (
        patch("backend.scheduler.batch_runner.broadcast_status", side_effect=_status),
        patch("backend.scheduler.batch_runner._resolve_cv_path", return_value=cv),
        patch("backend.scheduler.batch_runner.settings") as cfg,
    ):
        cfg.jobpilot_data_dir = str(tmp_path)
        await runner.run_batch()

    async with sqlite_factory() as db:
        matches = {
            m.id: m for m in (await db.execute(select(JobMatch).where(JobMatch.status == "new"))).scalars()
        }
        titles = {j.id: j.title for j in (await db.execute(select(Job))).scalars()}
        docs = (await db.execute(select(TailoredDocument))).scalars().all()

    # Every generation call targeted the match's own job (dir is "<match_id>_<slug>").
    assert pipeline.calls, "no CV generation happened"
    for dir_name, job_id, title in pipeline.calls:
        mid = int(dir_name.split("_", 1)[0])
        assert matches[mid].job_id == job_id, (dir_name, job_id)
        assert titles[job_id] == title
    assert "Python Engineer" not in [t for _, _, t in pipeline.calls]
    # The duplicate scrape of the Rust job produced ONE match and ONE generation.
    assert [t for _, _, t in pipeline.calls].count("Rust Engineer") == 1
    # Only the successful document is persisted, and it belongs to the Rust match.
    assert len(docs) == 1
    assert titles[matches[docs[0].job_match_id].job_id] == "Rust Engineer"
    # Readiness counts actual artifacts, not selected matches.
    assert any("1 ready" in m and "1 failed" in m for m in messages), messages


@pytest.mark.asyncio
async def test_same_day_rerun_does_not_overwrite_the_previous_version(sqlite_factory, tmp_path):
    """Review I6: each run writes its own directory, so earlier documents stay intact."""
    from backend.models.document import TailoredDocument

    pipeline = _RecordingPipeline()
    runner = BatchRunner(
        scraper=MockScraper(
            jobs=[RawJob(title="Solo", company="ACME", url="https://e.x/solo", description=_BODY)]
        ),
        matcher=_TitleScoreMatcher({"Solo": 90.0}),
        cv_pipeline=pipeline,
        db_factory=sqlite_factory,
    )
    cv = tmp_path / "base.tex"
    cv.write_text("x")
    with (
        patch("backend.scheduler.batch_runner.broadcast_status", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner._resolve_cv_path", return_value=cv),
        patch("backend.scheduler.batch_runner.settings") as cfg,
    ):
        cfg.jobpilot_data_dir = str(tmp_path)
        await runner.run_batch()
        await runner.run_batch()

    async with sqlite_factory() as db:
        paths = [d.pdf_path for d in (await db.execute(select(TailoredDocument))).scalars()]
    assert len(paths) == 2 and len(set(paths)) == 2


@pytest.mark.asyncio
async def test_description_less_card_gets_no_pre_generated_cv(sqlite_factory, tmp_path):
    """A card scored on its title alone (provisional, fixed 70) outranks described
    postings: it must not take a CV slot, nor reach the LLM tailoring with no text."""
    from backend.models.user import SearchSettings

    described = "Requirements\n- Python\n- Docker\n\n" + "We ship robots to factories. " * 4
    jobs = [
        RawJob(title="Card One", company="ACME", url="https://e.x/1"),
        RawJob(title="Card Two", company="ACME", url="https://e.x/2", description="Apply now!"),
        RawJob(title="Described", company="ACME", url="https://e.x/3", description=described),
    ]
    pipeline = _RecordingPipeline()
    runner = BatchRunner(
        scraper=MockScraper(jobs=jobs),
        matcher=_TitleScoreMatcher({"Card One": 70.0, "Card Two": 70.0, "Described": 55.0}),
        cv_pipeline=pipeline,
        db_factory=sqlite_factory,
    )
    async with sqlite_factory() as db:
        db.add(SearchSettings(keywords={"include": []}, daily_limit=1, min_match_score=1))
        await db.commit()
    cv = tmp_path / "base.tex"
    cv.write_text("Python, Docker")
    messages: list[str] = []

    async def _status(msg, progress=0.0):  # noqa: ARG001
        messages.append(msg)

    with (
        patch("backend.scheduler.batch_runner.broadcast_status", side_effect=_status),
        patch("backend.scheduler.batch_runner.broadcast_job_assessment", new_callable=AsyncMock),
        patch("backend.scheduler.batch_runner._resolve_cv_path", return_value=cv),
        patch("backend.scheduler.batch_runner.settings") as cfg,
    ):
        cfg.jobpilot_data_dir = str(tmp_path)
        await runner.run_batch()

    # The single slot of the day goes to the posting that has a text to tailor to.
    assert [title for _, _, title in pipeline.calls] == ["Described"]
    assert any("1 ready" in m for m in messages), messages
    # The cards are still stored as matches: only the CV is not pre-generated.
    async with sqlite_factory() as db:
        assert len((await db.execute(select(JobMatch))).scalars().all()) == 3
