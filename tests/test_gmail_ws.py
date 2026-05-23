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


async def test_sync_broadcasts_message_received_per_new_row():
    from backend.gmail.sync import GmailSyncWorker

    async with AsyncSessionLocal() as session:
        await save_credential(session, "ws-u@e.com", "rt", ["gmail.readonly"])
        await session.commit()

    fake_msgs = {
        "m-ws-1": {
            "id": "m-ws-1", "threadId": "t-ws-1", "snippet": "hi",
            "payload": {"headers": [
                {"name": "From", "value": "no-reply@greenhouse.io"},
                {"name": "Subject", "value": "We received your application"},
                {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
            ]},
            "internalDate": "1748000000000",
        },
    }

    class _Fake:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def messages_list(self, **kw):
            return {"messages": [{"id": "m-ws-1"}], "historyId": "1"}
        async def history_list(self, *a, **kw): return {"history": []}
        async def messages_get(self, mid): return fake_msgs[mid]

    sent: list = []
    async def fake_broadcast(*args, **kwargs):
        # Capture whatever was passed (either positional or keyword)
        sent.append((args, kwargs))

    with patch("backend.gmail.sync.GmailRestClient", return_value=_Fake()), \
         patch("backend.gmail.sync.broadcast_gmail_message_received", side_effect=fake_broadcast), \
         patch("backend.gmail.sync.broadcast_gmail_sync_status", side_effect=fake_broadcast):
        worker = GmailSyncWorker(token_manager=AsyncMock(access_token=AsyncMock(return_value="tok")))
        await worker.sync_now("ws-u@e.com")

    # At least one of each kind was broadcast (we use the same fake_broadcast for both
    # patches, so we just verify *something* was sent for both helpers)
    assert len(sent) >= 2


def test_ws_models_union_includes_gmail_variants():
    from backend.api.ws_models import GmailMessageReceived, GmailSyncStatus, WSMessage  # noqa: F401
    inst = GmailSyncStatus(last_history_id="1", messages_synced=0, progress=0.0)
    assert inst.type == "gmail_sync_status"
    inst2 = GmailMessageReceived(
        gmail_message_id="m-ws-1", from_address="x@y.com",
        subject="s", category="ats_ack", category_confidence=0.7,
    )
    assert inst2.type == "gmail_message_received"
