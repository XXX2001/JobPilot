"""Tests for BrowserSessionManager.

The manager evolved away from `browser_use.BrowserConfig` and now stores
sessions under two layouts:

* `<data>/browser_profiles/<site>/state.json`  (current canonical layout)
* `<data>/browser_sessions/<site>_state.json`  (legacy flat layout)

`list_sessions()` walks both; `clear_session()` removes both; and
`get_or_create_session()` short-circuits to ``None`` when a state file
already exists (the adaptive scraper loads it directly).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.scraping.session_manager import BrowserSessionManager, SessionInfo


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mgr_with_dirs(tmp_path) -> BrowserSessionManager:
    """Return a manager whose SESSIONS_DIR + PROFILES_DIR live under *tmp_path*."""
    mgr = BrowserSessionManager()
    mgr.SESSIONS_DIR = tmp_path / "browser_sessions"
    mgr.PROFILES_DIR = tmp_path / "browser_profiles"
    mgr.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    mgr.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return mgr


# ── list_sessions ─────────────────────────────────────────────────────────────


def test_list_sessions_empty(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    assert mgr.list_sessions() == []


def test_list_sessions_with_legacy_file(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    (mgr.SESSIONS_DIR / "linkedin_state.json").write_text("{}")

    result = mgr.list_sessions()

    assert len(result) == 1
    assert isinstance(result[0], SessionInfo)
    assert result[0].site == "linkedin"
    assert result[0].exists is True


def test_list_sessions_with_profile_dir(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    profile = mgr.PROFILES_DIR / "indeed"
    profile.mkdir()
    (profile / "state.json").write_text('{"cookies": []}')

    result = mgr.list_sessions()

    assert len(result) == 1
    assert result[0].site == "indeed"
    assert result[0].exists is True


def test_list_sessions_profile_dir_wins_over_legacy(tmp_path):
    """If both layouts exist for the same site, the profile dir entry wins."""
    mgr = _mgr_with_dirs(tmp_path)
    profile = mgr.PROFILES_DIR / "linkedin"
    profile.mkdir()
    (profile / "state.json").write_text('{"cookies": []}')
    (mgr.SESSIONS_DIR / "linkedin_state.json").write_text("{}")

    result = mgr.list_sessions()

    assert len(result) == 1
    assert result[0].storage_path.endswith("browser_profiles/linkedin/state.json")


# ── clear_session ─────────────────────────────────────────────────────────────


def test_clear_session_removes_legacy_file(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    state_file = mgr.SESSIONS_DIR / "indeed_state.json"
    state_file.write_text("{}")

    mgr.clear_session("indeed")

    assert not state_file.exists()


def test_clear_session_removes_profile_dir(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    profile = mgr.PROFILES_DIR / "indeed"
    profile.mkdir()
    (profile / "state.json").write_text("{}")

    mgr.clear_session("indeed")

    assert not profile.exists()


def test_clear_session_missing_file_no_error(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    # Should not raise
    mgr.clear_session("nonexistent_site")


# ── confirm_login ─────────────────────────────────────────────────────────────


def test_confirm_login_sets_event(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    event = asyncio.Event()
    mgr._pending_logins["linkedin"] = event

    mgr.confirm_login("linkedin")

    assert event.is_set()


def test_confirm_login_unknown_site_no_error(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    # Should not raise
    mgr.confirm_login("site_that_was_never_pending")


# ── cancel_login ──────────────────────────────────────────────────────────────


def test_cancel_login_sets_event_and_records_cancel(tmp_path):
    mgr = _mgr_with_dirs(tmp_path)
    event = asyncio.Event()
    mgr._pending_logins["indeed"] = event

    mgr.cancel_login("indeed")

    assert event.is_set()
    assert "indeed" in mgr._cancelled_logins


# ── get_or_create_session — existing state short-circuits ────────────────────


@pytest.mark.asyncio
async def test_existing_session_short_circuits(tmp_path):
    """When a state file already exists, get_or_create_session returns None
    (the adaptive scraper loads the state file directly — no Browser needed)."""
    mgr = _mgr_with_dirs(tmp_path)
    profile = mgr.PROFILES_DIR / "linkedin"
    profile.mkdir()
    (profile / "state.json").write_text('{"cookies": []}')

    result = await mgr.get_or_create_session("linkedin")

    assert result is None
