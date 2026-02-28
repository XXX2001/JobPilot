"""Tests for /api/jobs and /api/queue routes (T14)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


# ─── Jobs ─────────────────────────────────────────────────────────────────────


def test_list_jobs_empty(test_app: TestClient):
    """GET /api/jobs returns 200 with empty list on fresh DB."""
    resp = test_app.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert isinstance(data["jobs"], list)
    assert "total" in data


def test_get_job_not_found(test_app: TestClient):
    """GET /api/jobs/999999 returns 404 for non-existent job."""
    resp = test_app.get("/api/jobs/999999")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_get_job_score_not_found(test_app: TestClient):
    """GET /api/jobs/999999/score returns 404 for non-existent job."""
    resp = test_app.get("/api/jobs/999999/score")
    assert resp.status_code == 404


def test_search_jobs_missing_keywords(test_app: TestClient):
    """POST /api/jobs/search with missing keywords returns 422 validation error."""
    resp = test_app.post("/api/jobs/search", json={})
    assert resp.status_code == 422


def test_search_jobs_schema_valid(test_app: TestClient):
    """POST /api/jobs/search with valid body is accepted (may fail Adzuna call with 502)."""
    resp = test_app.post(
        "/api/jobs/search",
        json={"keywords": ["python"], "country": "gb", "max_results": 5},
    )
    # Either succeeds (200) or fails at Adzuna (502) — but never 422 or 500
    assert resp.status_code in (200, 502)


# ─── Queue ────────────────────────────────────────────────────────────────────


def test_list_queue_empty(test_app: TestClient):
    """GET /api/queue returns 200 with empty list."""
    resp = test_app.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)


def test_queue_refresh(test_app: TestClient):
    """POST /api/queue/refresh returns 200 with a status message."""
    resp = test_app.post("/api/queue/refresh")
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_queue_skip_not_found(test_app: TestClient):
    """PATCH /api/queue/999999/skip returns 404 for non-existent match."""
    resp = test_app.patch("/api/queue/999999/skip")
    assert resp.status_code == 404


def test_queue_status_update_not_found(test_app: TestClient):
    """PATCH /api/queue/999999/status returns 404 for non-existent match."""
    resp = test_app.patch("/api/queue/999999/status", json={"status": "applied"})
    assert resp.status_code == 404
