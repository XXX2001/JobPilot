"""Monitor store: a job is stored once (by source ID, then by hash) and matched once."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails, RawJob
from backend.monitor import store
from backend.monitor.store import match_new, raw_to_details, upsert_discovered
from backend.scheduler import batch_runner
from backend.scheduler.batch_runner import BatchRunner


def raw(**kw) -> RawJob:
    base = dict(
        title="Robotics Engineer",
        company="ACME",
        location="Paris",
        url="https://example.com/job",
    )
    return RawJob(**{**base, **kw})


async def source(db, name: str = "gh-acme", type_: str = "greenhouse") -> JobSource:
    src = JobSource(name=name, type=type_)
    db.add(src)
    await db.flush()
    return src


async def count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def freeze(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(store, "naive_utc_now", lambda: now)


# ── upsert_discovered ────────────────────────────────────────────────────────


async def test_new_job_gets_identity_and_seen_times(monkeypatch):
    now = datetime(2026, 11, 14, 9, 0)
    freeze(monkeypatch, now)
    async with AsyncSessionLocal() as db:
        src = await source(db)
        (job,) = await upsert_discovered(db, src, [raw(external_id="gh-1", description="d")])
        await db.commit()
    assert job.id is not None
    assert (job.source_id, job.external_id) == (src.id, "gh-1")
    assert (job.first_seen_at, job.last_seen_at) == (now, now)
    assert (job.title, job.company, job.location) == ("Robotics Engineer", "ACME", "Paris")
    assert job.apply_url == "https://example.com/job"  # falls back to the posting URL


async def test_same_external_id_twice_inserts_once():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        first = await upsert_discovered(db, src, [raw(external_id="gh-1")])
        second = await upsert_discovered(db, src, [raw(external_id="gh-1")])
        assert len(first) == 1 and second == []
        assert await count(db, Job) == 1


async def test_external_id_wins_over_a_changed_title():
    """A retitled posting keeps its row: the source ID is looked up before the hash."""
    async with AsyncSessionLocal() as db:
        src = await source(db)
        await upsert_discovered(db, src, [raw(external_id="gh-1")])
        new = await upsert_discovered(db, src, [raw(external_id="gh-1", title="Senior Robotics")])
        assert new == [] and await count(db, Job) == 1


async def test_same_job_from_two_sources_is_one_row():  # Review Focus 3
    async with AsyncSessionLocal() as db:
        greenhouse_src = await source(db, "gh-mirakl")
        linkedin_src = await source(db, "linkedin", "linkedin_guest")
        await upsert_discovered(
            db,
            greenhouse_src,
            [
                raw(
                    external_id="gh-1",
                    company="Mirakl",
                    title="Backend Engineer",
                    location="Paris",
                )
            ],
        )
        new = await upsert_discovered(
            db,
            linkedin_src,
            [
                raw(
                    external_id="li-9",
                    company="mirakl",
                    title="Backend  Engineer",
                    location="Paris",
                )
            ],
        )
        assert new == [] and await count(db, Job) == 1
        job = (await db.execute(select(Job))).scalar_one()
        # the first sighting keeps the identity
        assert (job.source_id, job.external_id) == (greenhouse_src.id, "gh-1")


async def test_duplicates_inside_one_poll_insert_once():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        new = await upsert_discovered(
            db,
            src,
            [
                raw(external_id="a"),
                raw(external_id="a"),  # same ID
                raw(external_id="b"),  # same hash, other ID
                raw(external_id="", title="No ID"),
                raw(external_id="", title="No ID"),
            ],
        )
        assert [j.external_id for j in new] == ["a", None]
        assert await count(db, Job) == 2


async def test_first_seen_is_kept_and_last_seen_moves(monkeypatch):
    t0 = datetime(2026, 11, 14, 9, 0)
    t1 = t0 + timedelta(minutes=5)
    async with AsyncSessionLocal() as db:
        src = await source(db)
        freeze(monkeypatch, t0)
        (job,) = await upsert_discovered(db, src, [raw(external_id="gh-1")])
        freeze(monkeypatch, t1)
        await upsert_discovered(db, src, [raw(external_id="gh-1")])
        await db.commit()
    async with AsyncSessionLocal() as db:
        row = await db.get(Job, job.id)
    assert (row.first_seen_at, row.last_seen_at) == (t0, t1)


async def test_existing_row_gains_longer_description_and_missing_apply_url():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        db.add(
            Job(
                title="Robotics Engineer",
                company="ACME",
                location="Paris",
                url="",
                description="a long description",
                apply_url="",
                dedup_hash=store.dedup_key("ACME", "Robotics Engineer", "Paris"),
            )
        )
        await db.flush()
        new = await upsert_discovered(db, src, [raw(description="short", apply_url="https://a/1")])
        job = (await db.execute(select(Job))).scalar_one()
        assert new == []
        assert (job.description, job.apply_url) == ("a long description", "https://a/1")

        await upsert_discovered(
            db,
            src,
            [
                raw(
                    description="a much longer description than before",
                    apply_url="https://a/2",
                )
            ],
        )
        assert job.description == "a much longer description than before"
        assert job.apply_url == "https://a/1"  # only a missing apply_url is filled


# ── match_new ────────────────────────────────────────────────────────────────


async def test_match_new_never_creates_second_match():
    filters = JobFilters()
    async with AsyncSessionLocal() as db:
        src = await source(db)
        job = (await upsert_discovered(db, src, [raw(external_id="a")]))[0]
        assert len(await match_new(db, [job, job], filters, 0)) == 1
        assert await match_new(db, [job], filters, 0) == []
        assert await count(db, JobMatch) == 1


async def test_match_new_row_shape(monkeypatch):
    freeze(monkeypatch, datetime(2026, 11, 14, 23, 30))
    filters = JobFilters(keywords=["python", "ros", "rust"])
    async with AsyncSessionLocal() as db:
        src = await source(db)
        (job,) = await upsert_discovered(
            db, src, [raw(external_id="a", description="Python and ROS on robots")]
        )
        (match,) = await match_new(db, [job], filters, 0)
        await db.commit()
    assert match.id is not None and match.job_id == job.id
    assert (match.status, match.batch_date) == ("new", date(2026, 11, 14))
    assert match.keyword_hits == {"python": True, "ros": True}
    assert match.score == JobMatcher().score(raw_to_details(job), filters)


async def test_match_new_skips_low_scores_and_any_existing_status():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        low, skipped = await upsert_discovered(
            db, src, [raw(external_id="a", title="A"), raw(external_id="b", title="B")]
        )
        db.add(JobMatch(job_id=skipped.id, score=1.0, status="skipped"))
        await db.flush()
        assert await match_new(db, [low], JobFilters(), 101) == []
        assert await match_new(db, [skipped], JobFilters(), 0) == []
        assert await count(db, JobMatch) == 1


def test_raw_to_details_accepts_a_job_row_with_nulls():
    details = raw_to_details(Job(title="T", company="C", url="u", dedup_hash="h"))
    assert (details.location, details.description, details.apply_url) == ("", "", "")


# ── batch path (spec D3) ─────────────────────────────────────────────────────


def _freeze_batch_day(monkeypatch, day: date) -> None:
    class _Date(date):
        @classmethod
        def today(cls):
            return day

    monkeypatch.setattr(batch_runner, "date", _Date)


def _runner() -> BatchRunner:
    return BatchRunner(
        scraper=None, matcher=JobMatcher(), cv_pipeline=None, db_factory=AsyncSessionLocal
    )


async def test_batch_path_does_not_requeue_next_day(monkeypatch):
    runner = _runner()
    ranked = [(JobDetails(title="Robotics Engineer", company="ACME", location="Paris"), 80.0)]

    _freeze_batch_day(monkeypatch, date(2026, 11, 14))
    async with AsyncSessionLocal() as db:
        first = await runner._store_matches(db, ranked, JobFilters())
    _freeze_batch_day(monkeypatch, date(2026, 11, 15))
    async with AsyncSessionLocal() as db:
        second = await runner._store_matches(db, ranked, JobFilters())
        assert await count(db, JobMatch) == 1
        match = (await db.execute(select(JobMatch))).scalar_one()

    assert [m.match_id for m in second] == [m.match_id for m in first] == [match.id]
    assert match.batch_date == date(2026, 11, 14)


async def test_batch_path_tolerates_legacy_per_day_matches(monkeypatch):
    """Rows written before D3 (one match per day) must not break the lookup."""
    runner = _runner()
    jd = JobDetails(title="Robotics Engineer", company="ACME", location="Paris")
    async with AsyncSessionLocal() as db:
        job = Job(
            title=jd.title,
            company=jd.company,
            location=jd.location,
            url="",
            dedup_hash=store.dedup_key(jd.company, jd.title, jd.location),
        )
        db.add(job)
        await db.flush()
        db.add_all(
            [
                JobMatch(job_id=job.id, score=50.0, batch_date=date(2026, 11, 12)),
                JobMatch(job_id=job.id, score=50.0, batch_date=date(2026, 11, 13)),
            ]
        )
        await db.commit()

    _freeze_batch_day(monkeypatch, date(2026, 11, 14))
    async with AsyncSessionLocal() as db:
        stored = await runner._store_matches(db, [(jd, 80.0)], JobFilters())
        assert len(stored) == 1 and await count(db, JobMatch) == 2


def test_batch_runner_uses_the_shared_conversion():
    assert not hasattr(BatchRunner, "_raw_to_details")
    details = raw_to_details(raw(external_id="x", description="d"))
    assert (details.title, details.company, details.description) == (
        "Robotics Engineer",
        "ACME",
        "d",
    )
