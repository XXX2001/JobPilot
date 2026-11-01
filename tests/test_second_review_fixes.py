"""Fixes from the second review (docs/reviews/2026-11-01-second-review.md)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard, quota_day_start
from backend.models.application import Application
from backend.utils.time import naive_utc_now
from tests.test_quota_policy import factory  # noqa: F401 — fixture
from tests.test_review_fixes import _seed_match

# ── R03: recruiting progress must not erase submission protection ────────────


@pytest.mark.parametrize("app_status", ["interview", "offer", "rejected"])
def test_apply_refuses_replay_after_recruiting_progress(test_app, app_status):
    from backend.applier.engine import ApplicationEngine

    mid, *_ = asyncio.run(_seed_match(status="applied", app_status=app_status))
    engine = MagicMock(spec=ApplicationEngine)
    engine.apply = AsyncMock()
    test_app.app.state.apply_engine = engine

    resp = test_app.post(f"/api/applications/{mid}/apply", json={"method": "manual"})

    assert resp.status_code == 409, resp.text
    engine.apply.assert_not_awaited()


@pytest.mark.parametrize("app_status", ["interview", "offer", "rejected"])
def test_recruiting_progress_keeps_todays_quota_slot(factory, app_status):  # noqa: F811
    async def go():
        async with factory() as db:
            db.add(
                Application(
                    job_match_id=1, method="auto", status=app_status,
                    quota_reserved_at=naive_utc_now(),
                )
            )
            await db.commit()
        async with factory() as db:
            return await DailyLimitGuard(db, limit=10).used_today()

    assert asyncio.run(go()) == 1


async def _reservation(factory, reserved_at) -> int:  # noqa: F811
    async with factory() as db:
        app = Application(
            job_match_id=1, method="auto", status="pending", quota_reserved_at=reserved_at
        )
        db.add(app)
        await db.commit()
        return app.id


def test_a_review_that_crossed_midnight_takes_a_slot_of_the_new_day(factory):  # noqa: F811
    async def go():
        old = await _reservation(factory, quota_day_start() - timedelta(minutes=5))
        async with factory() as db:
            await DailyLimitGuard(db, limit=1).renew_for_today(old)
        async with factory() as db:
            return (await db.get(Application, old)).quota_reserved_at

    assert asyncio.run(go()) >= quota_day_start()


def test_a_review_that_crossed_midnight_cannot_exceed_the_new_days_limit(factory):  # noqa: F811
    async def go():
        old = await _reservation(factory, quota_day_start() - timedelta(minutes=5))
        await _reservation(factory, naive_utc_now())  # today's only slot is taken
        async with factory() as db:
            with pytest.raises(DailyLimitExceeded):
                await DailyLimitGuard(db, limit=1).renew_for_today(old)
        async with factory() as db:
            return (await db.get(Application, old)).quota_reserved_at

    assert asyncio.run(go()) < quota_day_start()  # unchanged


def test_a_same_day_reservation_is_left_alone(factory):  # noqa: F811
    async def go():
        stamp = naive_utc_now()
        rid = await _reservation(factory, stamp)
        async with factory() as db:
            await DailyLimitGuard(db, limit=1).renew_for_today(rid)
        async with factory() as db:
            return stamp, (await db.get(Application, rid)).quota_reserved_at

    stamp, after = asyncio.run(go())
    assert after == stamp


def test_approval_is_refused_when_the_new_day_is_full(test_app):
    """The route checks the quota before the engine releases the submit."""
    from backend.applier.engine import ApplicationEngine
    from backend.database import AsyncSessionLocal

    mid, *_ = asyncio.run(_seed_match(status="applying"))

    async def seed():
        async with AsyncSessionLocal() as db:
            db.add(
                Application(
                    job_match_id=mid, method="auto", status="pending",
                    quota_reserved_at=quota_day_start() - timedelta(minutes=5),
                )
            )
            await db.commit()
            return (await db.execute(
                Application.__table__.select().where(Application.job_match_id == mid)
            )).first().id

    rid = asyncio.run(seed())
    engine = ApplicationEngine(daily_limit=0)  # nothing left today
    engine._confirm_events[mid], engine._cancel_events[mid] = asyncio.Event(), asyncio.Event()
    engine._reservations[mid] = rid
    snap = engine.record_pending_review(mid, filled_fields={"#a": "b"}, screenshot_b64=None)
    test_app.app.state.apply_engine = engine

    resp = test_app.post(
        f"/api/applications/{mid}/review-decision",
        json={
            "review_id": snap["review_id"], "revision": snap["revision"],
            "decision": "approve", "idempotency_key": "midnight-1",
        },
    )

    assert resp.status_code == 409 and "limit" in resp.json()["detail"].lower()
    assert not engine._confirm_events[mid].is_set()
    assert engine.get_pending_review(mid) is not None  # the review stays open
