from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def app_with_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-secret")
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_oauth_start_redirects_to_google(app_with_gmail: TestClient):
    resp = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=test-client.apps.googleusercontent.com" in loc
    assert "access_type=offline" in loc
    assert "prompt=consent" in loc
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in loc
    assert "state=" in loc


def test_oauth_start_when_unconfigured_returns_503(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "")
    import backend.config as cfg
    cfg.settings = cfg._load_settings()
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/gmail/oauth/start", follow_redirects=False)
    assert resp.status_code == 503


def _fake_token_response():
    return httpx.Response(
        200,
        json={
            "access_token": "ya29.test",
            "refresh_token": "1//refresh-test",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
        },
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
    )


def _fake_userinfo_response():
    return httpx.Response(
        200,
        json={"emailAddress": "oauth-user@example.com", "messagesTotal": 1234},
        request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
    )


def test_oauth_callback_exchanges_and_persists(app_with_gmail: TestClient):
    from urllib.parse import parse_qs, urlparse
    start = app_with_gmail.get("/api/gmail/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    with patch("backend.api.gmail_auth.httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=_fake_token_response())
        inst.get = AsyncMock(return_value=_fake_userinfo_response())
        resp = app_with_gmail.get(
            f"/api/gmail/oauth/callback?code=auth-code-xyz&state={state}",
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303), resp.text
    assert "/settings" in resp.headers["location"]

    from backend.database import AsyncSessionLocal
    from backend.gmail.credentials import decrypt_refresh_token, load_credential

    async def _read():
        async with AsyncSessionLocal() as session:
            return await load_credential(session, "oauth-user@example.com")
    row = asyncio.run(_read())
    assert row is not None
    assert decrypt_refresh_token(row.encrypted_refresh_token) == "1//refresh-test"


def test_oauth_callback_with_bad_state_rejects(app_with_gmail: TestClient):
    resp = app_with_gmail.get(
        "/api/gmail/oauth/callback?code=anything&state=forged-state",
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_disconnect_removes_credential(app_with_gmail: TestClient):
    from backend.database import AsyncSessionLocal
    from backend.gmail.credentials import save_credential, load_credential

    async def _seed():
        async with AsyncSessionLocal() as session:
            await save_credential(session, "oauth-disconnect@e.com", "rt", ["gmail.readonly"])
            await session.commit()
    asyncio.run(_seed())

    with patch("backend.gmail.auth.httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=httpx.Response(
            200,
            text="",
            request=httpx.Request("POST", "https://oauth2.googleapis.com/revoke"),
        ))
        resp = app_with_gmail.post("/api/gmail/disconnect", json={"email": "oauth-disconnect@e.com"})
    assert resp.status_code == 200

    async def _read():
        async with AsyncSessionLocal() as session:
            return await load_credential(session, "oauth-disconnect@e.com")
    assert asyncio.run(_read()) is None
