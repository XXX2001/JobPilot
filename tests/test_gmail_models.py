"""Gmail model / schema tests — refactored to use ``tests/factories``.

T8 dropped the per-file ``_init_db_for_each_test`` fixture: the
session-scoped ``_bootstrap_test_db`` in ``conftest.py`` runs ``init_db()``
exactly once per worker, and the autouse per-test wipe keeps the tables
empty without a teardown.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.database import AsyncSessionLocal, engine
from backend.models.gmail import GmailCredential, GmailMessage

from tests.factories import make_gmail_message


async def test_gmail_tables_created():
    """init_db creates all three Gmail tables."""
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    assert "gmail_credentials" in names
    assert "gmail_messages" in names
    assert "application_correspondence" in names


async def test_applications_last_correspondence_at_column_added():
    """_migrate_add_columns adds last_correspondence_at to existing applications table."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(applications)"))
        cols = {row[1] for row in result.fetchall()}
    assert "last_correspondence_at" in cols


async def test_gmail_credential_roundtrip():
    """A GmailCredential row roundtrips through the session."""
    async with AsyncSessionLocal() as session:
        row = GmailCredential(
            email_address="user@example.com",
            encrypted_refresh_token="enc-token-blob",
            scopes="https://www.googleapis.com/auth/gmail.readonly",
        )
        session.add(row)
        await session.commit()

        result = await session.execute(
            select(GmailCredential).where(
                GmailCredential.email_address == "user@example.com"
            )
        )
        loaded = result.scalar_one()
        assert loaded.encrypted_refresh_token == "enc-token-blob"
        assert loaded.enabled is True
        assert loaded.history_id is None


async def test_gmail_message_unique_constraint():
    """Inserting the same gmail_message_id twice raises."""
    async with AsyncSessionLocal() as session:
        session.add(make_gmail_message(gmail_message_id="m-1", gmail_thread_id="t-1"))
        await session.commit()

        session.add(make_gmail_message(gmail_message_id="m-1", gmail_thread_id="t-1"))
        with pytest.raises(IntegrityError):
            await session.commit()
