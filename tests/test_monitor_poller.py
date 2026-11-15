"""Monitor poller: seed silently, notify new IDs, back off, never raise. No network."""

from __future__ import annotations

import copy
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import RawJob
from backend.models.user import SearchSettings
from backend.monitor import poller, store
from backend.monitor.poller import monitor_tick, next_due, poll_source
from backend.scraping.sources import ADAPTERS, PollResult, SourceError
from backend.scraping.sources import indeed as indeed_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
BOARD = json.loads((FIXTURES / "greenhouse_mirakl.json").read_text())
INDEED = json.loads((FIXTURES / "indeed_search.json").read_text())
NOW = datetime(2026, 11, 14, 9, 0)
JSON_HEADERS = {"content-type": "application/json"}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def board_client(body: dict = BOARD, etag: str = 'W/"v1"') -> httpx.AsyncClient:
    return client_for(
        lambda request: httpx.Response(200, json=body, headers=JSON_HEADERS | {"etag": etag})
    )


def blocked_client() -> httpx.AsyncClient:
    return client_for(
        lambda request: httpx.Response(
            403, text="<html>Just a moment...</html>", headers={"content-type": "text/html"}
        )
    )


def board_with_extra_job() -> dict:
    body = copy.deepcopy(BOARD)
    extra = copy.deepcopy(body["jobs"][0])
    extra.update(id=999001, title="Robotics Software Engineer")
    body["jobs"].append(extra)
    return body


async def add_source(db, name: str = "gh-mirakl", **kw) -> JobSource:
    base = dict(name=name, type="greenhouse", config={"adapter": "greenhouse", "slug": "mirakl"})
    src = JobSource(**{**base, **kw})
    db.add(src)
    # every job matches: the fixture titles are not what is under test here
    if (await db.execute(select(SearchSettings))).first() is None:
        db.add(SearchSettings(id=1, keywords=[], min_match_score=0))
    await db.commit()
    return src


async def count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def freeze(monkeypatch, now: datetime = NOW) -> None:
    monkeypatch.setattr(poller, "naive_utc_now", lambda: now)
    monkeypatch.setattr(store, "naive_utc_now", lambda: now)


def hooks() -> tuple[AsyncMock, AsyncMock]:
    return AsyncMock(), AsyncMock()


# ── poll_source ──────────────────────────────────────────────────────────────


async def test_first_poll_seeds_without_notifying(monkeypatch):  # Review Focus 1
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        n = await poll_source(db, src, board_client(), notify, alert)
    async with AsyncSessionLocal() as db:  # a fresh session: it was committed
        assert n == 3
        assert notify.await_count == 0 and alert.await_count == 0
        assert await count(db, Job) == 3 and await count(db, JobMatch) == 3
        stored = await db.get(JobSource, src.id)
        assert stored.last_scraped_at == NOW and stored.first_polled_at == NOW
        assert stored.etag == 'W/"v1"'
        assert (stored.fail_count, stored.last_error) == (0, None)
        # default greenhouse interval is 60 s, jittered by ±20 %
        assert NOW + timedelta(seconds=48) <= stored.next_poll_at <= NOW + timedelta(seconds=72)


async def test_second_poll_notifies_only_new_ids(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        await poll_source(db, src, board_client(), notify, alert)
        n = await poll_source(
            db, src, board_client(board_with_extra_job(), 'W/"v2"'), notify, alert
        )
        assert n == 1 and notify.await_count == 1
        match, job, source_name = notify.await_args.args
        assert (job.external_id, job.title) == ("999001", "Robotics Software Engineer")
        assert match.job_id == job.id and source_name == "gh-mirakl"
        assert await count(db, Job) == 4 and await count(db, JobMatch) == 4
        assert src.etag == 'W/"v2"'


async def test_windowed_source_does_not_notify_jobs_older_than_its_seed(monkeypatch):
    """Review Focus 1: a later window returns postings the seed's page did not hold."""
    notify, alert = hooks()
    later = NOW + timedelta(minutes=5)
    polls = [
        [raw_job("li-1", "Seeded", posted_at=NOW - timedelta(hours=1))],
        [
            raw_job("li-2", "Before the seed", posted_at=NOW - timedelta(minutes=30)),
            raw_job("li-3", "Day before, no time", posted_at=datetime(2026, 11, 13)),
            raw_job("li-4", "After the seed", posted_at=NOW + timedelta(minutes=2)),
            raw_job("li-5", "Seed day, no time", posted_at=datetime(2026, 11, 14)),
            raw_job("li-6", "No date"),
        ],
    ]

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        return PollResult(jobs=polls.pop(0))

    monkeypatch.setitem(ADAPTERS, "linkedin_guest", poll)
    async with AsyncSessionLocal() as db:
        src = await add_source(
            db, name="li", type="linkedin_guest", config={"adapter": "linkedin_guest"}
        )
        freeze(monkeypatch)
        assert await poll_source(db, src, board_client(), notify, alert) == 1
        freeze(monkeypatch, later)
        # all five are stored and matched ...
        assert await poll_source(db, src, board_client(), notify, alert) == 5
        assert await count(db, Job) == 6 and await count(db, JobMatch) == 6
        # ... the seed instant does not move ...
        assert (src.first_polled_at, src.last_scraped_at) == (NOW, later)
    # ... and only those that can be newer than the seed are pushed
    assert sorted(call.args[1].title for call in notify.await_args_list) == [
        "After the seed",
        "No date",
        "Seed day, no time",
    ]


async def test_failed_first_poll_still_seeds_silently_afterwards(monkeypatch):
    """A failure does not count as the first poll: the next success must not flood."""
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        assert await poll_source(db, src, blocked_client(), notify, alert) == 0
        assert src.last_scraped_at is None
        assert await poll_source(db, src, board_client(), notify, alert) == 3
        assert notify.await_count == 0
        assert (src.fail_count, src.last_error) == (0, None)


async def test_notify_failure_does_not_fail_the_poll(monkeypatch):
    freeze(monkeypatch)
    notify, alert = AsyncMock(side_effect=RuntimeError("ntfy down")), AsyncMock()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        await poll_source(db, src, board_client(), notify, alert)
        n = await poll_source(db, src, board_client(board_with_extra_job()), notify, alert)
        assert n == 1 and src.fail_count == 0
        assert await count(db, JobMatch) == 4


async def test_not_modified_touches_only_schedule(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        return httpx.Response(304)

    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        await poll_source(db, src, board_client(), notify, alert)
        later = NOW + timedelta(minutes=5)
        freeze(monkeypatch, later)
        n = await poll_source(db, src, client_for(handler), notify, alert)
        assert n == 0 and seen == ['W/"v1"']
        assert notify.await_count == 0
        assert await count(db, Job) == 3 and await count(db, JobMatch) == 3
        assert src.etag == 'W/"v1"'
        assert src.last_scraped_at == later and src.next_poll_at > later
        jobs = (await db.execute(select(Job))).scalars().all()
        assert {j.last_seen_at for j in jobs} == {NOW}  # stored jobs untouched


async def test_blocked_backs_off_and_keeps_jobs(monkeypatch):  # Review Focus 2
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        await poll_source(db, src, board_client(), notify, alert)
        before = await count(db, Job)
        seeded_at = src.last_scraped_at
        later = NOW + timedelta(minutes=5)
        freeze(monkeypatch, later)
        n = await poll_source(db, src, blocked_client(), notify, alert)
    async with AsyncSessionLocal() as db:
        stored = await db.get(JobSource, src.id)
        assert n == 0 and stored.fail_count == 1
        assert stored.next_poll_at == later + timedelta(seconds=120)  # 60 s doubled, no jitter
        assert stored.next_poll_at > later + timedelta(seconds=60)
        assert "403" in stored.last_error and stored.enabled is True
        assert stored.last_scraped_at == seeded_at and stored.etag == 'W/"v1"'
        assert await count(db, Job) == before and await count(db, JobMatch) == before
        jobs = (await db.execute(select(Job))).scalars().all()
        assert {j.last_seen_at for j in jobs} == {NOW}
        assert notify.await_count == 0 and alert.await_count == 0


async def test_repeated_failures_back_off_exponentially(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db, poll_interval_s=100)
        for expected in (200, 400, 800):
            await poll_source(db, src, blocked_client(), notify, alert)
            assert src.next_poll_at == NOW + timedelta(seconds=expected)
        assert src.fail_count == 3


async def test_network_error_backs_off_and_never_raises(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        assert await poll_source(db, src, client_for(handler), notify, alert) == 0
        assert src.fail_count == 1 and "ConnectError" in src.last_error
        assert src.next_poll_at > NOW + timedelta(seconds=60)


async def test_unknown_adapter_backs_off_and_never_raises(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db, config={"adapter": "nope"})
        assert await poll_source(db, src, board_client(), notify, alert) == 0
        assert src.fail_count == 1 and "nope" in src.last_error


async def test_auth_error_disables_source(monkeypatch):
    freeze(monkeypatch)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        client = client_for(lambda request: httpx.Response(401, json={}, headers=JSON_HEADERS))
        n = await poll_source(db, src, client, notify, alert)
    async with AsyncSessionLocal() as db:
        stored = await db.get(JobSource, src.id)
        assert n == 0 and stored.enabled is False
        assert "401" in stored.last_error
        alert.assert_awaited_once_with("Source disabled", "gh-mirakl: HTTP 401")
        assert notify.await_count == 0


async def add_indeed(db, **kw) -> JobSource:
    cfg = {"adapter": "indeed", "what": "robotics", "where": "Paris"}
    return await add_source(db, name="indeed-paris", type="indeed", config=cfg, **kw)


def indeed_client(queries: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(json.loads(request.content)["query"])
        return httpx.Response(200, json=INDEED, headers=JSON_HEADERS)

    return client_for(handler)


async def test_truncated_indeed_halves_window(monkeypatch):  # Review Focus 5
    freeze(monkeypatch)
    monkeypatch.setattr(indeed_adapter, "naive_utc_now", lambda: NOW)
    monkeypatch.setattr(indeed_adapter, "LIMIT", 3)  # the fixture's 3 results fill the page
    notify, alert = hooks()
    queries: list[str] = []
    async with AsyncSessionLocal() as db:
        src = await add_indeed(db, window_hours=8)
        await poll_source(db, src, indeed_client(queries), notify, alert)
        assert 'start: "8h"' in queries[0]
        assert src.window_hours == 4 and src.fail_count == 0
        await poll_source(db, src, indeed_client(queries), notify, alert)
        assert 'start: "4h"' in queries[1]  # the column, not just the row, drives the adapter
        assert src.window_hours == 2
        src.window_hours = 1
        await poll_source(db, src, indeed_client(queries), notify, alert)
        assert src.window_hours == 1  # minimum


async def test_truncated_indeed_halves_the_default_window(monkeypatch):
    freeze(monkeypatch)
    monkeypatch.setattr(indeed_adapter, "naive_utc_now", lambda: NOW)
    monkeypatch.setattr(indeed_adapter, "LIMIT", 3)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_indeed(db)  # window_hours NULL -> the adapter's 2 h
        await poll_source(db, src, indeed_client([]), notify, alert)
        assert src.window_hours == 1


async def test_indeed_window_untouched_when_not_truncated(monkeypatch):
    freeze(monkeypatch)
    monkeypatch.setattr(indeed_adapter, "naive_utc_now", lambda: NOW)
    notify, alert = hooks()
    async with AsyncSessionLocal() as db:
        src = await add_indeed(db, window_hours=8)
        await poll_source(db, src, indeed_client([]), notify, alert)
        assert src.window_hours == 8
        # default indeed interval is 420 s
        assert NOW + timedelta(seconds=336) <= src.next_poll_at <= NOW + timedelta(seconds=504)


# ── next_due ─────────────────────────────────────────────────────────────────


def test_next_due_success_is_jittered_within_twenty_percent():
    rng = random.Random(7)
    dues = {next_due(NOW, 100, 0, rng) for _ in range(200)}
    assert len(dues) > 100  # jittered: sources do not stay in lockstep
    assert all(NOW + timedelta(seconds=80) <= d <= NOW + timedelta(seconds=120) for d in dues)


def test_next_due_backoff_doubles_per_failure():
    rng = random.Random(7)
    assert next_due(NOW, 60, 1, rng) == NOW + timedelta(seconds=120)
    assert next_due(NOW, 60, 3, rng) == NOW + timedelta(seconds=480)


def test_next_due_backoff_is_capped_at_six_hours():
    rng = random.Random(7)
    assert next_due(NOW, 60, 9, rng) == NOW + timedelta(hours=6)
    assert next_due(NOW, 60, 5000, rng) == NOW + timedelta(hours=6)  # no overflow


# ── monitor_tick ─────────────────────────────────────────────────────────────


def fake_adapter(monkeypatch, name: str = "fake") -> list[str]:
    polled: list[str] = []

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        polled.append(cfg["slug"])
        if cfg["slug"] == "boom":
            raise SourceError("blocked", "HTTP 429")
        if cfg["slug"] == "crash":
            raise RuntimeError("adapter bug")
        return PollResult()

    monkeypatch.setitem(ADAPTERS, name, poll)
    return polled


async def add_fake(db, slug: str, **kw) -> JobSource:
    return await add_source(
        db, name=slug, type="fake", config={"adapter": "fake", "slug": slug}, **kw
    )


async def test_tick_skips_sources_not_due(monkeypatch):
    freeze(monkeypatch)
    polled = fake_adapter(monkeypatch)
    async with AsyncSessionLocal() as db:
        await add_fake(db, "never-polled")
        await add_fake(db, "due", next_poll_at=NOW - timedelta(seconds=1))
        await add_fake(db, "not-due", next_poll_at=NOW + timedelta(seconds=30))
        await add_fake(db, "disabled", enabled=False)
        # a legacy batch source: no adapter in its config
        await add_source(db, name="legacy", type="adzuna", config={"country": "fr"})
        await add_source(db, name="no-config", type="adzuna", config=None)

    await monitor_tick()

    assert sorted(polled) == ["due", "never-polled"]
    async with AsyncSessionLocal() as db:
        rows = {s.name: s for s in (await db.execute(select(JobSource))).scalars()}
        assert rows["due"].last_scraped_at == NOW and rows["due"].next_poll_at > NOW
        assert rows["not-due"].last_scraped_at is None
        assert rows["legacy"].next_poll_at is None and rows["no-config"].next_poll_at is None


async def test_tick_survives_failing_sources(monkeypatch):
    freeze(monkeypatch)
    polled = fake_adapter(monkeypatch)
    async with AsyncSessionLocal() as db:
        await add_fake(db, "boom")
        await add_fake(db, "crash")
        await add_fake(db, "fine")

    await monitor_tick()  # must not raise

    assert sorted(polled) == ["boom", "crash", "fine"]
    async with AsyncSessionLocal() as db:
        rows = {s.name: s for s in (await db.execute(select(JobSource))).scalars()}
        assert rows["boom"].fail_count == 1 and rows["crash"].fail_count == 1
        assert rows["fine"].fail_count == 0 and rows["fine"].last_scraped_at == NOW


async def test_tick_limits_concurrency(monkeypatch):
    import asyncio

    freeze(monkeypatch)
    monkeypatch.setattr(poller.settings, "MONITOR_MAX_CONCURRENCY", 2)
    running = peak = 0

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return PollResult()

    monkeypatch.setitem(ADAPTERS, "fake", poll)
    async with AsyncSessionLocal() as db:
        for i in range(6):
            await add_fake(db, f"s{i}")

    await monitor_tick()

    assert peak == 2


def raw_job(external_id: str, title: str, **kw) -> RawJob:
    return RawJob(
        external_id=external_id,
        title=title,
        company="Mirakl",
        location="Paris",
        url=f"https://example.com/{external_id}",
        **kw,
    )


async def test_tick_polls_fetch_together_but_write_one_at_a_time(monkeypatch):
    """Interleaved writers wait on SQLite's lock and fail with 'database is locked'."""
    import asyncio

    freeze(monkeypatch)
    both_fetched = asyncio.Barrier(2)  # the fetches do overlap
    events: list[str] = []
    real_upsert, real_commit = poller.upsert_discovered, poller.AsyncSession.commit

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        await both_fetched.wait()
        return PollResult(jobs=[raw_job(cfg["slug"], f"Job {cfg['slug']}")])

    async def upsert(db, source, jobs):
        events.append(f"write {source.name}")
        await asyncio.sleep(0.01)  # room for the other poll to start writing
        return await real_upsert(db, source, jobs)

    async def commit(self):
        await real_commit(self)
        events.append("commit")

    monkeypatch.setitem(ADAPTERS, "fake", poll)
    monkeypatch.setattr(poller, "upsert_discovered", upsert)
    async with AsyncSessionLocal() as db:
        await add_fake(db, "a")
        await add_fake(db, "b")
    monkeypatch.setattr(poller.AsyncSession, "commit", commit)

    await monitor_tick()

    assert sorted(events[::2]) == ["write a", "write b"]
    assert events[1::2] == ["commit", "commit"]


async def test_engine_waits_for_the_write_lock_longer_than_a_seeding_poll():
    """sqlite3's default of 5 s is shorter than a board's first poll holds the lock."""
    from backend.database import engine

    async with engine.connect() as conn:
        timeout_ms = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
    assert timeout_ms >= 30_000


async def test_tick_same_job_from_two_sources_fails_neither(monkeypatch):  # Review Focus 3
    """Both sources insert the same new hash in one tick: one row, and no failed poll."""
    import asyncio

    freeze(monkeypatch)
    notify = AsyncMock()
    monkeypatch.setattr(poller, "notify_new_match", notify)
    both_fetched = asyncio.Barrier(2)  # neither stores before the other has its jobs
    jobs = {
        "board": [raw_job("gh-1", "Backend Engineer")],
        # one job of its own before and after the shared one: they must survive it
        "linkedin": [
            raw_job("li-1", "Data Engineer"),
            raw_job("li-9", "Backend  Engineer"),
            raw_job("li-2", "Site Reliability Engineer"),
        ],
    }

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        await both_fetched.wait()
        return PollResult(jobs=jobs[cfg["slug"]])

    monkeypatch.setitem(ADAPTERS, "fake", poll)
    async with AsyncSessionLocal() as db:
        seeded = NOW - timedelta(minutes=5)
        await add_fake(db, "board", last_scraped_at=seeded)
        await add_fake(db, "linkedin", last_scraped_at=seeded)

    await monitor_tick()

    async with AsyncSessionLocal() as db:
        rows = {s.name: s for s in (await db.execute(select(JobSource))).scalars()}
        assert {n: (s.fail_count, s.last_error) for n, s in rows.items()} == {
            "board": (0, None),
            "linkedin": (0, None),
        }
        assert rows["board"].last_scraped_at == NOW and rows["linkedin"].last_scraped_at == NOW
        assert await count(db, Job) == 3 and await count(db, JobMatch) == 3
    notified = sorted(call.args[1].title.replace("  ", " ") for call in notify.await_args_list)
    assert notified == ["Backend Engineer", "Data Engineer", "Site Reliability Engineer"]


async def test_db_error_mid_poll_rolls_back_then_recovers(monkeypatch):
    """A DB error after the jobs were written: nothing is kept, and nothing is lost later."""
    from sqlalchemy.exc import OperationalError

    freeze(monkeypatch)
    notify, alert = hooks()
    real_match_new = poller.match_new

    async def locked(*args, **kw):
        raise OperationalError("INSERT INTO job_matches", {}, Exception("database is locked"))

    async with AsyncSessionLocal() as db:
        src = await add_source(db)
        await poll_source(db, src, board_client(), notify, alert)  # seed
        etag = src.etag

        monkeypatch.setattr(poller, "match_new", locked)
        changed = board_client(board_with_extra_job(), 'W/"v2"')
        n = await poll_source(db, src, changed, notify, alert)
        assert n == 0 and notify.await_count == 0
        assert src.fail_count == 1 and "database is locked" in src.last_error
        assert src.etag == etag  # or the next poll would be answered 304 and skip the job

    async with AsyncSessionLocal() as db:  # what was really committed
        assert await count(db, Job) == 3 and await count(db, JobMatch) == 3
        stored = await db.get(JobSource, src.id)
        assert (stored.fail_count, stored.etag, stored.last_scraped_at) == (1, etag, NOW)

        monkeypatch.setattr(poller, "match_new", real_match_new)
        n = await poll_source(
            db, stored, board_client(board_with_extra_job(), 'W/"v2"'), notify, alert
        )
        assert n == 1 and notify.await_count == 1
        assert notify.await_args.args[1].title == "Robotics Software Engineer"
        assert await count(db, Job) == 4 and await count(db, JobMatch) == 4
        assert stored.fail_count == 0
