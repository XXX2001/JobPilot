"""Apply runs use the owner's JobPilot Chrome when it is running, and never close it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.browser import chrome


async def test_open_context_attaches_to_the_running_chrome(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(chrome, "running_endpoint", lambda: {"port": 4242})
    pw = MagicMock()
    session = object()
    pw.chromium.connect_over_cdp = AsyncMock(return_value=MagicMock(contexts=[session]))
    pw.chromium.launch_persistent_context = AsyncMock()
    assert await chrome.open_context(pw, tmp_path, []) == (session, True)
    pw.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:4242")
    pw.chromium.launch_persistent_context.assert_not_awaited()


async def test_open_context_launches_a_site_profile_without_it(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(chrome, "running_endpoint", lambda: None)
    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value="ctx")
    assert await chrome.open_context(pw, tmp_path, ["--x"]) == ("ctx", False)


async def test_fill_only_failure_closes_the_tab_not_the_owners_session(monkeypatch):
    from backend.applier.form_filler import PlaywrightFormFiller

    page = MagicMock()
    page.goto = AsyncMock(side_effect=RuntimeError("navigation failed"))
    page.close = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    async def open_context(pw, profile_dir, args):
        return context, True

    started = MagicMock()
    started.stop = AsyncMock()
    monkeypatch.setattr(chrome, "open_context", open_context)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: MagicMock(start=AsyncMock(return_value=started)),
    )
    with pytest.raises(RuntimeError, match="navigation failed"):
        await PlaywrightFormFiller(llm_client=MagicMock()).fill_only(
            apply_url="https://example.com/apply"
        )
    page.close.assert_awaited_once()
    context.close.assert_not_awaited()
