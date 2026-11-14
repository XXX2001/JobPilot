"""rescore_all: stored scores follow the CV, the keywords and the gazetteer (Review Focus 5)."""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal
from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.job import Job, JobMatch
from backend.monitor.poller import rescore_stale
from backend.monitor.store import raw_to_details, rescore_all

POSTING = """About the role
You will build the motion stack of our mobile manipulators.

Requirements
- Python and C++
- ROS 2

Nice to have
- Docker
- Kubernetes
"""


async def _match(db, n: int, *, status: str = "new", basis: str | None = None) -> JobMatch:
    job = Job(
        title="Robotics Engineer",
        company=f"ACME {n}",
        location="Paris",
        description=POSTING,
        url="",
        dedup_hash=f"h{n}",
    )
    db.add(job)
    await db.flush()
    match = JobMatch(
        job_id=job.id, score=1.0, keyword_hits={"old": True}, status=status, score_basis=basis
    )
    db.add(match)
    return match


async def test_rescore_updates_only_stale_new_matches():
    filters = JobFilters(keywords=["robotics"])
    matcher = JobMatcher(cv_text="Python, ROS 2, Docker")
    async with AsyncSessionLocal() as db:
        stale = await _match(db, 1)
        current = await _match(db, 2, basis=matcher.score_basis)
        actioned = await _match(db, 3, status="skipped")
        await db.flush()

        assert await rescore_all(db, matcher, filters) == 1
        await db.commit()
        for match in (stale, current, actioned):
            await db.refresh(match)

        job = (await db.execute(select(Job).where(Job.id == stale.job_id))).scalar_one()
        expected = matcher.explain(raw_to_details(job), filters)
        assert stale.score == expected.score > 1.0
        # covered, then by family (Kubernetes through Docker, the title through ROS 2)
        assert stale.keyword_hits == dict.fromkeys(
            ["docker", "python", "ros 2", "kubernetes", "robotics"], True
        )
        assert stale.score_basis == matcher.score_basis
        # already on this basis, or no longer "new": untouched
        assert (current.score, current.keyword_hits) == (1.0, {"old": True})
        assert (actioned.score, actioned.keyword_hits, actioned.score_basis) == (
            1.0,
            {"old": True},
            None,
        )
        assert await rescore_all(db, matcher, filters) == 0

        # the CV changes: every "new" match is stale again
        richer = JobMatcher(cv_text="Python, C++, ROS 2, Docker, Kubernetes")
        assert await rescore_all(db, richer, filters) == 2
        await db.commit()
        for match in (stale, current, actioned):
            await db.refresh(match)
        assert stale.score == current.score > expected.score
        assert current.score_basis == richer.score_basis
        assert actioned.score == 1.0


async def test_rescore_keeps_a_now_filtered_match_at_zero():
    """A match the filters now reject is not deleted: it drops to 0 with nothing covered."""
    matcher = JobMatcher(cv_text="Python")
    async with AsyncSessionLocal() as db:
        match = await _match(db, 1)
        await db.flush()
        assert await rescore_all(db, matcher, JobFilters(locations=["Lyon"])) == 1
        assert (match.score, match.keyword_hits, match.status) == (0.0, {}, "new")


async def test_rescore_1000_matches_under_5s():
    matcher = JobMatcher(cv_text="Python, C++, ROS 2, Docker")
    filters = JobFilters(keywords=["robotics"], locations=["Paris"])
    async with AsyncSessionLocal() as db:
        for n in range(1000):
            await _match(db, n)
        await db.commit()
        start = time.perf_counter()
        assert await rescore_all(db, matcher, filters) == 1000
        await db.commit()
        assert time.perf_counter() - start < 5.0


async def test_force_rescores_matches_already_on_this_basis():
    """The filters are not in the basis: a settings save rescores whatever the basis."""
    matcher = JobMatcher(cv_text="Python")
    async with AsyncSessionLocal() as db:
        match = await _match(db, 1, basis=matcher.score_basis)
        actioned = await _match(db, 2, status="skipped", basis=matcher.score_basis)
        await db.flush()
        lyon = JobFilters(locations=["Lyon"])
        assert await rescore_all(db, matcher, lyon) == 0
        assert await rescore_all(db, matcher, lyon, force=True) == 1
        assert (match.score, match.keyword_hits) == (0.0, {})
        assert actioned.score == 1.0


# --- the callers: startup, CV upload, settings save ---------------------------------


def _seed(n: int = 1, basis: str | None = None) -> int:
    async def seed() -> int:
        async with AsyncSessionLocal() as db:
            match = await _match(db, n, basis=basis)
            await db.commit()
            return match.id

    return asyncio.run(seed())


def _stored(match_id: int) -> JobMatch:
    async def load() -> JobMatch:
        async with AsyncSessionLocal() as db:
            return (await db.execute(select(JobMatch).where(JobMatch.id == match_id))).scalar_one()

    return asyncio.run(load())


def _upload_cv(client: TestClient, text: str) -> None:
    resp = client.post(
        "/api/settings/profile/cv-upload",
        files={"file": ("rescore_cv.tex", text.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


def test_uploading_a_cv_rescores_stored_new_matches(test_app: TestClient):
    match_id = _seed()
    _upload_cv(test_app, "Python")
    first = _stored(match_id)
    assert first.keyword_hits == {"python": True}
    assert first.score_basis == JobMatcher(cv_text="Python").score_basis

    _upload_cv(test_app, "Python, C++, ROS 2, Docker")
    second = _stored(match_id)
    assert second.score > first.score > 1.0
    assert {"c++", "ros 2", "docker"} <= set(second.keyword_hits)


def test_saving_search_keywords_rescores_stored_new_matches(test_app: TestClient):
    _upload_cv(test_app, "Python, Docker")
    match_id = _seed()
    resp = test_app.put("/api/settings/search", json={"keywords": {"include": ["robotics"]}})
    assert resp.status_code == 200, resp.text
    match = _stored(match_id)
    assert match.score > 1.0
    # Kubernetes by family, through Docker
    assert match.keyword_hits == {"docker": True, "python": True, "kubernetes": True}
    assert match.score_basis == JobMatcher("Python, Docker", ["robotics"]).score_basis


def test_saving_a_location_filter_rescores_matches_on_the_current_basis(test_app: TestClient):
    _upload_cv(test_app, "Python, Docker")
    match_id = _seed(basis=JobMatcher("Python, Docker").score_basis)
    resp = test_app.put("/api/settings/search", json={"locations": {"items": ["Lyon"]}})
    assert resp.status_code == 200, resp.text
    assert _stored(match_id).score == 0.0  # the job is in Paris


def test_startup_rescores_matches_stored_by_an_older_scorer():
    """A database upgraded from the old scorer holds NULL-basis rows: startup recomputes them."""
    from backend.main import app

    match_id = _seed()
    with TestClient(app):
        pass
    match = _stored(match_id)
    assert match.score_basis is not None
    assert match.keyword_hits != {"old": True}


async def test_rescore_stale_uses_the_owners_cv_and_search_settings(tmp_path):
    from backend.models.user import SearchSettings, UserProfile

    cv = tmp_path / "cv.tex"
    cv.write_text("Python, ROS 2", encoding="utf-8")
    async with AsyncSessionLocal() as db:
        db.add(UserProfile(full_name="A", email="a@example.com", base_cv_path=str(cv)))
        db.add(SearchSettings(keywords={"include": ["robotics"]}, locations={"items": ["Lyon"]}))
        match = await _match(db, 1)
        await db.commit()
        assert await rescore_stale(db) == 1
        await db.refresh(match)
        assert match.score_basis == JobMatcher("Python, ROS 2", ["robotics"]).score_basis
        assert match.score == 0.0  # Paris, and the owner wants Lyon
        assert await rescore_stale(db) == 0
        assert await rescore_stale(db, force=True) == 1
