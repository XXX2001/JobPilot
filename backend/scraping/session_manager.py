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
            return Browser(headless=False, storage_state=str(storage_path))

        # First time: open headful browser and wait for user to log in
        logger.info("No saved session for site=%s — requesting manual login", site)
        await self._request_login(site)

        # After the event fires, save state and return browser
        save_path = self.PROFILES_DIR / site / "state.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        browser = Browser(headless=False)
        try:
            ctx = await browser.new_context()
            await ctx.storage_state(path=str(save_path))
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


__all__ = ["BrowserSessionManager", "SessionInfo"]
