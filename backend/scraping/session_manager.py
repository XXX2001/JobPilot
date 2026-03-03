"""Browser session manager — persists login state across scraping runs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from browser_use import Browser  # type: ignore
except ImportError:  # pragma: no cover
    Browser = None  # type: ignore


@dataclass
class SessionInfo:
    site: str
    storage_path: str
    exists: bool
    last_used_at: Optional[datetime] = None


class BrowserSessionManager:
    """
    Manages persistent browser sessions so the user only logs in once per site.

    Flow for a NEW site:
      1. Emit ``LoginRequired`` WS message so the frontend can show guidance.
      2. Open a headful browser for the user to log in manually.
      3. Wait up to 10 minutes for ``confirm_login(site)`` to be called
         (triggered by the frontend sending a ``login_done`` WS message).
      4. Save Playwright storage-state (cookies + localStorage) to disk.
      5. Return the browser for scraping.

    Flow for a KNOWN site:
      - Load saved storage-state directly — no interaction needed.
    """

    def __init__(self) -> None:
        from backend.config import settings as _settings

        self.SESSIONS_DIR = Path(_settings.jobpilot_data_dir) / "browser_sessions"
        self.PROFILES_DIR = Path(_settings.jobpilot_data_dir) / "browser_profiles"

        self._pending_logins: dict[str, asyncio.Event] = {}
        # Tracks sites where the user cancelled the manual login flow
        self._cancelled_logins: set[str] = set()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def get_or_create_session(self, site: str) -> "Browser":  # type: ignore[return]
        """Return a browser pre-loaded with the user's saved session for *site*.

        If no session exists, blocks until the user logs in (or 10 min timeout).
        """
        if Browser is None:
            raise ImportError(
                "browser-use is not installed. Install it with: pip install browser-use"
            )

        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        # New canonical path: data/browser_profiles/{site}/state.json
        # Backward compat: fall back to old flat file if profile dir not yet used
        new_path = self.PROFILES_DIR / site / "state.json"
        old_path = self.SESSIONS_DIR / f"{site}_state.json"
        storage_path = new_path if (new_path.exists() or not old_path.exists()) else old_path

        if storage_path.exists():
            logger.info("Reusing saved session for site=%s", site)
            # Create browser with stored state and ensure we can open a page/context
            browser = Browser(headless=False, storage_state=str(storage_path))
            try:
                # warm up / probe available APIs so callers don't hit AttributeError
                await self._open_page_and_context(browser)
            except Exception:
                # non-fatal: we still return the browser even if probing fails
                logger.debug("Probing browser APIs failed for site=%s", site)
            return browser

        # First, try auto-login using stored credentials (if any). If that
        # succeeds we return a browser with the saved storage state already
        # persisted. Otherwise fall back to manual login flow.
        try:
            browser = await self._attempt_auto_login(site)
            if browser is not None:
                logger.info("Auto-login succeeded for site=%s", site)
                return browser
        except Exception:
            logger.debug("Auto-login attempt raised an exception for site=%s", site)

        # Auto-login not available or failed: request manual login via WS
        logger.info("No saved session for site=%s — requesting manual login", site)
        await self._request_login(site)

        # After the event fires, save state and return browser
        save_path = self.PROFILES_DIR / site / "state.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        browser = Browser(headless=False)
        try:
            page, ctx = await self._open_page_and_context(browser)
            # Prefer context.storage_state when available
            if ctx is not None and hasattr(ctx, "storage_state"):
                await ctx.storage_state(path=str(save_path))
                logger.info("Saved session state for site=%s to %s", site, save_path)
            elif page is not None:
                # Some browser APIs expose storage_state via the page's context
                page_ctx = getattr(page, "context", None)
                if page_ctx is not None and hasattr(page_ctx, "storage_state"):
                    await page_ctx.storage_state(path=str(save_path))
                    logger.info("Saved session state for site=%s to %s", site, save_path)
                elif hasattr(page, "storage_state"):
                    await page.storage_state(path=str(save_path))
                    logger.info("Saved session state for site=%s to %s", site, save_path)
                else:
                    raise RuntimeError("no storage_state API available on page/context")
            else:
                raise RuntimeError("no page/context API available on browser")
        except Exception as exc:
            logger.warning("Could not save storage state for site=%s: %s", site, exc)
        return browser

    def confirm_login(self, site: str) -> None:
        """Called by the WS handler when it receives a ``login_done`` message.

        Sets the asyncio event that ``get_or_create_session`` is waiting on.
        """
        event = self._pending_logins.get(site)
        if event:
            event.set()
            logger.info("Login confirmed for site=%s", site)
        else:
            logger.warning("confirm_login called for unknown site=%s", site)

    def cancel_login(self, site: str) -> None:
        """Mark the login as cancelled and wake any waiter.

        Called by the WS handler when it receives a ``login_cancel`` message.
        """
        event = self._pending_logins.get(site)
        # Mark cancelled so the waiter can detect the cancellation after
        # the event unblocks.
        self._cancelled_logins.add(site)
        if event:
            event.set()
            logger.info("Login cancelled by user for site=%s", site)
        else:
            logger.warning("cancel_login called for unknown site=%s", site)

    def list_sessions(self) -> list[SessionInfo]:
        """Return metadata for all saved sessions on disk."""
        infos: list[SessionInfo] = []
        # New profile dirs: data/browser_profiles/{site}/state.json
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for state_file in sorted(self.PROFILES_DIR.glob("*/state.json")):
            site = state_file.parent.name
            stat = state_file.stat()
            infos.append(
                SessionInfo(
                    site=site,
                    storage_path=str(state_file),
                    exists=True,
                    last_used_at=datetime.fromtimestamp(stat.st_mtime),
                )
            )
        # Also include old flat-file sessions for backward compat
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        for p in sorted(self.SESSIONS_DIR.glob("*_state.json")):
            site = p.stem.replace("_state", "")
            # Skip if already covered by profile dir entry
            if any(i.site == site for i in infos):
                continue
            stat = p.stat()
            infos.append(
                SessionInfo(
                    site=site,
                    storage_path=str(p),
                    exists=True,
                    last_used_at=datetime.fromtimestamp(stat.st_mtime),
                )
            )
        return infos

    def clear_session(self, site: str) -> None:
        """Delete the saved session for *site* (both profile dir and old flat file)."""
        import shutil

        profile_dir = self.PROFILES_DIR / site
        old_path = self.SESSIONS_DIR / f"{site}_state.json"
        cleared_any = False
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
            logger.info("Cleared profile dir for site=%s", site)
            cleared_any = True
        if old_path.exists():
            old_path.unlink()
            logger.info("Cleared old session file for site=%s", site)
            cleared_any = True
        if not cleared_any:
            logger.warning("No session to clear for site=%s", site)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _request_login(self, site: str) -> None:
        """Emit a WS ``login_required`` message and wait for confirmation."""
        # Import lazily to avoid circular imports at module load
        try:
            from backend.api.ws import manager as ws_manager  # type: ignore
            from backend.api.ws_models import LoginRequired  # type: ignore

            await ws_manager.broadcast(
                LoginRequired(
                    type="login_required",
                    site=site,
                    browser_window_title=(
                        f"Please log into {site} in the browser window,"
                        " then click 'Done' in JobPilot."
                    ),
                )
            )
        except Exception as exc:
            logger.warning("Could not broadcast LoginRequired for site=%s: %s", site, exc)

        event = asyncio.Event()
        self._pending_logins[site] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=600)  # 10 minutes
        except asyncio.TimeoutError:
            logger.error("Login for site=%s timed out after 10 minutes", site)
            raise TimeoutError(f"Login for {site} timed out after 10 minutes")
        finally:
            self._pending_logins.pop(site, None)

        # If the user cancelled the login flow, raise an error so callers
        # don't proceed with scraping for this site.
        if site in self._cancelled_logins:
            # remove the flag and raise a specific error
            self._cancelled_logins.discard(site)
            logger.info("Login for site=%s was cancelled by user", site)
            raise RuntimeError(f"Login for {site} cancelled by user")

    async def _attempt_auto_login(self, site: str) -> Optional[object]:
        """Try to perform an automatic login using stored SiteCredential.

        Returns a Browser instance with the logged-in session on success,
        or None on failure / when no credentials are available.
        """
        # Only attempt for known sites we have selectors for
        if site not in ("linkedin", "indeed"):
            return None

        # Lazy imports to avoid heavy startup dependencies / circular imports
        try:
            from backend.config import settings
            from backend.models.user import SiteCredential
            from backend.database import AsyncSessionLocal
            from sqlalchemy import select
            from cryptography.fernet import Fernet
        except Exception:
            return None

        # Fetch credentials from DB
        try:
            async with AsyncSessionLocal() as db:  # type: ignore[name-defined]
                stmt = select(SiteCredential).where(SiteCredential.site_name == site)
                result = await db.execute(stmt)
                row = result.scalar_one_or_none()
        except Exception:
            row = None

        if not row or not row.encrypted_email or not row.encrypted_password:
            return None

        if not getattr(settings, "CREDENTIAL_KEY", None):
            return None

        try:
            f = Fernet(settings.CREDENTIAL_KEY.encode())
            email = f.decrypt(row.encrypted_email.encode()).decode()
            password = f.decrypt(row.encrypted_password.encode()).decode()
        except Exception:
            return None

        # Open browser + page/context via compatibility helper
        browser = Browser(headless=False)
        try:
            page, ctx = await self._open_page_and_context(browser)

            if page is None:
                # cannot interact
                try:
                    await browser.close()
                except Exception:
                    pass
                return None

            # Site-specific login flows
            success = False

            if site == "linkedin":
                try:
                    await page.goto("https://www.linkedin.com/login")
                    # fill known LinkedIn inputs
                    await page.fill("input#username", email)
                    await page.fill("input#password", password)
                    # submit
                    await page.click("button[type=submit]")
                    # wait a short while for navigation/auth
                    await page.wait_for_timeout(3000)
                    # consider login successful if we're not on the login path
                    url = getattr(page, "url", None) or ""
                    if "/login" not in url:
                        success = True
                except Exception:
                    success = False

            elif site == "indeed":
                try:
                    # Indeed account login endpoint
                    await page.goto("https://www.indeed.com/account/login")

                    # Try several possible selectors for email + password
                    email_selectors = [
                        "input#login-email-input",
                        "input#email",
                        "input[name=login_email]",
                        "input[name=email]",
                    ]
                    pass_selectors = [
                        "input#login-password-input",
                        "input#password",
                        "input[name=password]",
                    ]

                    filled = False
                    for sel in email_selectors:
                        try:
                            if await page.query_selector(sel):
                                await page.fill(sel, email)
                                filled = True
                                break
                        except Exception:
                            continue
                    for sel in pass_selectors:
                        try:
                            if await page.query_selector(sel):
                                await page.fill(sel, password)
                                break
                        except Exception:
                            continue

                    # submit: try common submit buttons
                    try:
                        if await page.query_selector("button[type=submit]"):
                            await page.click("button[type=submit]")
                        else:
                            # fallback: press Enter in password field
                            await page.keyboard.press("Enter")
                    except Exception:
                        pass

                    await page.wait_for_timeout(3000)
                    url = getattr(page, "url", None) or ""
                    if "/account/login" not in url and "/login" not in url:
                        success = True
                except Exception:
                    success = False

            if not success:
                # close browser and return None to fall back to manual
                try:
                    await browser.close()
                except Exception:
                    pass
                return None

            # Save storage state to disk using the same logic as manual save
            save_path = self.PROFILES_DIR / site / "state.json"
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # Re-open page/context in case previous objects don't expose storage_state
            page, ctx = await self._open_page_and_context(browser)
            try:
                if ctx is not None and hasattr(ctx, "storage_state"):
                    await ctx.storage_state(path=str(save_path))
                    logger.info("Auto-saved session state for site=%s to %s", site, save_path)
                elif page is not None:
                    page_ctx = getattr(page, "context", None)
                    if page_ctx is not None and hasattr(page_ctx, "storage_state"):
                        await page_ctx.storage_state(path=str(save_path))
                        logger.info("Auto-saved session state for site=%s to %s", site, save_path)
                    elif hasattr(page, "storage_state"):
                        await page.storage_state(path=str(save_path))
                        logger.info("Auto-saved session state for site=%s to %s", site, save_path)
                    else:
                        logger.warning(
                            "Could not find storage_state API to save auto-login for %s", site
                        )
                else:
                    logger.warning(
                        "No page/context available to save auto-login state for %s", site
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to save storage state after auto-login for %s: %s", site, exc
                )

            return browser

        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
            return None

    async def _open_page_and_context(self, browser) -> tuple[Optional[object], Optional[object]]:
        """Compatibility helper: return (page, context) for different browser APIs.

        Tries the following in order:
        1. browser.new_context() -> ctx.new_page()
        2. browser.new_page()
        3. browser.open_page()

        Returns (page, ctx) where either may be None if unavailable.
        """
        # Try Playwright-style context -> page
        try:
            if hasattr(browser, "new_context"):
                ctx = await browser.new_context()
                page = None
                if hasattr(ctx, "new_page"):
                    try:
                        page = await ctx.new_page()
                    except Exception:
                        page = None
                return page, ctx
        except Exception:
            # fallthrough to other APIs
            pass

        # Try browser.new_page()
        try:
            if hasattr(browser, "new_page"):
                page = await browser.new_page()
                ctx = getattr(page, "context", None)
                return page, ctx
        except Exception:
            pass

        # Try older/open_page API
        try:
            if hasattr(browser, "open_page"):
                page = await browser.open_page()
                ctx = getattr(page, "context", None)
                return page, ctx
        except Exception:
            pass

        return None, None


__all__ = ["BrowserSessionManager", "SessionInfo"]
