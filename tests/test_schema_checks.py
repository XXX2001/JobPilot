"""N2 — enum-like CHECK constraints are enforced + legacy status migration.

Two layers of coverage:

* **Async constraint-rejection tests** (mirroring ``tests/test_db_integrity.py``):
  raw ``INSERT`` of an out-of-vocab value must raise ``IntegrityError``; a
  valid value inserts cleanly. The test DB is built by ``init_db()`` →
  ``alembic upgrade head``, so these exercise the CHECKs added by the
  ``t2b1_enum_checks`` migration, not just the model ``__table_args__``.

* **A sync legacy-migration test** (mirroring ``tests/test_migrations.py``):
  upgrade a temp DB to the pre-N2 head, seed a row carrying the legacy
  ``status='manual'`` value, upgrade to head, and assert the row was
  normalised to ``'applied'`` by ``upgrade()`` step 1.
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

# Pre-N2 Alembic head — the revision this migration's ``down_revision`` points at.
_PRE_N2_REVISION = "e5a65a3427cf"


# ── Async constraint-rejection helpers ──────────────────────────────────────


async def _expect_rejected(sql: str) -> None:
    """A raw INSERT that violates a CHECK must raise ``IntegrityError``."""
    async with AsyncSessionLocal() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(sql))
            await session.commit()


async def _expect_accepted(sql: str) -> None:
    """A raw INSERT with valid values must commit cleanly."""
    async with AsyncSessionLocal() as session:
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


async def _seed_application() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO applications (id, method, status, created_at) "
                "VALUES (1, 'manual', 'applied', datetime('now'))"
            )
        )
        await session.commit()


async def _seed_gmail_message() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO gmail_messages "
                "(id, gmail_message_id, gmail_thread_id, account_email, "
                "from_address, from_domain, received_at, created_at, category) "
                "VALUES (1, 'm1', 't1', 'a@b.com', 'x@y.com', 'y.com', "
                "datetime('now'), datetime('now'), 'noise')"
            )
        )
        await session.commit()


# ── applications.status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_applications_status_rejects_out_of_vocab():
    await _expect_rejected(
        "INSERT INTO applications (method, status, created_at) "
        "VALUES ('manual', 'bogus', datetime('now'))"
    )


@pytest.mark.asyncio
async def test_applications_status_accepts_valid():
    await _expect_accepted(
        "INSERT INTO applications (method, status, created_at) "
        "VALUES ('manual', 'applied', datetime('now'))"
    )


# ── applications.method ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_applications_method_rejects_out_of_vocab():
    await _expect_rejected(
        "INSERT INTO applications (method, status, created_at) "
        "VALUES ('robot', 'pending', datetime('now'))"
    )


@pytest.mark.asyncio
async def test_applications_method_accepts_valid():
    await _expect_accepted(
        "INSERT INTO applications (method, status, created_at) "
        "VALUES ('auto', 'pending', datetime('now'))"
    )


# ── job_matches.status ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_matches_status_rejects_out_of_vocab():
    await _seed_job()
    await _expect_rejected(
        "INSERT INTO job_matches (job_id, score, status, matched_at) "
        "VALUES (1, 0.5, 'bogus', datetime('now'))"
    )


@pytest.mark.asyncio
async def test_job_matches_status_accepts_valid():
    await _seed_job()
    await _expect_accepted(
        "INSERT INTO job_matches (job_id, score, status, matched_at) "
        "VALUES (1, 0.5, 'selected', datetime('now'))"
    )


# ── tailored_documents.doc_type ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tailored_documents_doc_type_rejects_out_of_vocab():
    await _seed_job_match()
    await _expect_rejected(
        "INSERT INTO tailored_documents (job_match_id, doc_type, created_at) "
        "VALUES (1, 'bogus', datetime('now'))"
    )


@pytest.mark.asyncio
async def test_tailored_documents_doc_type_accepts_valid():
    await _seed_job_match()
    await _expect_accepted(
        "INSERT INTO tailored_documents (job_match_id, doc_type, created_at) "
        "VALUES (1, 'letter', datetime('now'))"
    )


# ── application_correspondence.direction ─────────────────────────────────────


@pytest.mark.asyncio
async def test_application_correspondence_direction_rejects_out_of_vocab():
    await _seed_application()
    await _seed_gmail_message()
    await _expect_rejected(
        "INSERT INTO application_correspondence "
        "(application_id, message_id, gmail_thread_id, direction, "
        "link_confidence, link_method, confirmed_by_user, created_at) "
        "VALUES (1, 1, 't1', 'sideways', 1.0, 'manual', 0, datetime('now'))"
    )


@pytest.mark.asyncio
async def test_application_correspondence_direction_accepts_valid():
    await _seed_application()
    await _seed_gmail_message()
    await _expect_accepted(
        "INSERT INTO application_correspondence "
        "(application_id, message_id, gmail_thread_id, direction, "
        "link_confidence, link_method, confirmed_by_user, created_at) "
        "VALUES (1, 1, 't1', 'outbound', 1.0, 'manual', 0, datetime('now'))"
    )


# ── gmail_messages.category (NULLABLE — NULL must be allowed) ─────────────────


@pytest.mark.asyncio
async def test_gmail_messages_category_rejects_out_of_vocab():
    await _expect_rejected(
        "INSERT INTO gmail_messages "
        "(gmail_message_id, gmail_thread_id, account_email, from_address, "
        "from_domain, received_at, created_at, category) "
        "VALUES ('m1', 't1', 'a@b.com', 'x@y.com', 'y.com', "
        "datetime('now'), datetime('now'), 'bogus')"
    )


@pytest.mark.asyncio
async def test_gmail_messages_category_accepts_valid():
    await _expect_accepted(
        "INSERT INTO gmail_messages "
        "(gmail_message_id, gmail_thread_id, account_email, from_address, "
        "from_domain, received_at, created_at, category) "
        "VALUES ('m1', 't1', 'a@b.com', 'x@y.com', 'y.com', "
        "datetime('now'), datetime('now'), 'interview_invite')"
    )


@pytest.mark.asyncio
async def test_gmail_messages_category_accepts_null():
    await _expect_accepted(
        "INSERT INTO gmail_messages "
        "(gmail_message_id, gmail_thread_id, account_email, from_address, "
        "from_domain, received_at, created_at, category) "
        "VALUES ('m2', 't2', 'a@b.com', 'x@y.com', 'y.com', "
        "datetime('now'), datetime('now'), NULL)"
    )


# ── search_settings.cv_modification_sensitivity ──────────────────────────────


@pytest.mark.asyncio
async def test_search_settings_cv_sensitivity_rejects_out_of_vocab():
    await _expect_rejected(
        "INSERT INTO search_settings "
        "(id, keywords, remote_only, min_match_score, daily_limit, "
        "cv_modification_sensitivity) "
        "VALUES (1, '{}', 0, 30.0, 10, 'reckless')"
    )


@pytest.mark.asyncio
async def test_search_settings_cv_sensitivity_accepts_valid():
    await _expect_accepted(
        "INSERT INTO search_settings "
        "(id, keywords, remote_only, min_match_score, daily_limit, "
        "cv_modification_sensitivity) "
        "VALUES (1, '{}', 0, 30.0, 10, 'aggressive')"
    )


# ── Legacy status migration (sync, via Alembic command API) ──────────────────


def _alembic_config(db_url: str):
    """Mirror ``tests/test_migrations.py`` — async URL for the upgrade path."""
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_legacy_manual_status_is_migrated_to_applied():
    """A row with the legacy ``status='manual'`` becomes ``'applied'`` at head."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cfg = _alembic_config(f"sqlite+aiosqlite:///{tmp.name}")

        # Upgrade to the pre-N2 head, where applications.status has no CHECK.
        command.upgrade(cfg, _PRE_N2_REVISION)

        # Seed a legacy row via a plain sync connection.
        conn = sqlite3.connect(tmp.name)
        try:
            conn.execute(
                "INSERT INTO applications (id, method, status, created_at) "
                "VALUES (1, 'manual', 'manual', datetime('now'))"
            )
            conn.commit()
        finally:
            conn.close()

        # Upgrade to head — the N2 migration normalises legacy aliases first.
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(tmp.name)
        try:
            row = conn.execute(
                "SELECT status FROM applications WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()

    assert row is not None, "seeded application row vanished after upgrade"
    assert row[0] == "applied", f"legacy status not migrated: {row[0]!r}"
