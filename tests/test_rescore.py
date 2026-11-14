"""rescore_all: stored scores follow the CV, the keywords and the gazetteer (Review Focus 5)."""

from __future__ import annotations

import time

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.job import Job, JobMatch
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
