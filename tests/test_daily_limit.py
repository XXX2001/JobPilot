"""Tests for the atomic daily-limit guard (TOCTOU fix — PC-04 / DB-06).

These tests exercise :class:`DailyLimitGuard.reserve_slot` against a
real in-memory SQLite to verify that concurrent reservations cannot
exceed the configured limit. The mocked-AsyncSession tests in
``test_apply_engine.py`` cover surface behaviour; this file is the
race-window regression guard.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import tempfile

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard
from backend.models import Base
from backend.models.application import Application


# ----------------------------------------------------------------------
# Per-test isolated SQLite engine
# ----------------------------------------------------------------------


@pytest.fixture
async def sqlite_factory():
    """Yield an async_sessionmaker backed by a fresh on-disk SQLite.

    On-disk (not ``:memory:``) so two concurrent connections see the
    same database — which is the whole point of the race test.
    """
    tmpdir = tempfile.mkdtemp(prefix="jobpilot-daily-limit-test-")
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


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_slot_increments_persisted_count(sqlite_factory):
    """A successful reserve_slot inserts a counted ``pending`` row."""
    Session = sqlite_factory

    async with Session() as session:
        guard = DailyLimitGuard(db=session, limit=10)
        before = await guard.remaining_today()
        assert before == 10

        app_id = await guard.reserve_slot(job_match_id=42, method="auto")
        assert app_id is not None

        after = await guard.remaining_today()
        assert after == 9, "reservation must consume one slot"


@pytest.mark.asyncio
async def test_reserve_slot_raises_when_at_limit(sqlite_factory):
    """When today's count already equals limit, reserve_slot raises."""
    Session = sqlite_factory

    # Pre-populate today's table at limit.
    async with Session() as session:
        for i in range(10):
            session.add(
                Application(
                    job_match_id=i,
                    method="auto",
                    status="applied",
                    applied_at=datetime.utcnow(),
                )
            )
        await session.commit()

    async with Session() as session:
        guard = DailyLimitGuard(db=session, limit=10)
        with pytest.raises(DailyLimitExceeded):
            await guard.reserve_slot(job_match_id=999, method="auto")

    # Verify no extra row leaked (rollback worked).
    async with Session() as session:
        from sqlalchemy import func, select

        total = (
            await session.execute(select(func.count(Application.id)))
        ).scalar_one_or_none()
        assert total == 10, "rolled-back reservation must not persist"


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_limit(sqlite_factory):
    """Two concurrent reserve_slot calls at limit-1 must yield exactly one success.

    This is the TOCTOU regression guard. With the old non-atomic
    ``can_apply`` + later-insert pattern, both calls would see
    ``count = limit - 1`` and both insert — landing at ``limit + 1``.
    With the atomic ``reserve_slot``, one wins and the other must
    raise :class:`DailyLimitExceeded`.
    """
    Session = sqlite_factory
    limit = 5

    # Pre-populate today with (limit - 1) rows. One slot left.
    async with Session() as session:
        for i in range(limit - 1):
            session.add(
                Application(
                    job_match_id=i,
                    method="auto",
                    status="applied",
                    applied_at=datetime.utcnow(),
                )
            )
        await session.commit()

    async def attempt(idx: int):
        # Each task uses its own session (concurrent connections).
        async with Session() as session:
            guard = DailyLimitGuard(db=session, limit=limit)
            try:
                return ("ok", await guard.reserve_slot(job_match_id=100 + idx, method="auto"))
            except DailyLimitExceeded as exc:
                return ("denied", str(exc))

    # Fire two reservations concurrently. SQLite serialises writers,
    # so one will commit first and the other's post-insert count will
    # be limit+1 → rollback + DailyLimitExceeded.
    results = await asyncio.gather(attempt(0), attempt(1))

    statuses = sorted(r[0] for r in results)
    assert statuses == ["denied", "ok"], (
        f"exactly one reservation must succeed, got {results}"
    )

    # Verify on-disk count is exactly ``limit``.
    async with Session() as session:
        from sqlalchemy import func, select

        total = (
            await session.execute(select(func.count(Application.id)))
        ).scalar_one_or_none()
        assert total == limit, (
            f"daily limit ({limit}) must never be exceeded; got {total} rows"
        )
