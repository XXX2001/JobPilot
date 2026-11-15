"""Tests for GET /api/today — Today dashboard (nx-2)."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from starlette.testclient import TestClient


def test_today_returns_200(test_app: TestClient) -> None:
    """GET /api/today returns 200 on a fresh DB."""
    resp = test_app.get("/api/today")
    assert resp.status_code == 200


def test_today_response_shape(test_app: TestClient) -> None:
    """Response has the three required top-level sections."""
    resp = test_app.get("/api/today")
    assert resp.status_code == 200
    data = resp.json()
    assert "new_matches" in data
    assert "blocked_actions" in data
    assert "week_stats" in data


def test_today_new_matches_empty_state(test_app: TestClient) -> None:
    """new_matches section has expected keys when DB is empty."""
    resp = test_app.get("/api/today")
    nm = resp.json()["new_matches"]
    assert "since" in nm
    assert "high_confidence" in nm
    assert "worth_reviewing" in nm
    assert "skipped" in nm
    assert "total" in nm
    assert isinstance(nm["high_confidence"], list)
    assert isinstance(nm["worth_reviewing"], list)
    assert isinstance(nm["skipped"], list)
    assert nm["total"] == 0


def test_today_blocked_actions_empty_state(test_app: TestClient) -> None:
    """blocked_actions has an actions list (possibly empty) on fresh DB."""
    resp = test_app.get("/api/today")
    ba = resp.json()["blocked_actions"]
    assert "actions" in ba
    assert isinstance(ba["actions"], list)


@pytest.mark.parametrize("legacy_count", [0, 1, 2])
def test_today_only_counts_legacy_credentials_as_expired_logins(
    test_app: TestClient, monkeypatch, legacy_count: int,
) -> None:
    from backend.applier.harness.accounts import record_account
    from backend.config import settings
    from backend.database import db_session
    from backend.models.user import SiteCredential
    from backend.scraping.session_manager import BrowserSessionManager

    monkeypatch.setattr(settings, "CREDENTIAL_KEY", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(BrowserSessionManager, "list_sessions", lambda self: [])

    async def seed():
        await record_account(
            "career.sapsf.eu?company=fanuc", method="password", email="ada@example.com",
            status="signed_in", login_url="https://career.sapsf.eu/?company=fanuc",
        )
        await record_account(
            "acme.myworkdayjobs.com", method="google", email="ada@example.com",
            status="verified", login_url="https://acme.myworkdayjobs.com/login",
        )
        async with db_session() as db:
            for index in range(legacy_count):
                db.add(SiteCredential(site_name=f"legacy-{index}", method=None))

    asyncio.run(seed())
    response = test_app.get("/api/today")
    assert response.status_code == 200
    broken = [
        action for action in response.json()["blocked_actions"]["actions"]
        if action["kind"] == "broken_session"
    ]
    if legacy_count:
        assert len(broken) == 1
        assert broken[0]["count"] == legacy_count
    else:
        assert broken == []


def test_today_week_stats_shape(test_app: TestClient) -> None:
    """week_stats has the expected numeric keys and placeholder response_rate."""
    resp = test_app.get("/api/today")
    ws = resp.json()["week_stats"]
    assert "applications_submitted" in ws
    assert "daily_limit_used" in ws
    assert "daily_limit_total" in ws
    assert "response_rate" in ws
    assert isinstance(ws["applications_submitted"], int)
    assert isinstance(ws["daily_limit_used"], int)
    assert isinstance(ws["daily_limit_total"], int)
    assert "Gmail" in ws["response_rate"]
