"""Monitor schema (m01): job identity is (source_id, external_id); poll state on sources."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from backend.database import AsyncSessionLocal
from backend.models.job import Job, JobSource
from backend.models.schemas import RawJob
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


def test_raw_job_accepts_long_descriptions():
    assert len(RawJob(title="t", company="c", description="x" * 20000).description) == 20000
    with pytest.raises(ValueError):
        RawJob(title="t", company="c", description="x" * 20001)
