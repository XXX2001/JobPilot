"""N2 — unique constraints on natural keys are enforced + migration dedup.

Two layers of coverage, mirroring ``tests/test_schema_checks.py``:

* **Async constraint-rejection tests** (mirroring ``tests/test_db_integrity.py``):
  a raw ``INSERT`` of a duplicate natural key must raise ``IntegrityError``;
  distinct values insert cleanly. The test DB is built by ``init_db()`` →
  ``alembic upgrade head``, so these exercise the unique constraints added by
  the ``t2b2_unique_keys`` migration, not just the model ``__table_args__``.

* **A sync migration-dedup test** (mirroring ``tests/test_migrations.py``):
  upgrade a temp DB to the pre-N2-T2 head, seed duplicate ``job_sources``
  names and duplicate ``(job_id, batch_date)`` job_matches, upgrade to head,
  and assert ``upgrade()`` collapsed/deleted the duplicates while keeping the
  expected survivors, and that a subsequent duplicate insert now fails.
"""
from __future__ import annotations

import sqlite3
import tempfile

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.config import PROJECT_ROOT
from backend.database import AsyncSessionLocal

# Pre-N2-T2 Alembic head — the revision this migration's ``down_revision`` is at.
_PRE_N2T2_REVISION = "t2b1_enum_checks"


# ── Async constraint-rejection helpers ──────────────────────────────────────


async def _expect_rejected(statements: list[str]) -> None:
    """Running ``statements`` in one transaction must raise ``IntegrityError``."""
    async with AsyncSessionLocal() as session:
        with pytest.raises(IntegrityError):
            for sql in statements:
                await session.execute(text(sql))
            await session.commit()


async def _expect_accepted(statements: list[str]) -> None:
    """Running ``statements`` in one transaction must commit cleanly."""
    async with AsyncSessionLocal() as session:
        for sql in statements:
            await session.execute(text(sql))
        await session.commit()


async def _seed_job() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, title, company, url, scraped_at) "
                "VALUES (1, 'T', 'C', 'https://example.com/1', datetime('now'))"
            )
        )
        await session.commit()


# ── job_sources.name UNIQUE ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_sources_name_rejects_duplicate():
    await _expect_rejected(
        [
            "INSERT INTO job_sources (name, type, enabled, created_at) "
            "VALUES ('dup', 'rss', 1, datetime('now'))",
            "INSERT INTO job_sources (name, type, enabled, created_at) "
            "VALUES ('dup', 'rss', 1, datetime('now'))",
        ]
    )


@pytest.mark.asyncio
async def test_job_sources_name_accepts_distinct():
    await _expect_accepted(
        [
            "INSERT INTO job_sources (name, type, enabled, created_at) "
            "VALUES ('alpha', 'rss', 1, datetime('now'))",
            "INSERT INTO job_sources (name, type, enabled, created_at) "
            "VALUES ('beta', 'rss', 1, datetime('now'))",
        ]
    )


# ── (job_matches.job_id, job_matches.batch_date) UNIQUE ──────────────────────


@pytest.mark.asyncio
async def test_job_matches_job_id_batch_date_rejects_duplicate():
    await _seed_job()
    await _expect_rejected(
        [
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.5, 'new', '2026-05-31', datetime('now'))",
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.9, 'new', '2026-05-31', datetime('now'))",
        ]
    )


@pytest.mark.asyncio
async def test_job_matches_job_id_distinct_batch_date_accepted():
    await _seed_job()
    await _expect_accepted(
        [
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.5, 'new', '2026-05-31', datetime('now'))",
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.9, 'new', '2026-06-01', datetime('now'))",
        ]
    )


@pytest.mark.asyncio
async def test_job_matches_null_batch_date_not_deduped():
    """SQLite treats multiple NULLs as DISTINCT — two NULL-batch_date rows OK."""
    await _seed_job()
    await _expect_accepted(
        [
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.5, 'new', NULL, datetime('now'))",
            "INSERT INTO job_matches (job_id, score, status, batch_date, matched_at) "
            "VALUES (1, 0.9, 'new', NULL, datetime('now'))",
        ]
    )


# ── Migration dedup (sync, via Alembic command API) ──────────────────────────


def _alembic_config(db_url: str):
    """Mirror ``tests/test_migrations.py`` — async URL for the upgrade path."""
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_collapses_duplicate_natural_keys():
    """The ``t2b2_unique_keys`` upgrade dedups dirty data, then enforces uniqueness."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cfg = _alembic_config(f"sqlite+aiosqlite:///{tmp.name}")

        # Upgrade to the pre-N2-T2 head, where the natural keys are NOT unique.
        command.upgrade(cfg, _PRE_N2T2_REVISION)

        # Seed dirty data via a plain sync connection.
        conn = sqlite3.connect(tmp.name)
        try:
            # Two job_sources sharing the same name (survivor = smallest id = 1).
            conn.execute(
                "INSERT INTO job_sources (id, name, type, enabled, created_at) "
                "VALUES (1, 'dup', 'rss', 1, datetime('now'))"
            )
            conn.execute(
                "INSERT INTO job_sources (id, name, type, enabled, created_at) "
                "VALUES (2, 'dup', 'rss', 1, datetime('now'))"
            )
            # A job pointing at the loser source (id=2) must be repointed to 1.
            conn.execute(
                "INSERT INTO jobs (id, source_id, title, company, url, scraped_at) "
                "VALUES (1, 2, 'T', 'C', 'https://example.com/1', datetime('now'))"
            )
            # Two job_matches sharing (job_id, batch_date) — survivor = MAX(id) = 11.
            conn.execute(
                "INSERT INTO job_matches (id, job_id, score, status, batch_date, matched_at) "
                "VALUES (10, 1, 0.5, 'new', '2026-05-31', datetime('now'))"
            )
            conn.execute(
                "INSERT INTO job_matches (id, job_id, score, status, batch_date, matched_at) "
                "VALUES (11, 1, 0.9, 'new', '2026-05-31', datetime('now'))"
            )
            conn.commit()
        finally:
            conn.close()

        # Upgrade to head — the N2-T2 migration dedups before constraining.
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(tmp.name)
        try:
            # job_sources: only the survivor (id=1) remains for name 'dup'.
            source_rows = conn.execute(
                "SELECT id FROM job_sources WHERE name = 'dup' ORDER BY id"
            ).fetchall()
            # jobs.source_id was repointed from the loser (2) to the survivor (1).
            repointed = conn.execute(
                "SELECT source_id FROM jobs WHERE id = 1"
            ).fetchone()
            # job_matches: only the survivor (MAX id = 11) remains for the group.
            match_rows = conn.execute(
                "SELECT id FROM job_matches "
                "WHERE job_id = 1 AND batch_date = '2026-05-31' ORDER BY id"
            ).fetchall()

            assert source_rows == [(1,)], f"job_sources not collapsed: {source_rows}"
            assert repointed == (1,), f"jobs.source_id not repointed: {repointed}"
            assert match_rows == [(11,)], f"job_matches not deduped: {match_rows}"

            # A subsequent duplicate insert must now fail at the DB level.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO job_sources (name, type, enabled, created_at) "
                    "VALUES ('dup', 'rss', 1, datetime('now'))"
                )
                conn.commit()
            conn.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO job_matches "
                    "(job_id, score, status, batch_date, matched_at) "
                    "VALUES (1, 0.1, 'new', '2026-05-31', datetime('now'))"
                )
                conn.commit()
        finally:
            conn.close()
