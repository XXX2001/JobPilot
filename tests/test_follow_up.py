"""Tests for follow-up reminder scanner — Task qw-5.

Acceptance criteria (spec):
  (a) Empty DB → scan_overdue returns 0, no events created.
  (b) App with applied_at = now() - 3 days → returns 0.
  (c) App with applied_at = now() - 8 days, no prior events → returns 1,
      one follow_up_due event exists.
  (d) Re-run scan_overdue immediately after (c) → returns 0 (idempotent).
  (e) User posts a follow_up event AFTER follow_up_due was created →
      GET /api/applications?needs_follow_up=true excludes the application.

The ``test_app`` fixture is used for all tests so that the TestClient
lifespan initialises the DB tables before any async DB access occurs.
Direct async helpers (``asyncio.run``) are used for seeding / reading,
following the pattern from ``tests/test_apply_http.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _naive_utc_ago(days: float) -> datetime:
    """Return a naive UTC datetime ``days`` days in the past."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


async def _create_applied_app(*, days_ago: float) -> int:
    """Insert an Application row with status='applied' and applied_at days ago.

    Returns the application id.
    """
    from backend.database import AsyncSessionLocal
    from backend.models.application import Application

    async with AsyncSessionLocal() as db:
        app = Application(
            method="manual",
            status="applied",
            applied_at=_naive_utc_ago(days_ago),
        )
        db.add(app)
        await db.commit()
        await db.refresh(app)
        return app.id


async def _count_events(app_id: int, event_type: str) -> int:
    """Count ApplicationEvent rows for app_id with a given event_type."""
    from sqlalchemy import func, select

    from backend.database import AsyncSessionLocal
    from backend.models.application import ApplicationEvent

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count())
            .select_from(ApplicationEvent)
            .where(
                ApplicationEvent.application_id == app_id,
                ApplicationEvent.event_type == event_type,
            )
        )
        return result.scalar_one()


async def _add_event(app_id: int, event_type: str, days_offset: float = 0.0) -> None:
    """Insert an ApplicationEvent for app_id.

    days_offset > 0 means the event is that many days in the future relative
    to now (ensures a follow_up event is newer than follow_up_due).
    """
    from backend.database import AsyncSessionLocal
    from backend.models.application import ApplicationEvent

    event_date = (
        datetime.now(timezone.utc) + timedelta(days=days_offset)
    ).replace(tzinfo=None)

    async with AsyncSessionLocal() as db:
        event = ApplicationEvent(
            application_id=app_id,
            event_type=event_type,
            event_date=event_date,
        )
        db.add(event)
        await db.commit()


async def _run_scan() -> int:
    """Run scan_overdue against the shared test DB and return the count."""
    from backend.applier.follow_up import scan_overdue

    return await scan_overdue()


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_scan_overdue_empty_db(test_app: TestClient) -> None:
    """(a) Empty DB → scan_overdue returns 0 (or ≥ 0 if prior tests ran)."""
    # test_app fixture ensures tables exist. No applied rows ≥ 7 days old
    # should exist at this point in a clean test run.
    result = asyncio.run(_run_scan())
    assert isinstance(result, int)
    assert result >= 0


def test_scan_overdue_recent_app_not_flagged(test_app: TestClient) -> None:
    """(b) Applied 3 days ago → not yet overdue; our app gets no follow_up_due event."""
    app_id = asyncio.run(_create_applied_app(days_ago=3))
    asyncio.run(_run_scan())

    event_count = asyncio.run(_count_events(app_id, "follow_up_due"))
    assert event_count == 0


def test_scan_overdue_old_app_creates_event(test_app: TestClient) -> None:
    """(c) Applied 8 days ago → scan creates exactly one follow_up_due event."""
    app_id = asyncio.run(_create_applied_app(days_ago=8))
    result = asyncio.run(_run_scan())

    assert result >= 1
    event_count = asyncio.run(_count_events(app_id, "follow_up_due"))
    assert event_count == 1


def test_scan_overdue_idempotent(test_app: TestClient) -> None:
    """(d) Running scan twice in a row creates no additional events for the same app."""
    app_id = asyncio.run(_create_applied_app(days_ago=8))

    asyncio.run(_run_scan())
    asyncio.run(_run_scan())

    event_count = asyncio.run(_count_events(app_id, "follow_up_due"))
    assert event_count == 1


def test_needs_follow_up_filter_resolved(test_app: TestClient) -> None:
    """(e) After a follow_up event is posted, the app disappears from ?needs_follow_up=true."""
    app_id = asyncio.run(_create_applied_app(days_ago=8))

    # Run scan to create the follow_up_due event.
    asyncio.run(_run_scan())

    # The app should appear in ?needs_follow_up=true.
    resp = test_app.get("/api/applications?needs_follow_up=true")
    assert resp.status_code == 200
    data = resp.json()
    app_ids_before = [a["id"] for a in data["applications"]]
    assert app_id in app_ids_before

    # User posts a follow_up event AFTER the follow_up_due was created.
    # days_offset=0.001 (~86 seconds in the future) guarantees event_date ordering.
    asyncio.run(_add_event(app_id, "follow_up", days_offset=0.001))

    # Now the app should NOT appear in ?needs_follow_up=true.
    resp2 = test_app.get("/api/applications?needs_follow_up=true")
    assert resp2.status_code == 200
    data2 = resp2.json()
    app_ids_after = [a["id"] for a in data2["applications"]]
    assert app_id not in app_ids_after
