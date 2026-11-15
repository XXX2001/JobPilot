"""A missed container CDP probe must never start a replacement browser."""

import io
import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from backend.applier import _strategy_common as common
from backend.browser import chrome
from backend.config import settings

ERROR = "The JobPilot Chrome in the container is not answering"
VIEW_URL = "http://127.0.0.1:6080/vnc.html"
ENDPOINT = {
    "Browser": "Chrome/test",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test",
}


@pytest.fixture(autouse=True)
def container_mode(monkeypatch):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", VIEW_URL)


def test_container_probe_miss_never_launches_unsandboxed_browser(monkeypatch):
    """Regression port of the reviewer's test_container_nosandbox_proof.py."""
    monkeypatch.setattr(common, "running_endpoint", lambda: None)
    browser = MagicMock()
    monkeypatch.setattr(common, "_Browser", browser)
    with pytest.raises(RuntimeError, match=ERROR):
        common.build_browser({}, None, apply_url="https://jobs.example.com/apply")
    browser.assert_not_called()


async def test_container_probe_miss_never_launches_persistent_context(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "running_endpoint", lambda: None)
    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock()
    with pytest.raises(RuntimeError, match=ERROR):
        await chrome.open_context(pw, tmp_path, ["--no-sandbox"])
    pw.chromium.launch_persistent_context.assert_not_called()
    pw.chromium.connect_over_cdp.assert_not_called()


def test_container_ensure_cannot_launch_another_chrome(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "running_endpoint", lambda: None)
    launch = MagicMock()
    monkeypatch.setattr(chrome.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match=ERROR):
        chrome.ensure_chrome_running(tmp_path)
    launch.assert_not_called()


def test_container_build_attaches_without_launch_flags(monkeypatch):
    monkeypatch.setattr(common, "running_endpoint", lambda: {"port": 9222})
    browser = MagicMock()
    monkeypatch.setattr(common, "_Browser", browser)
    common.build_browser({"args": ["--no-sandbox"], "headless": True}, None)
    kwargs = browser.call_args.kwargs
    assert kwargs["cdp_url"] == "http://127.0.0.1:9222"
    assert kwargs["keep_alive"] is True
    assert "args" not in kwargs and "headless" not in kwargs


@pytest.mark.parametrize("connection_fails", [False, True])
async def test_container_context_only_attaches(monkeypatch, tmp_path, connection_fails):
    monkeypatch.setattr(chrome, "running_endpoint", lambda: {"port": 9222})
    session = object()
    pw = MagicMock()
    pw.chromium.connect_over_cdp = AsyncMock(
        return_value=MagicMock(contexts=[session]),
        side_effect=ConnectionError("Chrome stopped") if connection_fails else None,
    )
    pw.chromium.launch_persistent_context = AsyncMock()
    if connection_fails:
        with pytest.raises(ConnectionError):
            await chrome.open_context(pw, tmp_path, [])
    else:
        assert await chrome.open_context(pw, tmp_path, []) == (session, True)
    pw.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222")
    pw.chromium.launch_persistent_context.assert_not_called()


def test_native_build_browser_retains_fallback(monkeypatch):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", "")
    monkeypatch.setattr(common, "running_endpoint", lambda: None)
    monkeypatch.setattr(common, "_Browser", lambda **kwargs: kwargs)
    kwargs = common.build_browser({}, None)
    assert "--no-sandbox" in kwargs["args"]
    assert kwargs["chromium_sandbox"] is False


async def test_native_context_retains_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", "")
    monkeypatch.setattr(chrome, "running_endpoint", lambda: None)
    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value="context")
    assert await chrome.open_context(pw, tmp_path, ["--native"]) == ("context", False)
    pw.chromium.launch_persistent_context.assert_awaited_once_with(
        user_data_dir=tmp_path.as_posix(), headless=False, args=["--native"]
    )


@pytest.mark.parametrize("misses", [0, 1, 2, 3])
def test_running_endpoint_retries_container_version_probe(monkeypatch, tmp_path, misses):
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/test\n")
    monkeypatch.setattr(chrome, "chrome_dir", lambda: tmp_path)
    replies = [TimeoutError()] * misses + [io.BytesIO(json.dumps(ENDPOINT).encode())]
    request = MagicMock(side_effect=replies)
    monkeypatch.setattr(chrome.urllib.request, "urlopen", request)
    sleep = MagicMock()
    monkeypatch.setattr(chrome.time, "sleep", sleep)
    endpoint = chrome.running_endpoint()
    assert (endpoint is not None) == (misses < 3)
    attempts = min(misses + 1, 3)
    assert request.call_args_list == [
        call("http://127.0.0.1:9222/json/version", timeout=1)
    ] * attempts
    assert sleep.call_args_list == [call(1)] * (attempts - 1)


def test_native_endpoint_remains_a_single_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", "")
    monkeypatch.setattr(chrome, "chrome_dir", lambda: tmp_path)
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/test\n")
    probe = MagicMock(return_value=None)
    monkeypatch.setattr(chrome, "_probe", probe)
    sleep = MagicMock()
    monkeypatch.setattr(chrome.time, "sleep", sleep)
    assert chrome.running_endpoint() is None
    probe.assert_called_once_with(9222)
    sleep.assert_not_called()


@pytest.mark.parametrize("reply", [{}, [], {**ENDPOINT, "webSocketDebuggerUrl": "ws://wrong"}])
def test_container_rejects_invalid_or_stale_endpoint(monkeypatch, tmp_path, reply):
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/test\n")
    monkeypatch.setattr(chrome, "chrome_dir", lambda: tmp_path)
    monkeypatch.setattr(chrome, "_probe", lambda _: reply)
    monkeypatch.setattr(chrome.time, "sleep", lambda _: None)
    assert chrome.running_endpoint() is None
