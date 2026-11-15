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


def test_search_stores_jobs_under_the_shared_dedup_key(test_app: TestClient, monkeypatch):
    """Manual search hashes with ``dedup_key``: whitespace variants are one job."""
    import asyncio

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.job import Job
    from backend.models.schemas import RawJob
    from backend.scraping.adzuna_client import AdzunaClient
    from backend.scraping.deduplicator import dedup_key

    titles = iter(["Robotics  Engineer", " robotics engineer"])

    async def fake_search(self, **kwargs):
        return [RawJob(title=next(titles), company="ACME ", location="Paris", url="http://x")]

    monkeypatch.setattr(AdzunaClient, "search", fake_search)
    body = {"keywords": ["robotics"], "country": "fr", "max_results": 5}
    assert test_app.post("/api/jobs/search", json=body).json()["stored"] == 1
    assert test_app.post("/api/jobs/search", json=body).json()["stored"] == 0

    async def _hashes():
        async with AsyncSessionLocal() as db:
            return (await db.execute(select(Job.dedup_hash))).scalars().all()

    assert asyncio.run(_hashes()) == [dedup_key("acme", "robotics engineer", "paris")]


# ─── Queue ────────────────────────────────────────────────────────────────────


def test_list_queue_empty(test_app: TestClient):
    """GET /api/queue returns 200 with empty list."""
    resp = test_app.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)


def test_queue_lists_title_only_matches_after_described_ones(test_app: TestClient):
    """A provisional 70 does not outrank a described posting; both flags are exposed."""
    import asyncio
    from datetime import date

    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch

    async def seed() -> None:
        async with AsyncSessionLocal() as db:
            for n, (score, provisional) in enumerate([(70.0, True), (40.0, False), (55.0, False)]):
                job = Job(title=f"T{n}", company="C", url="", dedup_hash=f"q{n}")
                db.add(job)
                await db.flush()
                db.add(
                    JobMatch(
                        job_id=job.id,
                        score=score,
                        status="new",
                        batch_date=date(2026, 11, 14),
                        provisional=provisional,
                        location_unverified=provisional,
                    )
                )
            await db.commit()

    asyncio.run(seed())
    matches = test_app.get("/api/queue").json()["matches"]
    assert [(m["score"], m["provisional"], m["location_unverified"]) for m in matches] == [
        (55.0, False, False),
        (40.0, False, False),
        (70.0, True, True),
    ]


def test_queue_refresh(test_app: TestClient):
    """POST /api/queue/refresh returns 200 with a status message."""
    resp = test_app.post("/api/queue/refresh")
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_queue_refresh_dry_run_returns_preview(test_app: TestClient):
    """POST /api/queue/refresh?dry_run=true runs inline and returns a preview.

    The runner is swapped for a stub so the endpoint stays hermetic (no real
    scraping / LLM). We assert the {status:'preview', matches, total} shape.
    """
    from unittest.mock import AsyncMock

    app = test_app.app
    runner = app.state.batch_runner
    original_run = runner.run_batch
    original_running = runner.running
    runner.running = False
    stub_run = AsyncMock(
        return_value=[
            {"title": "Backend Engineer", "company": "ACME", "score": 88, "location": "Paris"},
            {"title": "Platform Engineer", "company": "Globex", "score": 72, "location": "Remote"},
        ]
    )
    runner.run_batch = stub_run
    try:
        resp = test_app.post("/api/queue/refresh?dry_run=true")
    finally:
        runner.run_batch = original_run
        runner.running = original_running

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "preview"
    assert data["total"] == 2
    assert len(data["matches"]) == 2
    assert data["matches"][0]["title"] == "Backend Engineer"
    assert data["matches"][0]["company"] == "ACME"
    # The runner was invoked in dry-run mode (no background task).
    stub_run.assert_awaited_once_with(dry_run=True)


def test_queue_skip_not_found(test_app: TestClient):
    """PATCH /api/queue/999999/skip returns 404 for non-existent match."""
    resp = test_app.patch("/api/queue/999999/skip")
    assert resp.status_code == 404


def test_queue_status_update_not_found(test_app: TestClient):
    """PATCH /api/queue/999999/status returns 404 for non-existent match."""
    resp = test_app.patch("/api/queue/999999/status", json={"status": "applied"})
    assert resp.status_code == 404


def test_queue_source_health_returns_list(test_app: TestClient):
    """GET /api/queue/source-health returns 200 with a ``sources`` list."""
    resp = test_app.get("/api/queue/source-health")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert isinstance(data["sources"], list)
