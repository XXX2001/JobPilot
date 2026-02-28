"""Tests for BrowserSessionManager."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scraping.session_manager import BrowserSessionManager, SessionInfo


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mgr() -> BrowserSessionManager:
    return BrowserSessionManager()


# ── list_sessions ─────────────────────────────────────────────────────────────


def test_list_sessions_empty(tmp_path, monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(BrowserSessionManager, "SESSIONS_DIR", tmp_path)
    result = mgr.list_sessions()
    assert result == []


def test_list_sessions_with_file(tmp_path, monkeypatch):
    monkeypatch.setattr(BrowserSessionManager, "SESSIONS_DIR", tmp_path)
    (tmp_path / "linkedin_state.json").write_text("{}")
    mgr = _mgr()
    mgr.SESSIONS_DIR = tmp_path
    result = mgr.list_sessions()
    assert len(result) == 1
    assert result[0].site == "linkedin"
    assert result[0].exists is True


# ── clear_session ─────────────────────────────────────────────────────────────


def test_clear_session_removes_file(tmp_path, monkeypatch):
    mgr = _mgr()
    mgr.SESSIONS_DIR = tmp_path
    state_file = tmp_path / "indeed_state.json"
    state_file.write_text("{}")
    assert state_file.exists()
    mgr.clear_session("indeed")
    assert not state_file.exists()


def test_clear_session_missing_file_no_error(tmp_path):
    mgr = _mgr()
    mgr.SESSIONS_DIR = tmp_path
    # Should not raise
    mgr.clear_session("nonexistent_site")


# ── confirm_login ─────────────────────────────────────────────────────────────


def test_confirm_login_sets_event():
    mgr = _mgr()
    event = asyncio.Event()
    mgr._pending_logins["linkedin"] = event
    assert not event.is_set()
    mgr.confirm_login("linkedin")
    assert event.is_set()


def test_confirm_login_unknown_site_no_error():
    mgr = _mgr()
    # Should not raise
    mgr.confirm_login("site_that_was_never_pending")


# ── get_or_create_session — existing state ────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_session_no_login_flow(tmp_path):
    """When a state file already exists, we skip the login flow entirely."""
    mgr = _mgr()
    mgr.SESSIONS_DIR = tmp_path
    # Create a fake state file
    (tmp_path / "linkedin_state.json").write_text('{"cookies": []}')

    mock_browser = MagicMock()
    mock_browser_cls = MagicMock(return_value=mock_browser)
    mock_config_cls = MagicMock()

    broadcast_called = False

    async def fake_broadcast(msg):
        nonlocal broadcast_called
        broadcast_called = True

    with (
        patch("backend.scraping.session_manager.Browser", mock_browser_cls),
        patch("backend.scraping.session_manager.BrowserConfig", mock_config_cls),
    ):
        result = await mgr.get_or_create_session("linkedin")

    assert result is mock_browser
    assert not broadcast_called, "LoginRequired should NOT have been broadcast for existing session"


# ── get_or_create_session — no state, confirm quickly ────────────────────────


@pytest.mark.asyncio
async def test_new_session_confirm_login_resolves(tmp_path):
    """New session: confirm_login() resolves the waiting coroutine."""
    mgr = _mgr()
    mgr.SESSIONS_DIR = tmp_path

    mock_browser = MagicMock()
    mock_ctx = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_ctx)
    mock_browser_cls = MagicMock(return_value=mock_browser)
    mock_config_cls = MagicMock()

    broadcast_msgs = []

    async def fake_broadcast(msg):
        broadcast_msgs.append(msg)

    async def confirm_after_delay():
        await asyncio.sleep(0.05)
        mgr.confirm_login("indeed")

    with (
        patch("backend.scraping.session_manager.Browser", mock_browser_cls),
        patch("backend.scraping.session_manager.BrowserConfig", mock_config_cls),
        patch("backend.scraping.session_manager.logger"),
    ):
        # Patch the internal ws broadcast call
        mgr._request_login = AsyncMock(
            side_effect=lambda site: _fake_request_login(mgr, site, broadcast_msgs)
        )

        async def _fake_request_login(m, site, msgs):
            event = asyncio.Event()
            m._pending_logins[site] = event
            try:
                await asyncio.wait_for(event.wait(), timeout=5)
            except asyncio.TimeoutError:
                raise TimeoutError(f"Login for {site} timed out")
            finally:
                m._pending_logins.pop(site, None)

        mgr._request_login = lambda site: _fake_request_login(mgr, site, broadcast_msgs)

        task = asyncio.create_task(mgr.get_or_create_session("indeed"))
        await asyncio.sleep(0.05)
        mgr.confirm_login("indeed")
        result = await asyncio.wait_for(task, timeout=5)

    assert result is mock_browser
