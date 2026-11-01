"""W11 / F13, F15 — one quota policy: same limit, same count, same day boundary."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard, quota_day_start
from backend.models import Base
from backend.models.application import Application
from backend.models.user import SearchSettings
from backend.utils.time import naive_utc_now


@pytest.fixture
def factory():
    path = Path(tempfile.mkdtemp(prefix="jobpilot-quota-")) / "q.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 15})

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init())
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        from backend.models.job import Job, JobMatch

        async with sm() as db:
            db.add(Job(id=1, title="t", company="c", url="u", dedup_hash="h"))
            await db.flush()
            db.add(JobMatch(id=1, job_id=1, score=1.0, status="new"))
            await db.commit()

    asyncio.run(seed())
    yield sm
    asyncio.run(engine.dispose())


async def _set_limit(factory, n: int) -> None:
    async with factory() as db:
        db.add(
            SearchSettings(id=1, keywords=[], daily_limit=n, min_match_score=0, remote_only=False)
        )
        await db.commit()


async def _row(
    factory, *, status: str, reserved_delta: timedelta | None = timedelta(0), method="auto"
) -> None:
    async with factory() as db:
        db.add(
            Application(
                job_match_id=1,
                method=method,
                status=status,
                quota_reserved_at=None
                if reserved_delta is None
                else naive_utc_now() + reserved_delta,
            )
        )
        await db.commit()


def test_guard_uses_the_configured_limit(factory):
    async def go():
        await _set_limit(factory, 1)
        await _row(factory, status="applied")
        async with factory() as db:
            guard = await DailyLimitGuard.from_settings(db)
            assert guard.limit == 1
            with pytest.raises(DailyLimitExceeded):
                await guard.reserve_slot(job_match_id=1, method="auto")

    asyncio.run(go())


def test_raising_the_limit_allows_more(factory):
    async def go():
        await _set_limit(factory, 3)
        await _row(factory, status="applied")
        async with factory() as db:
            guard = await DailyLimitGuard.from_settings(db)
            assert await guard.remaining_today() == 2

    asyncio.run(go())


def test_unresolved_possible_submission_counts_and_prepared_work_does_not(factory):
    async def go():
        await _set_limit(factory, 10)
        await _row(factory, status="needs_verification")
        await _row(factory, status="awaiting_submission", reserved_delta=None)  # released
        await _row(factory, status="cancelled", reserved_delta=None)
        await _row(factory, status="applied", method="manual", reserved_delta=None)  # user-made
        async with factory() as db:
            assert await (await DailyLimitGuard.from_settings(db)).used_today() == 1

    asyncio.run(go())


def test_day_boundary_is_utc_midnight(factory):
    async def go():
        await _set_limit(factory, 10)
        start = quota_day_start()
        async with factory() as db:
            db.add(
                Application(
                    job_match_id=1, method="auto", status="applied", quota_reserved_at=start - timedelta(seconds=1)
                )
            )
            db.add(
                Application(
                    job_match_id=1, method="auto", status="applied", quota_reserved_at=start + timedelta(seconds=1)
                )
            )
            await db.commit()
        async with factory() as db:
            assert await (await DailyLimitGuard.from_settings(db)).used_today() == 1

    asyncio.run(go())


def test_two_connections_cannot_both_take_the_last_slot(factory):
    async def go():
        await _set_limit(factory, 1)

        async def attempt():
            async with factory() as db:
                guard = await DailyLimitGuard.from_settings(db)
                try:
                    return await guard.reserve_slot(job_match_id=1, method="auto")
                except DailyLimitExceeded:
                    return None

        results = await asyncio.gather(attempt(), attempt())
        assert sum(r is not None for r in results) == 1
        async with factory() as db:
            rows = (await db.execute(select(Application))).scalars().all()
        assert [r.status for r in rows] == ["pending"]

    asyncio.run(go())


def test_recorder_releases_prepared_and_keeps_possible_submissions():
    """The reservation is released only when non-submission is established."""
    from backend.applier.recorder import ApplicationRecorder
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch

    async def go(status: str):
        tag = uuid.uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            job = Job(title="E", company=f"Q{tag}", url=f"https://q/{tag}", dedup_hash=f"q-{tag}")
            db.add(job)
            await db.flush()
            m = JobMatch(job_id=job.id, score=1.0, status="new")
            db.add(m)
            await db.flush()
            app = Application(
                job_match_id=m.id,
                method="auto",
                status="pending",
                quota_reserved_at=naive_utc_now(),
            )
            db.add(app)
            await db.commit()
            await ApplicationRecorder().record(
                db=db,
                job_match_id=m.id,
                result_status=status,
                result_method="auto",
                result_message=None,
                reserved_app_id=app.id,
            )
        async with AsyncSessionLocal() as db:
            return (await db.get(Application, app.id)).quota_reserved_at

    assert asyncio.run(go("awaiting_user_submission")) is None
    assert asyncio.run(go("needs_verification")) is not None
    assert asyncio.run(go("confirmed")) is not None


def test_limit_status_endpoint_agrees_with_the_guard(test_app):
    from backend.database import AsyncSessionLocal

    async def go():
        async with AsyncSessionLocal() as db:
            guard = await DailyLimitGuard.from_settings(db)
            return guard.limit, await guard.used_today()

    limit, used = asyncio.run(go())
    body = test_app.get("/api/applications/limit-status").json()
    assert (body["limit"], body["used"]) == (limit, used)
    assert body["resets_at"].startswith((quota_day_start() + timedelta(days=1)).date().isoformat())
