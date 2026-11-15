"""The JobPilot Chrome: the dedicated window the owner logs into once per site.

The app only ever attaches to this profile (``$DATA/jobpilot-chrome``), never to the
owner's everyday Chrome. Logging in is done by hand in the window.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from fastapi import APIRouter, HTTPException, Request

from backend.browser.chrome import (
    chrome_dir,
    ensure_chrome_running,
    running_endpoint,
    show_app,
)
from backend.config import settings

router = APIRouter(prefix="/api/browser", tags=["browser"], redirect_slashes=False)

# Login pages shown by "open". Only sites whose login the app can check and use: a tab
# for a site nothing uses would reopen on every click.
LOGIN_PAGES = {
    "linkedin": "https://www.linkedin.com/login",
    "google": "https://accounts.google.com/ServiceLogin?continue=https://mail.google.com/mail/",
}
# Session cookies for the job source and the Gmail / Google account flow.
SESSION_COOKIES = {
    "linkedin": ("https://www.linkedin.com", "li_at"),
    "google": ("https://mail.google.com", "SID"),
}


def _view_details() -> dict[str, str]:
    if settings.JOBPILOT_CHROME_VIEW_URL:
        return {
            "view_url": settings.JOBPILOT_CHROME_VIEW_URL,
            "view_password_hint": "data/chrome-view.password",
        }
    return {}


def _open_tab(port: int, url: str) -> None:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")
    json.load(urllib.request.urlopen(request, timeout=5))


async def _logged_in(port: int) -> dict[str, bool]:
    from playwright.async_api import async_playwright  # noqa: PLC0415 - heavy, rarely needed

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0]
        return {
            site: any(c["name"] == name for c in await context.cookies(url))
            for site, (url, name) in SESSION_COOKIES.items()
        }


@router.post("/show-app")
async def show_app_here(request: Request):
    """Open (or focus) this app's page in the JobPilot Chrome."""
    if not await asyncio.to_thread(show_app, str(request.base_url).rstrip("/")):
        raise HTTPException(status_code=503, detail="The JobPilot Chrome did not start")
    return {"running": True}


@router.get("/status")
async def browser_status():
    """Whether the JobPilot Chrome is running and which sites hold a session in it."""
    endpoint = await asyncio.to_thread(running_endpoint)
    if endpoint is None:
        return {"running": False, "logged_in": {}, **_view_details()}
    return {"running": True, "logged_in": await _logged_in(endpoint["port"]), **_view_details()}


@router.post("/open")
async def open_browser():
    """Start the JobPilot Chrome (if needed) and show the login pages not yet signed in."""
    if settings.JOBPILOT_CHROME_VIEW_URL:
        # Only the container supervisor owns Chrome's lifecycle and sandbox flags.
        endpoint = await asyncio.to_thread(running_endpoint)
        if endpoint is None:
            raise HTTPException(
                status_code=503,
                detail="Container Chrome is unavailable. See container logs and docker/README.md.",
            )
    else:
        try:
            endpoint = await asyncio.to_thread(ensure_chrome_running, chrome_dir())
        except (RuntimeError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=503, detail=f"Chrome did not start: {exc}") from exc
    logged_in = await _logged_in(endpoint["port"])
    for site, url in LOGIN_PAGES.items():
        if not logged_in.get(site):
            await asyncio.to_thread(_open_tab, endpoint["port"], url)
    return {"running": True, "logged_in": logged_in, **_view_details()}
