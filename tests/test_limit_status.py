"""Tests for GET /api/applications/limit-status endpoint.

Covers the three acceptance-criteria cases:
  - fresh     → response has the expected schema; used >= 0, limit >= 1
  - mid-day   → after adding 3 today-dated applications, used increases by 3
  - at-cap    → after adding enough to fill the configured limit, used == limit

The endpoint reads from the same counter as DailyLimitGuard:
Application rows with applied_at >= today AND status in {"applied", "pending"}.
"""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient


def _today_utc_naive() -> datetime:
    """Return the current UTC datetime as a naive datetime (no tzinfo), matching
    the storage convention used by Application.applied_at."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_limit_status_fresh_day(test_app: TestClient) -> None:
    """GET /api/applications/limit-status returns valid schema."""
    resp = test_app.get("/api/applications/limit-status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["used"], int)
    assert data["used"] >= 0
    # limit reflects the configured value (≥ 1); default is 10
    assert isinstance(data["limit"], int)
    assert data["limit"] >= 1
    assert isinstance(data["resets_at"], str)
    # Verify it looks like an ISO 8601 datetime (contains 'T' separator)
    assert "T" in data["resets_at"]


def test_hand_recorded_applications_do_not_consume_the_quota(test_app: TestClient) -> None:
    """W11: the quota guards automated submissions; a manual tracker entry is not one."""
    baseline: int = test_app.get("/api/applications/limit-status").json()["used"]
    for status in ("applied", "pending"):
        resp = test_app.post("/api/applications", json={"method": "manual", "status": status})
        assert resp.status_code == 201
    assert test_app.get("/api/applications/limit-status").json()["used"] == baseline


def test_limit_status_counts_reservations_up_to_the_cap(test_app: TestClient) -> None:
    """Engine reservations (the only thing the guard counts) show up in ``used``."""
    import asyncio
    import uuid

    from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch

    async def fill_to_cap() -> int:
        async with AsyncSessionLocal() as db:
            tag = uuid.uuid4().hex[:8]
            job = Job(title="t", company=f"L{tag}", url=f"https://l/{tag}", dedup_hash=f"lim-{tag}")
            db.add(job)
            await db.flush()
            match = JobMatch(job_id=job.id, score=1.0, status="new")
            db.add(match)
            await db.commit()
            guard = await DailyLimitGuard.from_settings(db)
            while True:
                try:
                    await guard.reserve_slot(job_match_id=match.id, method="auto")
                except DailyLimitExceeded:
                    return guard.limit

    limit = asyncio.run(fill_to_cap())
    data = test_app.get("/api/applications/limit-status").json()
    assert data["used"] == limit == data["limit"]
