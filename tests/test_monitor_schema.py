"""Monitor schema (m01): job identity is (source_id, external_id); poll state on sources."""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from backend.database import AsyncSessionLocal
from backend.models.job import Job, JobSource
from backend.models.schemas import RawJob
from backend.scraping.deduplicator import dedup_key
from tests.test_migrations import _alembic_config


async def _source(session, name: str = "gh-acme") -> int:
    src = JobSource(name=name, type="greenhouse")
    session.add(src)
    await session.flush()
    return src.id


def _job(source_id: int | None, external_id: str | None, n: int) -> Job:
    return Job(
        source_id=source_id,
        external_id=external_id,
        title=f"T{n}",
        company="C",
        url=f"https://example.com/{n}",
        dedup_hash=f"h{n}",
    )


async def test_source_external_id_is_unique():
    async with AsyncSessionLocal() as session:
        sid = await _source(session)
        session.add_all([_job(sid, "42", 1), _job(sid, "42", 2)])
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_same_external_id_on_another_source_is_allowed():
    async with AsyncSessionLocal() as session:
        a = await _source(session, "a")
        b = await _source(session, "b")
        session.add_all([_job(a, "42", 1), _job(b, "42", 2)])
        await session.commit()


@pytest.mark.parametrize("external_id", [None, ""])
async def test_missing_external_id_is_not_constrained(external_id):
    async with AsyncSessionLocal() as session:
        sid = await _source(session)
        session.add_all([_job(sid, external_id, 1), _job(sid, external_id, 2)])
        await session.commit()


async def test_poll_state_defaults():
    async with AsyncSessionLocal() as session:
        sid = await _source(session)
        session.add(_job(sid, "1", 1))
        await session.commit()
        src = (await session.execute(select(JobSource))).scalar_one()
        job = (await session.execute(select(Job))).scalar_one()
    assert src.fail_count == 0
    assert (src.poll_interval_s, src.next_poll_at, src.etag) == (None, None, None)
    assert (src.last_error, src.window_hours) == (None, None)
    assert (job.first_seen_at, job.last_seen_at) == (None, None)


def test_next_poll_at_is_indexed(tmp_path):
    db = tmp_path / "m01.db"
    command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")
    eng = create_engine(f"sqlite:///{db}")
    indexed = {tuple(i["column_names"]) for i in inspect(eng).get_indexes("job_sources")}
    eng.dispose()
    assert ("next_poll_at",) in indexed


def test_m01_upgrades_existing_rows_and_downgrades(tmp_path):
    """Existing sources get fail_count 0; the migration is reversible."""
    db = tmp_path / "m01.db"
    cfg = _alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "w11_quota_reservation")
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO job_sources (id, name, type, enabled, created_at) "
            "VALUES (1, 'old', 'rss', 1, '2026-01-01')"
        ))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert c.execute(text("SELECT fail_count FROM job_sources")).scalar() == 0
    command.downgrade(cfg, "w11_quota_reservation")
    cols = {c["name"] for c in inspect(eng).get_columns("job_sources")}
    job_cols = {c["name"] for c in inspect(eng).get_columns("jobs")}
    eng.dispose()
    assert not cols & {"poll_interval_s", "next_poll_at", "etag", "fail_count", "last_error"}
    assert not job_cols & {"first_seen_at", "last_seen_at"}


def _legacy_hash(company, title, location) -> str:
    """What the batch runner and /api/jobs/search stored before ``dedup_key``."""
    return hashlib.md5(f"{company}|{title}|{location}".lower().encode()).hexdigest()


def test_m01_rehashes_legacy_rows_so_they_are_still_found(tmp_path):
    """Stored hashes follow ``dedup_key``; a twin row hands its matches to the hash holder."""
    db = tmp_path / "m01.db"
    cfg = _alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "w11_quota_reservation")
    rows = {  # id -> company, title, location
        1: ("ACME ", "Robotics  Engineer", None),  # whitespace-dirty, applied
        2: ("Globex", "Dev", "Paris"),  # clean: hash already right
        3: ("Globex", " Dev", "Paris"),  # dirty twin of 2, applied
        4: ("Globex", "Dev ", "Paris"),  # another twin; its match date clashes with 2's
    }
    matches = [  # id, job_id, status, batch_date
        (1, 1, "applied", "2026-11-01"),
        (2, 2, "new", "2026-11-01"),
        (3, 3, "applied", "2026-11-02"),
        (4, 4, "skipped", "2026-11-01"),
    ]
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        for i, (company, title, location) in rows.items():
            c.execute(
                text(
                    "INSERT INTO jobs (id, title, company, location, url, dedup_hash, scraped_at)"
                    " VALUES (:i, :t, :c, :l, 'u', :h, '2026-01-01')"
                ),
                {"i": i, "t": title, "c": company, "l": location,
                 "h": _legacy_hash(company, title, location)},
            )
        for i, job_id, status, day in matches:
            c.execute(
                text(
                    "INSERT INTO job_matches (id, job_id, score, status, batch_date, matched_at)"
                    " VALUES (:i, :j, 50, :s, :d, '2026-01-01')"
                ),
                {"i": i, "j": job_id, "s": status, "d": day},
            )

    def state():
        with eng.connect() as c:
            hashes = dict(c.execute(text("SELECT id, dedup_hash FROM jobs")).fetchall())
            owners = dict(c.execute(text("SELECT id, job_id FROM job_matches")).fetchall())
        return hashes, owners

    command.upgrade(cfg, "head")
    hashes, owners = state()
    assert hashes[1] == dedup_key("ACME", "Robotics Engineer", None)
    assert hashes[2] == dedup_key("Globex", "Dev", "Paris")
    # the twins cannot share the unique hash: they keep theirs, job 2 is the one found
    assert (hashes[3], hashes[4]) == (_legacy_hash(*rows[3]), _legacy_hash(*rows[4]))
    # ... and it carries the applied match, so the job is not queued again
    assert owners == {1: 1, 2: 2, 3: 2, 4: 4}

    command.downgrade(cfg, "w11_quota_reservation")
    hashes, _ = state()
    eng.dispose()
    assert hashes == {i: _legacy_hash(*r) for i, r in rows.items()}


def test_raw_job_accepts_long_descriptions():
    assert len(RawJob(title="t", company="c", description="x" * 20000).description) == 20000
    with pytest.raises(ValueError):
        RawJob(title="t", company="c", description="x" * 20001)
