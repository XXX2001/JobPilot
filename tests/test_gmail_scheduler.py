from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import save_credential
from backend.models.gmail import GmailCredential


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_scheduler_invokes_sync_for_every_enabled_credential():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "sched-a@e.com", "rt1", ["gmail.readonly"])
        await save_credential(session, "sched-b@e.com", "rt2", ["gmail.readonly"])
        await session.commit()

    mock_sync = AsyncMock(return_value=2)
    with patch("backend.main.GmailSyncWorker") as Worker:
        Worker.return_value.sync_now = mock_sync
        from backend.main import _run_gmail_poll
        await _run_gmail_poll()

    assert {c.args[0] for c in mock_sync.await_args_list} >= {"sched-a@e.com", "sched-b@e.com"}


async def test_scheduler_skips_disabled_credentials():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "sched-c@e.com", "rt", ["gmail.readonly"])
        await session.commit()
        cred = (await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "sched-c@e.com")
        )).scalar_one()
        cred.enabled = False
        await session.commit()

    mock_sync = AsyncMock()
    with patch("backend.main.GmailSyncWorker") as Worker:
        Worker.return_value.sync_now = mock_sync
        from backend.main import _run_gmail_poll
        await _run_gmail_poll()

    # mock_sync must not have been called for sched-c@e.com
    emails_called = {c.args[0] for c in mock_sync.await_args_list}
    assert "sched-c@e.com" not in emails_called
