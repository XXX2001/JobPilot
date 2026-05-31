"""N2 — NOT NULL + conditional CHECK on always-set columns.

Two layers of coverage, mirroring ``tests/test_schema_checks.py`` and
``tests/test_schema_unique.py``:

* **Async constraint-rejection tests** (mirroring ``tests/test_db_integrity.py``):
  a raw ``INSERT`` of a NULL into a now-NOT-NULL column (or a row violating
  the conditional CHECK on ``applications``) must raise ``IntegrityError``;
  a valid row inserts cleanly. The test DB is built by ``init_db()`` →
  ``alembic upgrade head``, so these exercise the constraints added by the
  ``t2b3_not_null`` migration, not just the model ``__table_args__``.

* **A sync migration test** (mirroring ``tests/test_migrations.py``): upgrade
  a temp DB to the pre-N2-T3 head, seed rows that violate the new invariants
  (a ``jobs`` row with NULL ``dedup_hash``, an orphan ``tailored_documents``
  row with NULL ``job_match_id``, and an ``auto``/NULL ``applications`` row),
  upgrade to head, and assert ``upgrade()`` cleaned each up (backfill / delete
  / normalise) and that a subsequent NULL insert now fails.
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

# Pre-N2-T3 Alembic head — the revision this migration's ``down_revision`` is at.
_PRE_N2T3_REVISION = "t2b2_unique_keys"


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
                "INSERT INTO jobs (id, title, company, url, scraped_at, dedup_hash) "
                "VALUES (1, 'T', 'C', 'https://example.com/1', datetime('now'), 'h1')"
            )
        )
        await session.commit()


async def _seed_job_match() -> None:
    await _seed_job()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO job_matches (id, job_id, score, status, matched_at) "
                "VALUES (1, 1, 0.5, 'new', datetime('now'))"
            )
        )
        await session.commit()


# ── tailored_documents.job_match_id NOT NULL ─────────────────────────────────


@pytest.mark.asyncio
async def test_tailored_documents_job_match_id_rejects_null():
    await _seed_job_match()
    await _expect_rejected(
        [
            "INSERT INTO tailored_documents (job_match_id, doc_type, created_at) "
            "VALUES (NULL, 'cv', datetime('now'))"
        ]
    )


@pytest.mark.asyncio
async def test_tailored_documents_job_match_id_accepts_value():
    await _seed_job_match()
    await _expect_accepted(
        [
            "INSERT INTO tailored_documents (job_match_id, doc_type, created_at) "
            "VALUES (1, 'cv', datetime('now'))"
        ]
    )


# ── jobs.dedup_hash NOT NULL ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jobs_dedup_hash_rejects_null():
    await _expect_rejected(
        [
            "INSERT INTO jobs (title, company, url, scraped_at, dedup_hash) "
            "VALUES ('T', 'C', 'https://example.com/2', datetime('now'), NULL)"
        ]
    )


@pytest.mark.asyncio
async def test_jobs_dedup_hash_accepts_value():
    await _expect_accepted(
        [
            "INSERT INTO jobs (title, company, url, scraped_at, dedup_hash) "
            "VALUES ('T', 'C', 'https://example.com/3', datetime('now'), 'abc123')"
        ]
    )


# ── applications conditional CHECK (method='manual' OR job_match_id NOT NULL) ─


@pytest.mark.asyncio
async def test_applications_manual_without_match_accepted():
    """The legitimate manual-apply path: method='manual', no match — must pass."""
    await _expect_accepted(
        [
            "INSERT INTO applications (method, status, job_match_id, created_at) "
            "VALUES ('manual', 'pending', NULL, datetime('now'))"
        ]
    )


@pytest.mark.asyncio
async def test_applications_auto_without_match_rejected():
    await _expect_rejected(
        [
            "INSERT INTO applications (method, status, job_match_id, created_at) "
            "VALUES ('auto', 'pending', NULL, datetime('now'))"
        ]
    )


@pytest.mark.asyncio
async def test_applications_auto_with_match_accepted():
    await _seed_job_match()
    await _expect_accepted(
        [
            "INSERT INTO applications (method, status, job_match_id, created_at) "
            "VALUES ('auto', 'pending', 1, datetime('now'))"
        ]
    )


# ── application_events.application_id NOT NULL ───────────────────────────────


@pytest.mark.asyncio
async def test_application_events_application_id_rejects_null():
    await _expect_rejected(
        [
            "INSERT INTO application_events (application_id, event_type, event_date) "
            "VALUES (NULL, 'created', datetime('now'))"
        ]
    )


# ── Migration cleanup (sync, via Alembic command API) ────────────────────────


def _alembic_config(db_url: str):
    """Mirror ``tests/test_migrations.py`` — async URL for the upgrade path."""
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_cleans_up_null_columns_then_enforces():
    """The ``t2b3_not_null`` upgrade backfills/deletes/normalises, then enforces."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cfg = _alembic_config(f"sqlite+aiosqlite:///{tmp.name}")

        # Upgrade to the pre-N2-T3 head, where the columns are still nullable.
        command.upgrade(cfg, _PRE_N2T3_REVISION)

        # Seed dirty data via a plain sync connection.
        conn = sqlite3.connect(tmp.name)
        try:
            # A job with a NULL dedup_hash — must be BACKFILLED (not deleted, as
            # deleting would orphan job_matches with FK enforcement off).
            conn.execute(
                "INSERT INTO jobs (id, title, company, location, url, scraped_at, "
                "dedup_hash) VALUES (1, 'Engineer', 'Acme', 'Paris', "
                "'https://example.com/1', datetime('now'), NULL)"
            )
            # A valid match so the orphan tailored_document below is the ONLY
            # NULL-job_match_id row (and not mistaken for a real child).
            conn.execute(
                "INSERT INTO job_matches (id, job_id, score, status, matched_at) "
                "VALUES (1, 1, 0.5, 'new', datetime('now'))"
            )
            # An orphan tailored_document (NULL job_match_id) — must be DELETED.
            conn.execute(
                "INSERT INTO tailored_documents (id, job_match_id, doc_type, "
                "created_at) VALUES (1, NULL, 'cv', datetime('now'))"
            )
            # A non-manual application with no match — must be NORMALISED to
            # method='manual' so the conditional CHECK holds.
            conn.execute(
                "INSERT INTO applications (id, method, status, job_match_id, "
                "created_at) VALUES (1, 'auto', 'pending', NULL, datetime('now'))"
            )
            conn.commit()
        finally:
            conn.close()

        # Upgrade to head — the N2-T3 migration cleans up before constraining.
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(tmp.name)
        try:
            dedup_hash = conn.execute(
                "SELECT dedup_hash FROM jobs WHERE id = 1"
            ).fetchone()
            orphan_doc = conn.execute(
                "SELECT COUNT(*) FROM tailored_documents WHERE id = 1"
            ).fetchone()
            app_method = conn.execute(
                "SELECT method FROM applications WHERE id = 1"
            ).fetchone()

            # The NULL dedup_hash was backfilled deterministically.
            assert dedup_hash is not None and dedup_hash[0] is not None, (
                f"dedup_hash not backfilled: {dedup_hash}"
            )
            # The orphan tailored_document was removed.
            assert orphan_doc == (0,), f"orphan tailored_document survived: {orphan_doc}"
            # The auto/NULL application was normalised to method='manual'.
            assert app_method == ("manual",), (
                f"auto/NULL application not normalised: {app_method}"
            )

            # A subsequent NULL/violating insert must now fail at the DB level.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs (title, company, url, scraped_at, dedup_hash) "
                    "VALUES ('T', 'C', 'https://example.com/x', datetime('now'), NULL)"
                )
                conn.commit()
            conn.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tailored_documents (job_match_id, doc_type, "
                    "created_at) VALUES (NULL, 'cv', datetime('now'))"
                )
                conn.commit()
            conn.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO applications (method, status, job_match_id, "
                    "created_at) VALUES ('auto', 'pending', NULL, datetime('now'))"
                )
                conn.commit()
        finally:
            conn.close()
