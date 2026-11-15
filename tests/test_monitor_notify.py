"""Monitor notifications: one WebSocket frame, one ntfy POST. No network."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from backend.api.ws import manager
from backend.config import settings
from backend.models.job import Job, JobMatch
from backend.monitor import notify, poller
from backend.monitor.notify import notify_new_match, notify_text

NTFY_URL = "https://ntfy.test/jobpilot"


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


@pytest.fixture
def ws():
    fake = FakeWS()
    manager.active_connections.clear()
    manager.active_connections["c1"] = fake  # type: ignore[assignment]
    yield fake
    manager.active_connections.clear()


class Recorder(list):
    """The requests ntfy received, plus the knobs of the fake server."""

    state: dict


@pytest.fixture
def ntfy(monkeypatch):
    """Route the notifier's HTTP client to a recorder; returns the requests seen."""
    requests = Recorder()
    state = {"status": 200, "timeouts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(state["status"])

    real_client = httpx.AsyncClient

    def fake_client(**kw) -> httpx.AsyncClient:
        state["timeouts"].append(kw.get("timeout"))
        return real_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(notify.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(settings, "NTFY_URL", NTFY_URL)
    requests.state = state
    return requests


async def drain() -> None:
    """Wait for the ntfy pushes still going out in the background."""
    await asyncio.gather(*notify._pending)


def a_match(**job_kw) -> tuple[JobMatch, Job]:
    job = Job(
        **{
            "id": 7,
            "title": "Ingénieur Robotique",
            "company": "Mirakl",
            "location": "Paris, France",
            "url": "https://job-boards.greenhouse.io/mirakl/jobs/7",
            "posted_at": datetime(2026, 11, 14, 8, 30),
            "dedup_hash": "h",
            **job_kw,
        }
    )
    return JobMatch(id=3, job_id=7, score=87.4, status="new"), job


async def test_ntfy_post_has_title_and_click_headers(ntfy, ws):
    match, job = a_match()
    await notify_new_match(match, job, "greenhouse:mirakl")
    await drain()

    assert len(ntfy) == 1
    request = ntfy[0]
    assert request.method == "POST" and str(request.url) == NTFY_URL
    assert request.headers["Title"] == "New match 87"
    assert request.headers["Click"] == job.url
    assert request.content.decode() == "Ingénieur Robotique — Mirakl (Paris, France)"
    assert ntfy.state["timeouts"] == [5]


async def test_new_match_is_broadcast_with_job_fields(ntfy, ws):
    match, job = a_match()
    await notify_new_match(match, job, "greenhouse:mirakl")
    await drain()

    assert ws.sent == [
        {
            "type": "new_match",
            "match_id": 3,
            "job_id": 7,
            "title": "Ingénieur Robotique",
            "company": "Mirakl",
            "location": "Paris, France",
            "score": 87.4,
            "source": "greenhouse:mirakl",
            "posted_at": "2026-11-14T08:30:00",
            "url": "https://job-boards.greenhouse.io/mirakl/jobs/7",
            "provisional": False,
            "location_unverified": False,
        }
    ]


async def test_notification_says_what_the_score_rests_on(ntfy, ws):
    match, job = a_match()
    match.provisional, match.location_unverified = True, True
    await notify_new_match(match, job, "greenhouse:mirakl")
    await drain()
    assert ntfy[0].headers["Title"] == "New match 87 (title only, location unverified)"
    assert (ws.sent[0]["provisional"], ws.sent[0]["location_unverified"]) == (True, True)


async def test_job_without_location_or_date(ntfy, ws):
    match, job = a_match(location=None, posted_at=None)
    await notify_new_match(match, job, "indeed:robotics")
    await drain()

    assert ntfy[0].content.decode() == "Ingénieur Robotique — Mirakl"
    assert ws.sent[0]["location"] == "" and ws.sent[0]["posted_at"] is None


async def test_ntfy_failure_does_not_raise(ntfy, ws, monkeypatch):
    match, job = a_match()
    ntfy.state["status"] = 500
    await notify_new_match(match, job, "greenhouse:mirakl")  # HTTP error: swallowed
    await notify_text("Source disabled", "greenhouse:mirakl: key revoked")
    await drain()
    assert len(ntfy) == 2

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ntfy unreachable")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        notify.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(boom), **kw),
    )
    await notify_new_match(match, job, "greenhouse:mirakl")  # network error: swallowed
    await notify_text("Source disabled", "greenhouse:mirakl: key revoked")
    await drain()
    # the WebSocket frame went out every time regardless
    assert [m["type"] for m in ws.sent] == ["new_match", "new_match"]


async def test_no_ntfy_url_sends_nothing(ntfy, ws, monkeypatch):
    monkeypatch.setattr(settings, "NTFY_URL", "")
    match, job = a_match()
    await notify_new_match(match, job, "greenhouse:mirakl")
    await notify_text("Source disabled", "greenhouse:mirakl: key revoked")
    await drain()

    assert ntfy == [] and ntfy.state["timeouts"] == []  # no client was even built
    assert len(ws.sent) == 1  # the page is still told


async def test_notify_text_posts_a_plain_alert(ntfy, ws):
    await notify_text("Source disabled", "greenhouse:mirakl: key revoked")
    await drain()

    assert len(ntfy) == 1
    assert ntfy[0].headers["Title"] == "Source disabled"
    assert "Click" not in ntfy[0].headers
    assert ntfy[0].content.decode() == "greenhouse:mirakl: key revoked"
    assert ws.sent == []


async def test_tick_uses_the_real_notifiers(monkeypatch):
    from backend.database import AsyncSessionLocal
    from backend.models.job import JobSource

    seen: list[tuple] = []

    async def fake_poll_source(db, source, client, notify_fn, alert_fn) -> int:
        seen.append((notify_fn, alert_fn))
        return 0

    monkeypatch.setattr(poller, "poll_source", fake_poll_source)
    async with AsyncSessionLocal() as db:
        db.add(JobSource(name="gh", type="monitor", config={"adapter": "greenhouse", "slug": "x"}))
        await db.commit()

    await poller.monitor_tick()

    assert seen == [(notify_new_match, notify_text)]


async def test_hanging_ntfy_does_not_hold_up_the_tick(ws, monkeypatch):
    """A black-holed ntfy must cost the tick nothing: pushes go out in the background."""
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.job import JobSource
    from backend.models.schemas import RawJob
    from backend.models.user import SearchSettings
    from backend.scraping.sources import ADAPTERS, PollResult

    hanging = 0

    async def black_hole(request: httpx.Request) -> httpx.Response:
        nonlocal hanging
        hanging += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        notify.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(black_hole), **kw),
    )
    monkeypatch.setattr(settings, "NTFY_URL", NTFY_URL)
    monkeypatch.setattr(notify, "NTFY_TIMEOUT_S", 0.2)  # the deadline of one whole push

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        return PollResult(
            jobs=[
                RawJob(external_id=str(i), title=f"Engineer {i}", company="ACME", url="https://x")
                for i in range(5)
            ]
        )

    monkeypatch.setitem(ADAPTERS, "fake", poll)
    async with AsyncSessionLocal() as db:
        db.add(SearchSettings(id=1, keywords=[], min_match_score=0))
        db.add(
            JobSource(
                name="fake",
                type="fake",
                config={"adapter": "fake"},
                last_scraped_at=datetime(2026, 11, 14, 8, 0),  # seeded: new jobs notify
            )
        )
        await db.commit()

    await asyncio.wait_for(poller.monitor_tick(), timeout=5)  # far below 5 x the push deadline

    async with AsyncSessionLocal() as db:
        source = (await db.execute(select(JobSource))).scalar_one()
        assert source.fail_count == 0 and source.last_scraped_at is not None
    assert len(ws.sent) == 5  # the page was told before the tick returned
    await drain()  # every push gives up at its deadline instead of hanging for ever
    assert hanging == 5 and not notify._pending
