from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.auth import GmailTokenManager
from backend.gmail.credentials import save_credential


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


def _fake_token_response(access_token: str = "ya29.abc", expires_in: int = 3600):
    return httpx.Response(
        200,
        json={"access_token": access_token, "expires_in": expires_in,
              "token_type": "Bearer", "scope": "https://www.googleapis.com/auth/gmail.readonly"},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
    )


async def test_first_call_refreshes_and_caches(monkeypatch):
    monkeypatch.setattr("backend.gmail.auth.settings.GMAIL_CLIENT_ID", "fake-client")
    from pydantic import SecretStr
    monkeypatch.setattr("backend.gmail.auth.settings.GMAIL_CLIENT_SECRET", SecretStr("fake-secret"))

    async with AsyncSessionLocal() as session:
        await save_credential(session, "auth-u1@e.com", "rt-1", ["gmail.readonly"])
        await session.commit()

    mgr = GmailTokenManager()
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        mock_inst = MockClient.return_value.__aenter__.return_value
        mock_inst.post = AsyncMock(return_value=_fake_token_response("tok-A"))

        tok = await mgr.access_token("auth-u1@e.com")
        assert tok == "tok-A"
        tok2 = await mgr.access_token("auth-u1@e.com")
        assert tok2 == "tok-A"
        assert mock_inst.post.await_count == 1


async def test_cache_expires_and_refreshes(monkeypatch):
    monkeypatch.setattr("backend.gmail.auth.settings.GMAIL_CLIENT_ID", "fake-client")
    from pydantic import SecretStr
    monkeypatch.setattr("backend.gmail.auth.settings.GMAIL_CLIENT_SECRET", SecretStr("fake-secret"))

    async with AsyncSessionLocal() as session:
        await save_credential(session, "auth-u2@e.com", "rt-1", ["gmail.readonly"])
        await session.commit()

    mgr = GmailTokenManager(_clock=lambda: 1000.0)
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        mock_inst = MockClient.return_value.__aenter__.return_value
        mock_inst.post = AsyncMock(return_value=_fake_token_response("tok-A", expires_in=60))
        assert await mgr.access_token("auth-u2@e.com") == "tok-A"

    mgr._clock = lambda: 2000.0
    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient2:
        mock_inst2 = MockClient2.return_value.__aenter__.return_value
        mock_inst2.post = AsyncMock(return_value=_fake_token_response("tok-B"))
        assert await mgr.access_token("auth-u2@e.com") == "tok-B"


async def test_missing_credential_raises():
    mgr = GmailTokenManager()
    with pytest.raises(KeyError):
        await mgr.access_token("nobody@example.com")
