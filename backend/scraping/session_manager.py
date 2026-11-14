"""Browser session manager — persists login state across scraping runs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from browser_use import Browser
else:
    try:
        from browser_use import Browser
    except ImportError:  # pragma: no cover
        Browser = None


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

    async def get_or_create_session(self, site: str) -> Optional[Any]:
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
            # Session file exists — adaptive_scraper will load it directly.
            # No need to create a Browser object here.
            return None  # type: ignore[return-value]

        # Login is manual, always: the app never types a stored password into a site
        # (an automatic login saved 2FA pages as valid sessions).
        logger.info("No saved session for site=%s — requesting manual login", site)

        # Create the browser FIRST so the user has a window to log into
        save_path = self.PROFILES_DIR / site / "state.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Pass storage_state so watchdog knows where to auto-save upon stop()
        from backend.utils.browser_path import get_chromium_executable
        _exe = get_chromium_executable()
        _bkw: dict = {"headless": False, "storage_state": save_path.resolve().as_posix(), "user_data_dir": None}
        if _exe:
            _bkw["executable_path"] = _exe
        browser = Browser(**_bkw)
        
        try:
            await browser.start()
            page = await browser.new_page()
            
            # Navigate to the site's login page so the user sees it
            from backend.scraping.site_prompts import SITE_CONFIGS
            cfg = SITE_CONFIGS.get(site, {})
            login_url = cfg.get("login_url")
            if login_url and page is not None:
                try:
                    await page.goto(login_url)
                except Exception:
                    logger.debug("Could not navigate to login page for %s", site)
        except Exception:
            logger.debug("Could not open page/context for manual login browser")

        # Now broadcast LoginRequired and wait for user to click 'Done'
        await self._request_login(site)

        # After the event fires, save state from the ALREADY OPEN browser
        try:
            # stop() gracefully clears event buses and dispatches SaveStorageStateEvent
            # which writes the state to the storage_state path we provided in __init__.
            # The agent will call start() again later.
            await browser.stop()
            logger.info("Saved session state for site=%s to %s", site, save_path)
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

__all__ = ["BrowserSessionManager", "SessionInfo"]
