"""End-to-end Phase-1 happy path: OAuth callback → sync → list unlinked
→ link → application detail. All external HTTP is mocked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal, init_db
from backend.models.application import Application


@pytest.fixture
def app_with_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "smoke-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "smoke-secret")
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    # backend.gmail.auth binds `settings` at import time (`from ... import settings`),
    # so the prior import in earlier tests pinned an empty GMAIL_CLIENT_ID. Re-bind
    # it on the module so the live TokenManager sees our patched config.
    import backend.gmail.auth as _gmail_auth
    monkeypatch.setattr(_gmail_auth, "settings", cfg.settings)

    # The test DB is shared across the whole pytest session. Other gmail-tests
    # leave behind GmailCredential / GmailMessage / ApplicationCorrespondence rows
    # that would shadow our connected account in /api/gmail/status (it returns
    # `select(GmailCredential).limit(1)`) and pollute the unlinked list. Wipe
    # those tables before the smoke run so the smoke owns its world.
    async def _wipe():
        from sqlalchemy import delete
        from backend.database import AsyncSessionLocal as _S
        from backend.models.gmail import (
            ApplicationCorrespondence,
            GmailCredential,
            GmailMessage,
        )
        async with _S() as session:
            await session.execute(delete(ApplicationCorrespondence))
            await session.execute(delete(GmailMessage))
            await session.execute(delete(GmailCredential))
            await session.commit()
    asyncio.run(_wipe())

    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_phase_1_happy_path(app_with_gmail: TestClient):
    asyncio.run(init_db())

    # ── 1. seed an application we'll link to later
    async def _seed_app():
        async with AsyncSessionLocal() as session:
            app = Application(
                method="manual", status="applied",
                applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(app)
            await session.commit()
            return app.id
    app_id = asyncio.run(_seed_app())

    # ── 2. OAuth callback (mocked Google)
    start = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    fake_token = httpx.Response(
        200,
        json={
            "access_token": "tok", "refresh_token": "rt",
            "expires_in": 3600, "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        },
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
    )
    fake_profile = httpx.Response(
        200,
        json={"emailAddress": "smoke@example.com"},
        request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
    )

    with patch("backend.api.gmail_auth.httpx.AsyncClient") as MockCB:
        inst = MockCB.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=fake_token)
        inst.get = AsyncMock(return_value=fake_profile)
        cb = app_with_gmail.get(
            f"/api/gmail/oauth/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
    assert cb.status_code in (302, 303), cb.text

    # ── 3. trigger a sync (mock the Gmail REST client + the token-manager refresh)
    fake_msg = {
        "id": "m-smoke", "threadId": "t-smoke", "snippet": "hi",
        "payload": {"headers": [
            {"name": "From", "value": "recruiter@acme.com"},
            {"name": "Subject", "value": "Interview invitation — next steps"},
            {"name": "Date", "value": "Fri, 23 May 2026 10:00:00 +0000"},
        ]},
        "internalDate": "1748000000000",
    }

    class _Fake:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def messages_list(self, **kw):
            return {"messages": [{"id": "m-smoke"}], "historyId": "1"}
        async def history_list(self, *a, **kw): return {"history": []}
        async def messages_get(self, mid): return fake_msg

    with patch("backend.gmail.sync.GmailRestClient", return_value=_Fake()), \
         patch("backend.gmail.auth.httpx.AsyncClient") as MockTok:
        tk = MockTok.return_value.__aenter__.return_value
        tk.post = AsyncMock(return_value=fake_token)
        sync_resp = app_with_gmail.post("/api/gmail/sync")
    assert sync_resp.status_code == 200, sync_resp.text
    assert sync_resp.json()["synced"] == 1

    # ── 4. list unlinked — the new message should appear
    unlinked = app_with_gmail.get("/api/correspondence/unlinked").json()["items"]
    assert any(it["gmail_message_id"] == "m-smoke" for it in unlinked)
    msg_row_id = next(it["id"] for it in unlinked if it["gmail_message_id"] == "m-smoke")

    # ── 5. link it
    link = app_with_gmail.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_row_id,
    })
    assert link.status_code == 201, link.text

    # ── 6. application detail thread now contains the message
    thread = app_with_gmail.get(f"/api/correspondence/{app_id}").json()
    assert [m["gmail_message_id"] for m in thread["messages"]] == ["m-smoke"]

    # ── 7. status endpoint reports a connected, synced account
    status = app_with_gmail.get("/api/gmail/status").json()
    assert status["connected"] is True
    assert status["email_address"] == "smoke@example.com"
    assert status["message_count"] >= 1
    assert status["history_id"] is not None
