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


def test_limit_status_mid_day(test_app: TestClient) -> None:
    """Adding 3 applied applications today increases used by exactly 3."""
    # Read baseline before inserting
    baseline_resp = test_app.get("/api/applications/limit-status")
    assert baseline_resp.status_code == 200
    baseline_used: int = baseline_resp.json()["used"]

    today_iso = _today_utc_naive().isoformat()
    for _ in range(3):
        resp = test_app.post(
            "/api/applications",
            json={"method": "manual", "status": "applied"},
        )
        assert resp.status_code == 201
        app_id = resp.json()["id"]
        # Set applied_at to today so the endpoint counts it
        patch = test_app.patch(
            f"/api/applications/{app_id}",
            json={"applied_at": today_iso},
        )
        assert patch.status_code == 200

    resp = test_app.get("/api/applications/limit-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["used"] == baseline_used + 3


def test_limit_status_at_cap(test_app: TestClient) -> None:
    """When used reaches the configured limit, used == limit."""
    # Read current usage and the configured limit
    baseline_resp = test_app.get("/api/applications/limit-status")
    assert baseline_resp.status_code == 200
    baseline_used: int = baseline_resp.json()["used"]
    limit: int = baseline_resp.json()["limit"]

    needed = max(0, limit - baseline_used)
    today_iso = _today_utc_naive().isoformat()
    for _ in range(needed):
        # ``method='manual'`` so each row satisfies the N2-T3 conditional CHECK
        # without a match (the daily-limit counter keys off ``status``, not
        # ``method``, so this still fills the cap as intended).
        resp = test_app.post(
            "/api/applications",
            json={"method": "manual", "status": "pending"},
        )
        assert resp.status_code == 201
        app_id = resp.json()["id"]
        patch = test_app.patch(
            f"/api/applications/{app_id}",
            json={"applied_at": today_iso},
        )
        assert patch.status_code == 200

    resp = test_app.get("/api/applications/limit-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["used"] == limit
    assert data["limit"] == limit
