"""CAPTCHA and Cloudflare block detection + manual-pause handler.

Detects common CAPTCHA elements AND Cloudflare "Request Blocked" / challenge
pages.  Broadcasts a WS notification to the user and polls until the block is
resolved manually in the browser window.  After resolution the browser's
storage state (cookies + localStorage) is persisted so subsequent visits to
the same domain skip repeated CAPTCHAs.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
from backend.config import settings as _settings  # noqa: E402

logger = logging.getLogger(__name__)

try:
    from playwright_stealth import stealth_async  # type: ignore

    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    stealth_async = None  # type: ignore
SESSIONS_DIR = Path(_settings.jobpilot_data_dir) / "browser_sessions"
PROFILES_DIR = Path(_settings.jobpilot_data_dir) / "browser_profiles"

# ── Selector-based detection ────────────────────────────────────────────────
_CAPTCHA_SELECTORS = [
    # Google reCAPTCHA
    ".g-recaptcha",
    "iframe[src*='recaptcha']",
    # hCaptcha
    ".h-captcha",
    "iframe[src*='hcaptcha']",
    # Cloudflare Turnstile / challenge
    "#challenge-running",
    "#cf-challenge-running",
    ".cf-turnstile",
    "iframe[src*='challenges.cloudflare.com']",
    "#turnstile-wrapper",
    # Generic
    "[data-sitekey]",
    ".captcha-solver",
    "#captcha",
    ".antibot",
]

# ── Title / text-based detection (Cloudflare block pages) ───────────────────
_BLOCK_TITLE_FRAGMENTS = [
    "just a moment",
    "attention required",
    "request blocked",
    "access denied",
    "verify you are human",
    "checking your browser",
    "confirm you're not a robot",
    "one more step",
    "please wait",
    "security check",
]


def _domain_key(url: str) -> str:
    """Extract a clean domain key from a URL (e.g. 'linkedin_com')."""
    hostname = urlparse(url).hostname or "unknown"
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname.replace(".", "_")


def get_session_path(url: str) -> Path:
    """Return the storage-state file path for a domain.

    New canonical location: data/browser_profiles/{site}/state.json
    Falls back to old data/browser_sessions/{site}_state.json if the
    profile dir doesn't exist yet (backward compatibility).
    """
    site_key = _domain_key(url)
    new_path = PROFILES_DIR / site_key / "state.json"
    old_path = SESSIONS_DIR / f"{site_key}_state.json"
    # Prefer new profile dir; fall back to old flat file
    if new_path.exists() or not old_path.exists():
        return new_path
    return old_path


async def save_session(page) -> None:
    """Persist the browser context's storage state for the page's domain."""
    try:
        path = get_session_path(page.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        context = page.context
        await context.storage_state(path=str(path))
        logger.info("Saved browser session for %s → %s", page.url, path)
    except Exception as exc:
        logger.debug("Could not save session after CAPTCHA: %s", exc)


async def detect_captcha(page) -> bool:
    """Check if a CAPTCHA widget is visible on the page."""
    for selector in _CAPTCHA_SELECTORS:
        try:
            element = await page.query_selector(selector)
            if element:
                visible = await element.is_visible()
                if visible:
                    logger.info("CAPTCHA detected via selector: %s", selector)
                    return True
        except Exception:
            continue
    return False


async def detect_block_page(page) -> bool:
    """Check if the page is a Cloudflare / bot-detection block page.

    Uses the page title and a small body-text sample to detect common
    "Request Blocked", "Just a moment…", and similar challenge pages.
    """
    try:
        title = (await page.title() or "").lower()
        for fragment in _BLOCK_TITLE_FRAGMENTS:
            if fragment in title:
                logger.info("Block page detected via title: '%s'", title)
                return True
    except Exception:
        pass

    # Check a snippet of visible body text (first 500 chars)
    try:
        body_text = await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 500).toLowerCase()"
        )
        for fragment in _BLOCK_TITLE_FRAGMENTS:
            if fragment in body_text:
                logger.info("Block page detected via body text containing '%s'", fragment)
                return True
    except Exception:
        pass

    return False


async def detect_any_block(page) -> bool:
    """Return True if either a CAPTCHA widget or a block page is detected."""
    return await detect_captcha(page) or await detect_block_page(page)


async def wait_for_captcha_resolution(
    page,
    job_id: int | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> bool:
    """Broadcast CAPTCHA/block notification and poll until resolved or timeout.

    Returns True if resolved, False on timeout.
    """
    try:
        from backend.api.ws import manager as ws_manager
        from backend.api.ws_models import CaptchaDetected, CaptchaResolved
    except ImportError:
        logger.warning("WS manager not available for CAPTCHA notification")
        return False

    site = urlparse(page.url).hostname or "unknown"

    await ws_manager.broadcast(
        CaptchaDetected(
            site=site,
            job_id=job_id,
            message=f"CAPTCHA / block detected on {site} — please solve it in the browser window",
        )
    )

    elapsed = 0.0
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if not await detect_any_block(page):
            logger.info("Block resolved on %s after %.1fs", site, elapsed)
            await ws_manager.broadcast(CaptchaResolved(job_id=job_id))
            await save_session(page)
            return True

    logger.warning("Block resolution timed out on %s after %.1fs", site, timeout)
    await ws_manager.broadcast(CaptchaResolved(job_id=job_id))
    return False


async def check_and_handle_captcha(page, job_id: int | None = None) -> bool:
    """Convenience: detect + handle in one call.

    Returns True if a CAPTCHA/block was found and handled.
    """
    if await detect_any_block(page):
        return await wait_for_captcha_resolution(page, job_id=job_id)
    return False


async def preflight_check_url(
    url: str,
    *,
    headless: bool = True,
    job_id: int | None = None,
    timeout: float = 300.0,
) -> bool:
    """Probe *url* with a raw Playwright browser and handle any block.

    Uses Playwright directly (not browser-use's lazy ``Browser`` wrapper)
    so we can reliably navigate before an agent is created.

    1. Launch a headless browser and navigate to *url*.
    2. If blocked → relaunch as **visible**, notify user via WS, poll until
       the user solves the challenge, save the session, then close.
    3. Returns True if the page is accessible (immediately or after solving).
       Returns False on timeout or unrecoverable error.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping preflight check")
        return True  # optimistic: let the agent try

    session_path = get_session_path(url)
    site_key = _domain_key(url)
    profile_dir = PROFILES_DIR / site_key

    # ── Phase 1: quick headless probe ───────────────────────────────────
    pw = await async_playwright().start()
    try:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--disable-infobars",
        ]
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=launch_args,
        )
        page = await context.new_page()

        # Apply stealth patches to avoid bot detection
        if _STEALTH_AVAILABLE and stealth_async:
            await stealth_async(page)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        except Exception as exc:
            logger.warning("Preflight navigation to %s failed: %s", url, exc)
            await context.close()
            await pw.stop()
            return False

        blocked = await detect_any_block(page)
        if not blocked:
            logger.info("Preflight: %s is accessible (no block)", url)
            # Save storage-state snapshot alongside profile dir for browser-use consumers
            try:
                await context.storage_state(path=str(session_path))
            except Exception:
                pass
            await context.close()
            await pw.stop()
            return True

        logger.info("Preflight: block detected on %s", url)
        await context.close()
    except Exception as exc:
        logger.warning("Preflight headless probe failed: %s", exc)
        await pw.stop()
        return True  # let the agent try

    # ── Phase 2: visible browser for user to solve ──────────────────────
    if headless:
        logger.info("Preflight: reopening %s in visible browser for user to solve", url)
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=launch_args,
        )
        page = await context.new_page()

        # Apply stealth patches
        if _STEALTH_AVAILABLE and stealth_async:
            await stealth_async(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
    except Exception as exc:
        logger.warning("Preflight visible browser failed: %s", exc)
        try:
            await context.close()
        except Exception:
            pass
        await pw.stop()
        return False

    # Notify + poll
    resolved = await wait_for_captcha_resolution(page, job_id=job_id, timeout=timeout)

    if resolved:
        # Save storage-state snapshot for browser-use consumers that load storage_state=
        try:
            await context.storage_state(path=str(session_path))
            logger.info("Preflight: saved session for %s after block resolution", url)
        except Exception as exc:
            logger.debug("Preflight session save failed: %s", exc)

    await context.close()
    await pw.stop()
    return resolved
