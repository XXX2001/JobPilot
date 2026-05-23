from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text

from backend.database import AsyncSessionLocal, engine, init_db


@pytest.fixture(autouse=True)
async def _init_db_for_each_test():
    await init_db()
    yield


async def test_gmail_tables_created():
    """init_db creates all three Gmail tables."""
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
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
    from backend.models.gmail import GmailCredential

    async with AsyncSessionLocal() as session:
        row = GmailCredential(
            email_address="user@example.com",
            encrypted_refresh_token="enc-token-blob",
            scopes="https://www.googleapis.com/auth/gmail.readonly",
        )
        session.add(row)
        await session.commit()

        result = await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "user@example.com")
        )
        loaded = result.scalar_one()
        assert loaded.encrypted_refresh_token == "enc-token-blob"
        assert loaded.enabled is True
        assert loaded.history_id is None


async def test_gmail_message_unique_constraint():
    """Inserting the same gmail_message_id twice raises."""
    from sqlalchemy.exc import IntegrityError

    from backend.models.gmail import GmailMessage

    async with AsyncSessionLocal() as session:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(GmailMessage(
            gmail_message_id="m-1", gmail_thread_id="t-1",
            account_email="u@e.com", from_address="r@ats.io", from_domain="ats.io",
            received_at=now,
        ))
        await session.commit()

        session.add(GmailMessage(
            gmail_message_id="m-1", gmail_thread_id="t-1",
            account_email="u@e.com", from_address="r@ats.io", from_domain="ats.io",
            received_at=now,
        ))
        with pytest.raises(IntegrityError):
            await session.commit()
