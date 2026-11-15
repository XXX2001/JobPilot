"""Monitor API: status shape, lag median, pause/resume, source creation. No network."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal
from backend.models.job import Job, JobSource
from backend.utils.time import naive_utc_now

SOURCE_KEYS = {
    "id", "name", "adapter", "enabled", "interval_s", "last_scraped_at", "next_poll_at",
    "fail_count", "last_error", "jobs_seen", "median_lag_s",
}  # fmt: skip


def seed(*rows) -> list[int]:
    """Insert ORM rows; return their ids."""

    async def run() -> list[int]:
        async with AsyncSessionLocal() as db:
            db.add_all(rows)
            await db.commit()
            return [row.id for row in rows]

    return asyncio.run(run())


def source(name: str = "greenhouse:mirakl", **kw) -> JobSource:
    base = dict(name=name, type="monitor", config={"adapter": "greenhouse", "slug": "mirakl"})
    return JobSource(**{**base, **kw})


def job(source_id: int, n: int, first_seen_at, posted_at) -> Job:
    return Job(
        source_id=source_id,
        external_id=str(n),
        title=f"Job {n}",
        company="Mirakl",
        url=f"https://example.test/{n}",
        dedup_hash=f"hash-{source_id}-{n}",
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        posted_at=posted_at,
    )


def get_source(source_id: int) -> JobSource:
    async def run() -> JobSource:
        async with AsyncSessionLocal() as db:
            return await db.get(JobSource, source_id)

    return asyncio.run(run())


def status_of(client: TestClient, source_id: int) -> dict:
    sources = client.get("/api/monitor/status").json()["sources"]
    return next(s for s in sources if s["id"] == source_id)


def test_status_shape(test_app: TestClient):
    now = naive_utc_now().replace(microsecond=0)
    (sid,) = seed(
        source(
            last_scraped_at=now,
            next_poll_at=now + timedelta(seconds=60),
            fail_count=2,
            last_error="blocked: HTTP 403",
        )
    )
    seed(job(sid, 1, now, None))

    resp = test_app.get("/api/monitor/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"enabled", "sources"}
    assert body["enabled"] is False  # MONITOR_ENABLED defaults to false
    (row,) = body["sources"]
    assert set(row) == SOURCE_KEYS
    assert row["id"] == sid
    assert row["name"] == "greenhouse:mirakl"
    assert row["adapter"] == "greenhouse"
    assert row["enabled"] is True
    assert row["interval_s"] == 60  # the greenhouse default: poll_interval_s is NULL
    assert row["last_scraped_at"].startswith(now.isoformat())
    assert row["next_poll_at"].startswith((now + timedelta(seconds=60)).isoformat())
    assert row["fail_count"] == 2
    assert row["last_error"] == "blocked: HTTP 403"
    assert row["jobs_seen"] == 1
    assert row["median_lag_s"] is None


def test_status_reports_the_configured_interval_and_skips_batch_sources(test_app: TestClient):
    sid, _ = seed(
        source(poll_interval_s=900),
        JobSource(name="old-batch", type="api", config={"country": "fr"}),  # old batch path
    )
    body = test_app.get("/api/monitor/status").json()
    assert [s["id"] for s in body["sources"]] == [sid]
    assert body["sources"][0]["interval_s"] == 900


def test_median_lag_ignores_seeded_jobs(test_app: TestClient):
    now = naive_utc_now().replace(microsecond=0)
    first_poll = now - timedelta(days=2)
    (sid,) = seed(source(last_scraped_at=now))
    seed(
        # the seeding poll: old postings, all first seen at the same instant
        job(sid, 1, first_poll, first_poll - timedelta(days=30)),
        job(sid, 2, first_poll, first_poll - timedelta(days=90)),
        # found by later polls: lags of 100 s, 300 s and 1000 s
        job(sid, 3, now - timedelta(hours=5), now - timedelta(hours=5, seconds=100)),
        job(sid, 4, now - timedelta(hours=3), now - timedelta(hours=3, seconds=300)),
        job(sid, 5, now - timedelta(hours=1), now - timedelta(hours=1, seconds=1000)),
        # no posted_at: counted as seen, not in the median
        job(sid, 6, now - timedelta(minutes=5), None),
    )
    row = status_of(test_app, sid)
    assert row["jobs_seen"] == 6
    assert row["median_lag_s"] == 300


def test_median_lag_ignores_jobs_older_than_seven_days(test_app: TestClient):
    now = naive_utc_now().replace(microsecond=0)
    (sid,) = seed(source(last_scraped_at=now))
    seed(
        job(sid, 1, now - timedelta(days=20), now - timedelta(days=40)),  # seed
        job(sid, 2, now - timedelta(days=10), now - timedelta(days=10, seconds=5000)),  # too old
        job(sid, 3, now - timedelta(days=1), now - timedelta(days=1, seconds=120)),
    )
    assert status_of(test_app, sid)["median_lag_s"] == 120


def test_median_lag_is_per_source(test_app: TestClient):
    now = naive_utc_now().replace(microsecond=0)
    a, b = seed(source("greenhouse:a"), source("greenhouse:b"))
    seed(
        job(a, 1, now - timedelta(days=1), None),
        job(a, 2, now - timedelta(hours=1), now - timedelta(hours=1, seconds=60)),
        job(b, 3, now - timedelta(days=1), None),
        job(b, 4, now - timedelta(hours=1), now - timedelta(hours=1, seconds=600)),
    )
    assert status_of(test_app, a)["median_lag_s"] == 60
    assert status_of(test_app, b)["median_lag_s"] == 600


def test_pause_and_resume(test_app: TestClient):
    (sid,) = seed(source(fail_count=4, last_error="blocked: HTTP 429"))

    resp = test_app.post(f"/api/monitor/sources/{sid}/pause")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    paused = get_source(sid)
    assert paused.enabled is False
    assert paused.fail_count == 4  # pausing keeps the failure history

    resp = test_app.post(f"/api/monitor/sources/{sid}/resume")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["fail_count"] == 0
    resumed = get_source(sid)
    assert resumed.enabled is True
    assert resumed.fail_count == 0


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_resume_unknown_source_is_404(test_app: TestClient, action: str):
    assert test_app.post(f"/api/monitor/sources/999999/{action}").status_code == 404


def test_pause_does_not_apply_to_a_batch_source(test_app: TestClient):
    (sid,) = seed(JobSource(name="old-batch", type="api", config={"country": "fr"}))
    assert test_app.post(f"/api/monitor/sources/{sid}/pause").status_code == 404
    assert get_source(sid).enabled is True


def test_mutating_routes_need_the_session(test_app: TestClient):
    from backend.main import app

    (sid,) = seed(source())
    anon = TestClient(app, raise_server_exceptions=False)  # no /api/session cookie
    assert anon.post(f"/api/monitor/sources/{sid}/pause").status_code == 401
    body = {"adapter": "greenhouse", "name": "x", "config": {"slug": "x"}}
    assert anon.post("/api/monitor/sources", json=body).status_code == 401
    assert get_source(sid).enabled is True


def test_create_source(test_app: TestClient):
    resp = test_app.post(
        "/api/monitor/sources",
        json={
            "adapter": "indeed",
            "name": "indeed:robotics",
            "config": {"keywords": "robotics", "country": "FR"},
            "interval_s": 600,
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    assert set(created) == SOURCE_KEYS
    assert created["adapter"] == "indeed" and created["interval_s"] == 600

    stored = get_source(created["id"])
    assert stored.type == "monitor"
    assert stored.name == "indeed:robotics"
    assert stored.config == {"keywords": "robotics", "country": "FR", "adapter": "indeed"}
    assert stored.poll_interval_s == 600
    assert stored.enabled is True
    assert status_of(test_app, created["id"])["name"] == "indeed:robotics"


def test_create_source_without_interval_uses_the_adapter_default(test_app: TestClient):
    resp = test_app.post(
        "/api/monitor/sources",
        json={"adapter": "linkedin_guest", "name": "li:robotics", "config": {"keywords": "ros"}},
    )
    assert resp.status_code == 201
    assert resp.json()["interval_s"] == 300
    assert get_source(resp.json()["id"]).poll_interval_s is None


def test_create_source_rejects_an_unknown_adapter(test_app: TestClient):
    resp = test_app.post(
        "/api/monitor/sources",
        json={"adapter": "monster", "name": "monster:x", "config": {}},
    )
    assert resp.status_code == 422
    assert test_app.get("/api/monitor/status").json()["sources"] == []


def test_create_source_rejects_a_duplicate_name(test_app: TestClient):
    seed(source())
    resp = test_app.post(
        "/api/monitor/sources",
        json={"adapter": "greenhouse", "name": "greenhouse:mirakl", "config": {"slug": "mirakl"}},
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("interval_s", [0, -5])
def test_create_source_rejects_a_non_positive_interval(test_app: TestClient, interval_s: int):
    resp = test_app.post(
        "/api/monitor/sources",
        json={"adapter": "greenhouse", "name": "g", "config": {}, "interval_s": interval_s},
    )
    assert resp.status_code == 422
