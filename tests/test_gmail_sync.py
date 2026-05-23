from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import save_credential
from backend.gmail.sync import GmailSyncWorker
from backend.models.gmail import GmailCredential, GmailMessage


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def _seed_credential(email: str = "sync-u@e.com") -> None:
    async with AsyncSessionLocal() as session:
        await save_credential(session, email, "rt", ["gmail.readonly"])
        await session.commit()


def _msg_payload(mid: str, thread: str, sender: str, subject: str, snippet: str = "..."):
    return {
        "id": mid,
        "threadId": thread,
        "snippet": snippet,
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
            {"name": "To", "value": "user@example.com"},
        ]},
        "internalDate": "1748000000000",
    }


class _FakeClient:
    """Async-context-manager-shaped stub for backend.gmail.client.GmailRestClient."""

    def __init__(self, *, list_pages, history_pages, get_messages):
        self._list_pages = list_pages
        self._history_pages = history_pages
        self._get = get_messages
        self.history_calls: list[str] = []

    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return None

    async def messages_list(self, q: str | None = None, page_token: str | None = None):
        return self._list_pages.pop(0)

    async def history_list(self, start_history_id: str, page_token: str | None = None):
        self.history_calls.append(start_history_id)
        return self._history_pages.pop(0)

    async def messages_get(self, message_id: str):
        return self._get[message_id]


async def test_first_run_backfills_from_messages_list():
    await _seed_credential("sync-u1@e.com")

    fake_msgs = {
        "m-sync-1": _msg_payload("m-sync-1", "t-sync-1", "no-reply@greenhouse.io", "We received your application"),
        "m-sync-2": _msg_payload("m-sync-2", "t-sync-2", "friend@gmail.com", "lunch?"),
    }
    fake = _FakeClient(
        list_pages=[{"messages": [{"id": "m-sync-1"}, {"id": "m-sync-2"}], "nextPageToken": None,
                     "historyId": "12345"}],
        history_pages=[],
        get_messages=fake_msgs,
    )

    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("sync-u1@e.com")
    assert synced == 2

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(GmailMessage).where(GmailMessage.account_email == "sync-u1@e.com")
        )).scalars().all()
        assert {r.gmail_message_id for r in rows} == {"m-sync-1", "m-sync-2"}
        m1 = next(r for r in rows if r.gmail_message_id == "m-sync-1")
        assert m1.category == "ats_ack"
        assert m1.classified_by == "heuristic"
        assert m1.from_domain == "greenhouse.io"
        cred = (await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "sync-u1@e.com")
        )).scalar_one()
        assert cred.history_id == "12345"
        assert cred.last_synced_at is not None


async def test_second_run_uses_history_list_delta():
    await _seed_credential("sync-u2@e.com")
    # Pre-seed history_id so we go down the delta path
    async with AsyncSessionLocal() as session:
        cred = (await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "sync-u2@e.com")
        )).scalar_one()
        cred.history_id = "100"
        await session.commit()

    fake = _FakeClient(
        list_pages=[],
        history_pages=[{
            "history": [{"messagesAdded": [{"message": {"id": "m-sync-3", "threadId": "t-sync-3"}}]}],
            "historyId": "150",
        }],
        get_messages={"m-sync-3": _msg_payload("m-sync-3", "t-sync-3", "recruiter@acme.com",
                                                "Interview invitation — next steps")},
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("sync-u2@e.com")
    assert synced == 1
    assert fake.history_calls == ["100"]

    async with AsyncSessionLocal() as session:
        m3 = (await session.execute(
            select(GmailMessage).where(GmailMessage.gmail_message_id == "m-sync-3")
        )).scalar_one()
        assert m3.category == "interview_invite"


async def test_dedup_via_unique_constraint_doesnt_crash():
    """Re-syncing a message we've already stored is a no-op, not a crash."""
    await _seed_credential("sync-u3@e.com")
    fake_msgs = {"m-sync-4": _msg_payload("m-sync-4", "t-sync-4", "no-reply@greenhouse.io", "We received your app")}
    fake_a = _FakeClient(
        list_pages=[{"messages": [{"id": "m-sync-4"}], "nextPageToken": None, "historyId": "10"}],
        history_pages=[], get_messages=fake_msgs,
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake_a):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        await worker.sync_now("sync-u3@e.com")

    # Pretend Gmail re-served the same message via history.list (it does this when
    # labels change). The second sync MUST swallow the IntegrityError.
    fake_b = _FakeClient(
        list_pages=[],
        history_pages=[{"history": [{"messagesAdded": [{"message": {"id": "m-sync-4", "threadId": "t-sync-4"}}]}],
                        "historyId": "20"}],
        get_messages=fake_msgs,
    )
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake_b):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        synced = await worker.sync_now("sync-u3@e.com")
    assert synced == 0  # zero NEW rows


async def test_sync_skipped_when_credential_disabled():
    await _seed_credential("sync-u4@e.com")
    async with AsyncSessionLocal() as session:
        cred = (await session.execute(
            select(GmailCredential).where(GmailCredential.email_address == "sync-u4@e.com")
        )).scalar_one()
        cred.enabled = False
        await session.commit()

    fake = _FakeClient(list_pages=[], history_pages=[], get_messages={})
    with patch("backend.gmail.sync.GmailRestClient", return_value=fake):
        worker = GmailSyncWorker(token_manager=AsyncMock())
        synced = await worker.sync_now("sync-u4@e.com")
    assert synced == 0
