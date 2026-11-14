"""Monitor store: a job is stored once (by source ID, then by hash) and matched once."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.matching import matcher as matcher_module
from backend.matching.filters import JobFilters
from backend.matching.matcher import JobMatcher
from backend.models.job import Job, JobMatch, JobSource
from backend.models.schemas import JobDetails, RawJob
from backend.models.user import SearchSettings
from backend.monitor import store
from backend.monitor.store import match_new, raw_to_details, upsert_discovered
from backend.scheduler import batch_runner
from backend.scheduler.batch_runner import BatchRunner


@pytest.fixture(autouse=True)
def _no_stray_cv(monkeypatch, tmp_path):
    """Other tests leave ``templates/*.tex`` in the shared data dir: ``load_matcher``
    would read one as the owner's CV."""
    monkeypatch.setattr(
        matcher_module, "settings", SimpleNamespace(jobpilot_data_dir=str(tmp_path))
    )


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


POSTING = """About the role
You will build the motion stack of our mobile manipulators.

Requirements
- Python
- ROS
"""


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


async def test_same_job_with_each_sources_location_spelling_is_one_row():  # Review Focus 3
    """The strings the live sources really return for one Paris posting."""
    async with AsyncSessionLocal() as db:
        board = await source(db, "gh-mirakl")
        linkedin = await source(db, "linkedin", "linkedin_guest")
        indeed = await source(db, "indeed", "indeed")
        job = dict(company="Mirakl", title="Backend Engineer")
        first = await upsert_discovered(
            db, board, [raw(external_id="gh-1", location="Paris, France", **job)]
        )
        second = await upsert_discovered(
            db, linkedin, [raw(external_id="li-9", location="Paris, Île-de-France, France", **job)]
        )
        third = await upsert_discovered(
            db, indeed, [raw(external_id="in-3", location="Paris, A8, FR", **job)]
        )
        assert len(first) == 1 and second == [] and third == []
        assert await count(db, Job) == 1
        assert len(await match_new(db, first + second + third, JobFilters(), 0)) == 1
        # another city is another job
        lyon = await upsert_discovered(
            db, linkedin, [raw(external_id="li-10", location="Lyon, France", **job)]
        )
        assert len(lyon) == 1 and await count(db, Job) == 2


async def test_two_sessions_inserting_the_same_new_job_do_not_collide():
    """B looks the job up before A commits it, so B's own INSERT meets A's row."""
    import asyncio

    async with AsyncSessionLocal() as setup:
        a_id, b_id = (await source(setup, "gh-mirakl")).id, (await source(setup, "linkedin")).id
        await setup.commit()
    job = dict(company="Mirakl", location="Paris")
    async with AsyncSessionLocal() as a, AsyncSessionLocal() as b:
        src_a, src_b = await a.get(JobSource, a_id), await b.get(JobSource, b_id)
        # A inserts and holds the write lock, uncommitted
        in_a = await upsert_discovered(
            a, src_a, [raw(external_id="gh-1", title="Backend Engineer", **job)]
        )
        task = asyncio.create_task(
            upsert_discovered(
                b,
                src_b,
                [  # the shared job first: B's lookup of it must miss
                    raw(external_id="li-9", title="Backend  Engineer", **job),
                    raw(external_id="li-2", title="SRE", **job),
                ],
            )
        )
        await asyncio.sleep(0.2)
        assert not task.done()  # B waits on A's lock, past its own lookup
        await a.commit()
        in_b = await task
        await b.commit()
        assert [j.external_id for j in in_a] == ["gh-1"]
        assert [j.external_id for j in in_b] == ["li-2"]
        assert await count(b, Job) == 2


async def test_retitled_posting_is_still_one_row_from_a_second_source():  # Review Focus 3
    async with AsyncSessionLocal() as db:
        board = await source(db, "gh-acme")
        linkedin = await source(db, "linkedin", "linkedin_guest")
        await upsert_discovered(db, board, [raw(external_id="1")])
        await upsert_discovered(db, board, [raw(external_id="1", title="Senior Robotics Engineer")])
        new = await upsert_discovered(
            db, linkedin, [raw(external_id="li", title="Senior Robotics Engineer")]
        )
        assert new == [] and await count(db, Job) == 1
        job = (await db.execute(select(Job))).scalar_one()
        assert job.title == "Senior Robotics Engineer"
        assert job.dedup_hash == store.dedup_key("ACME", "Senior Robotics Engineer", "Paris")


async def test_retitle_onto_another_rows_hash_keeps_both_rows_unchanged():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        await upsert_discovered(db, src, [raw(external_id="1"), raw(external_id="2", title="B")])
        new = await upsert_discovered(db, src, [raw(external_id="2")])  # "2" now reads like "1"
        await db.flush()  # a hash collision would raise here
        assert new == []
        titles = (await db.execute(select(Job.title).order_by(Job.id))).scalars().all()
        assert titles == ["Robotics Engineer", "B"]


async def test_same_source_merge_by_hash_is_logged(caplog):
    """Known miss (accepted): one source, two IDs, same company/title/location -> one row."""
    async with AsyncSessionLocal() as db:
        src = await source(db)
        other = await source(db, "linkedin", "linkedin_guest")
        with caplog.at_level(logging.INFO, logger=store.__name__):
            await upsert_discovered(db, src, [raw(external_id="a")])
            await upsert_discovered(db, src, [raw(external_id="a")])
            await upsert_discovered(db, other, [raw(external_id="li")])  # cross-source: silent
            assert caplog.records == []
            new = await upsert_discovered(db, src, [raw(external_id="b")])
        assert new == [] and await count(db, Job) == 1
        (record,) = caplog.records
        assert "'b'" in record.getMessage() and "'a'" in record.getMessage()


async def test_duplicates_inside_one_poll_insert_once():
    async with AsyncSessionLocal() as db:
        src = await source(db)
        new = await upsert_discovered(
            db,
            src,
            [
                raw(external_id="a"),
                raw(external_id="a"),  # same ID
                raw(external_id="b"),  # same hash, other ID: merged — the accepted miss
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
        (job,) = await upsert_discovered(db, src, [raw(external_id="a", description=POSTING)])
        (match,) = await match_new(db, [job], filters, 0)
        await db.commit()
    assert match.id is not None and match.job_id == job.id
    assert (match.status, match.batch_date) == ("new", date(2026, 11, 14))
    # no CV: only the skills the keywords name are scored, and listed
    assert match.keyword_hits == {"python": True, "ros": True}
    assert match.score == JobMatcher().score(raw_to_details(job), filters) > 0
    assert match.score_basis == JobMatcher().score_basis


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


async def test_match_new_matches_a_repost_under_another_company_once():
    """A tracking board carries the same posting under another company name."""
    filters = JobFilters()
    async with AsyncSessionLocal() as db:
        src = await source(db)
        first, repost, other_city = await upsert_discovered(
            db,
            src,
            [
                raw(external_id="a", company="Artefact", description=POSTING),
                raw(external_id="b", company="LinkedIn Job Wrapping", description=POSTING),
                raw(external_id="c", company="Artefact", location="Lyon", description=POSTING),
            ],
        )
        created = await match_new(db, [first, repost, other_city], filters, 0)
        assert [m.job_id for m in created] == [first.id, other_city.id]
        again = (
            await upsert_discovered(
                db, src, [raw(external_id="d", company="WTTJ", description=POSTING)]
            )
        )[0]
        assert await match_new(db, [again], filters, 0) == []


async def test_match_new_scores_description_less_card_provisionally():
    """LinkedIn and SmartRecruiters cards have no description: the title is scored."""
    filters = JobFilters(keywords=["robotics", "software engineer", "python"])
    async with AsyncSessionLocal() as db:
        # the owner's keywords are skills, also those the curated list does not know
        db.add(SearchSettings(keywords={"include": filters.keywords}))
        src = await source(db)
        hit, miss = await upsert_discovered(
            db,
            src,
            [
                raw(external_id="a", title="Robotics Software Engineer Python"),
                raw(external_id="b", title="Chef de rayon"),
            ],
        )
        (match,) = await match_new(db, [hit, miss], filters, 30)
        assert match.job_id == hit.id
        assert match.score == 50
        assert match.keyword_hits == {"python": True, "robotics": True, "software engineer": True}


async def test_match_new_stores_what_the_score_rests_on():
    """A title-only score and an unproven location are kept on the match, for the UI."""
    filters = JobFilters(keywords=["robotics"], locations=["France"])
    async with AsyncSessionLocal() as db:
        src = await source(db)
        jobs = await upsert_discovered(
            db,
            src,
            [
                raw(external_id="card", company="A", description=""),
                raw(external_id="town", company="B", description=POSTING, location="Massy"),
            ],
        )
        card, town = await match_new(db, jobs, filters, 0)
        await db.commit()
        assert (card.provisional, card.location_unverified) == (True, False)
        assert (town.provisional, town.location_unverified) == (False, True)


async def test_match_new_skips_filtered_job():
    """A job failing a hard filter is not stored, even with a threshold of 0."""
    filters = JobFilters(keywords=["robotics", "python"], locations=["Paris"])
    async with AsyncSessionLocal() as db:
        src = await source(db)
        (job,) = await upsert_discovered(
            db, src, [raw(external_id="a", location="Lyon", description=POSTING)]
        )
        assert await match_new(db, [job], filters, 0) == []
        assert await count(db, JobMatch) == 0
        # the same job passes once the city is not filtered on
        assert len(await match_new(db, [job], JobFilters(keywords=["python"]), 0)) == 1


async def test_match_new_does_not_match_a_job_without_any_of_the_keywords():
    """What the removed keyword-hit gate was for: the score alone now keeps it out."""
    filters = JobFilters(keywords=["robotics", "python"], locations=["France", "Paris"])
    async with AsyncSessionLocal() as db:
        src = await source(db)
        (job,) = await upsert_discovered(
            db,
            src,
            [
                raw(
                    external_id="a",
                    title="Account Executive",
                    location="New York",
                    description="Sell things. " * 10,
                    posted_at=datetime.now() - timedelta(days=29),
                )
            ],
        )
        assert await match_new(db, [job], filters, 30) == []


async def test_match_new_scores_with_the_matcher_it_is_given():
    filters = JobFilters(keywords=["rust"])
    async with AsyncSessionLocal() as db:
        src = await source(db)
        (job,) = await upsert_discovered(db, src, [raw(external_id="a", description=POSTING)])
        matcher = JobMatcher(cv_text="Python, ROS 2")
        (match,) = await match_new(db, [job], filters, 0, matcher)
    assert match.keyword_hits == {"python": True, "ros": True, "robotics": True}
    assert match.score_basis == matcher.score_basis != JobMatcher().score_basis


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


async def test_batch_path_does_not_return_a_rejected_job(monkeypatch):
    """A rejected job must not take a top-N CV slot on every later batch."""
    runner = _runner()
    ranked = [(JobDetails(title="Robotics Engineer", company="ACME", location="Paris"), 80.0)]

    _freeze_batch_day(monkeypatch, date(2026, 11, 14))
    async with AsyncSessionLocal() as db:
        (first,) = await runner._store_matches(db, ranked, JobFilters())
        (await db.get(JobMatch, first.match_id)).status = "rejected"
        await db.commit()
    _freeze_batch_day(monkeypatch, date(2026, 11, 15))
    async with AsyncSessionLocal() as db:
        assert await runner._store_matches(db, ranked, JobFilters()) == []
        assert await count(db, JobMatch) == 1


async def test_batch_path_finds_a_monitor_job_despite_case_and_spacing():
    """The batch runner hashes with ``dedup_key``, not its former un-normalised md5."""
    async with AsyncSessionLocal() as db:
        src = await source(db)
        jobs = await upsert_discovered(db, src, [raw(external_id="a")])
        (match,) = await match_new(db, jobs, JobFilters(), 0)
        await db.commit()
    dirty = JobDetails(title="robotics  engineer ", company="acme", location="PARIS")
    async with AsyncSessionLocal() as db:
        (stored,) = await _runner()._store_matches(db, [(dirty, 80.0)], JobFilters())
        assert stored.match_id == match.id
        assert (await count(db, Job), await count(db, JobMatch)) == (1, 1)


def test_batch_runner_uses_the_shared_conversion():
    assert not hasattr(BatchRunner, "_raw_to_details")
    details = raw_to_details(raw(external_id="x", description="d"))
    assert (details.title, details.company, details.description) == (
        "Robotics Engineer",
        "ACME",
        "d",
    )
