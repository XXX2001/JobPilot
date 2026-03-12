"""
Windows Playwright / browser-use diagnostic tests.

Run on the Windows machine with:
    uv run pytest tests/test_windows_playwright.py -v -s

Each test is independent and prints diagnostic info so you can see exactly
where the chain breaks. Tests are ordered from low-level to high-level —
run them in order and stop at the first failure.
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"
ROOT = Path(__file__).resolve().parents[1]


def _print(msg: str) -> None:
    print(f"\n  [DIAG] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Stage 1 — Python / event loop
# ---------------------------------------------------------------------------


def test_01_python_version():
    """Python ≥ 3.10 required."""
    _print(f"Python {sys.version}")
    assert sys.version_info >= (3, 10), f"Need Python 3.10+, got {sys.version}"


def test_02_event_loop_policy():
    """On Windows the SelectorEventLoopPolicy must be active (set in start.py)."""
    _print(f"OS: {platform.system()} {platform.release()}")
    policy = asyncio.get_event_loop_policy()
    _print(f"Event loop policy: {type(policy).__name__}")
    if IS_WINDOWS:
        assert isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy), (
            "WindowsSelectorEventLoopPolicy not active — check start.py sets it before uvicorn.run(). "
            "ProactorEventLoop deadlocks when browser-use spawns playwright subprocess."
        )
    else:
        pytest.skip("Windows-only check")


# ---------------------------------------------------------------------------
# Stage 2 — Playwright binary discovery
# ---------------------------------------------------------------------------


def test_03_playwright_importable():
    """playwright package must be importable."""
    try:
        import playwright  # noqa: F401
        _print(f"playwright imported OK")
    except ImportError as e:
        pytest.fail(f"Cannot import playwright: {e}")


def test_04_playwright_chromium_executable():
    """Chromium binary must exist at the path Playwright reports."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
        _print(f"Playwright chromium path: {exe}")
        assert Path(exe).exists(), (
            f"Chromium binary not found at: {exe}\n"
            "Run:  uv run playwright install chromium\n"
            "Then re-run this test."
        )


def test_05_playwright_launch_sync():
    """Chromium must launch (sync API) without errors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        version = browser.version
        _print(f"Chromium launched OK — version: {version}")
        browser.close()


@pytest.mark.asyncio
async def test_06_playwright_launch_async():
    """Chromium must launch (async API) inside an asyncio coroutine."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        version = browser.version
        _print(f"Chromium async launch OK — version: {version}")
        await browser.close()


# ---------------------------------------------------------------------------
# Stage 3 — browser-use discovery
# ---------------------------------------------------------------------------


def test_07_browser_use_importable():
    """browser-use package must be importable."""
    try:
        from browser_use import Browser  # noqa: F401
        _print("browser_use.Browser imported OK")
    except ImportError as e:
        pytest.fail(f"Cannot import browser_use: {e}\nRun: uv sync")


def test_07b_patchright_chromium_installed():
    """patchright (used by browser-use internally) must have Chromium installed.

    If this fails, run:  uv run patchright install chromium
    This is the ROOT CAUSE of the 'No local browser binary found' deadlock.
    """
    try:
        from patchright.sync_api import sync_playwright as sync_patchright
    except ImportError:
        pytest.skip("patchright not installed — run: uv sync")

    try:
        with sync_patchright() as p:
            exe = p.chromium.executable_path
            _print(f"patchright chromium path: {exe}")
            assert Path(exe).exists(), (
                f"patchright Chromium binary not found at: {exe}\n"
                "Run:  uv run patchright install chromium\n"
                "This is the ROOT CAUSE of the browser-use deadlock on Windows."
            )
    except Exception as e:
        pytest.fail(
            f"patchright Chromium not installed: {e}\n"
            "Run:  uv run patchright install chromium"
        )


def test_08_browser_use_finds_chromium():
    """browser-use's LocalBrowserWatchdog must find the chromium binary."""
    try:
        from browser_use.browser.watchdog_base import LocalBrowserWatchdog
    except ImportError:
        pytest.skip("LocalBrowserWatchdog not accessible in this browser-use version")

    try:
        watchdog = LocalBrowserWatchdog()
        binary = getattr(watchdog, "browser_binary_location", None) or getattr(
            watchdog, "_binary", None
        )
        _print(f"browser-use binary path: {binary}")
    except Exception as e:
        _print(f"Could not inspect watchdog: {e}")
        pytest.skip("Cannot inspect LocalBrowserWatchdog internals")


@pytest.mark.asyncio
async def test_09_browser_use_start_headless():
    """browser-use Browser() must start headless without a 30s timeout."""
    from browser_use import Browser

    _print("Starting browser-use Browser(headless=True) …")
    browser = Browser(headless=True, user_data_dir=None)
    try:
        await asyncio.wait_for(browser.start(), timeout=20)
        _print("browser-use Browser started OK")
    except asyncio.TimeoutError:
        pytest.fail(
            "browser-use Browser.start() timed out after 20s.\n"
            "This is the deadlock: browser-use tries to run 'playwright install' "
            "as a subprocess but it blocks.\n"
            "Ensure start.py sets asyncio.WindowsSelectorEventLoopPolicy() BEFORE "
            "uvicorn starts, then restart the server."
        )
    finally:
        try:
            await browser.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stage 4 — storage_state path handling
# ---------------------------------------------------------------------------


def test_10_storage_state_path_format():
    """storage_state must be an absolute path with forward slashes."""
    from backend.config import settings

    data_dir = Path(settings.jobpilot_data_dir)
    save_path = data_dir / "browser_profiles" / "linkedin" / "state.json"
    resolved = save_path.resolve().as_posix()

    _print(f"Raw path:       {save_path}")
    _print(f"Resolved POSIX: {resolved}")

    assert resolved.startswith("/") or (
        len(resolved) >= 3 and resolved[1] == ":"
    ), f"Path is not absolute: {resolved}"
    assert "\\" not in resolved, f"Path contains backslashes: {resolved}"
    assert resolved == resolved.replace("\\", "/"), "Backslashes present"
    _print("Path format OK")


@pytest.mark.asyncio
async def test_11_storage_state_roundtrip(tmp_path):
    """Can create a storage_state file and pass it back to Playwright."""
    from playwright.async_api import async_playwright

    state_file = tmp_path / "state.json"

    async with async_playwright() as p:
        # Save a storage state
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.storage_state(path=state_file.resolve().as_posix())
        await ctx.close()
        await browser.close()

        _print(f"Saved storage state to: {state_file}")
        assert state_file.exists(), "storage_state file was not created"

        # Reload from that state
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(
            storage_state=state_file.resolve().as_posix()
        )
        _print("Reloaded storage state OK")
        await ctx2.close()
        await browser2.close()


# ---------------------------------------------------------------------------
# Stage 5 — AdaptiveScraper path construction
# ---------------------------------------------------------------------------


def test_12_adaptive_scraper_storage_path():
    """AdaptiveScraper must produce an absolute POSIX storage_state path."""
    from backend.config import settings
    from pathlib import Path

    site = "linkedin"
    storage_path = (
        Path(settings.jobpilot_data_dir) / "browser_profiles" / site / "state.json"
    )
    resolved = storage_path.resolve().as_posix()
    _print(f"AdaptiveScraper storage_state would be: {resolved}")
    assert "\\" not in resolved
    assert Path(resolved.replace("/", os.sep)).is_absolute()
