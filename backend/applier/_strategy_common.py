"""Shared building blocks for the two Tier-2 apply strategies (M1-T6).

``AutoApplyStrategy`` (``auto_apply.py``) and ``AssistedApplyStrategy``
(``assisted_apply.py``) historically carried literal copies of a handful of
helpers. This module hosts the pieces that were *byte-for-byte* identical so
both strategies import a single implementation.

This is a STRUCTURAL de-duplication only — no control flow or behaviour
changes. The strategies keep their distinct flows (auto submits after
confirmation; assisted leaves the browser open) and their own divergent
prompt scaffolding; only the truly-shared fragments live here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

# Canonical profile-directory key. Re-exported so the strategies have a single
# import surface for the shared helpers; this IS the captcha_handler function
# (the old per-strategy ``_site_key`` copies were removed in T4a).
from backend.applier.captcha_handler import site_profile_key
from backend.browser.chrome import CONTAINER_CHROME_UNAVAILABLE, running_endpoint
from backend.config import settings

if TYPE_CHECKING:
    # Concrete type the Tier-2 strategies instantiate. Imported under
    # TYPE_CHECKING only so the return annotation stays precise without the
    # heavy runtime import; the runtime binding below may be ``None`` when
    # browser_use is not installed, so it is aliased to avoid shadowing.
    from browser_use import Browser

try:
    from browser_use import Browser as _Browser  # type: ignore
except ImportError:
    _Browser = None  # type: ignore

# Sites that require clicking "Apply" / "Easy Apply" before the form appears.
_MULTI_STEP_DOMAINS = {"linkedin.com", "www.linkedin.com"}


def is_multi_step_site(url: str) -> bool:
    """Check if the URL belongs to a site with multi-step application flows."""
    hostname = urlparse(url).hostname or ""
    return hostname.lstrip("www.") in {h.lstrip("www.") for h in _MULTI_STEP_DOMAINS}


def build_browser(
    browser_kwargs: dict, saved_session_path: Path | None, *, apply_url: str | None = None
) -> "Browser":
    """Construct a browser-use ``Browser``, loading a saved session if present.

    Mutates *browser_kwargs* in place to add ``storage_state`` /
    ``user_data_dir`` when *saved_session_path* exists, then returns
    ``Browser(**browser_kwargs)``. Callers remain responsible for handing the
    result to their ``on_browser`` callback and for their own session-load logging.
    """
    if _Browser is None:  # pragma: no cover - guarded at the call sites
        raise RuntimeError("browser_use is not available")
    if saved_session_path is not None and saved_session_path.exists():
        browser_kwargs["storage_state"] = saved_session_path.resolve().as_posix()
        browser_kwargs["user_data_dir"] = None
    if apply_url:
        # W08: page text cannot send the agent elsewhere — navigation is
        # limited to the apply site (and its parent domain for SSO/ATS hops).
        host = (urlparse(apply_url).hostname or "").lower()
        # ponytail: no public-suffix list (co.uk etc. widen to *.co.uk)
        parent = ".".join(host.split(".")[-2:])
        browser_kwargs["allowed_domains"] = sorted({host, parent, f"*.{parent}"})
    # W08: a site-delivered download must never become an uploadable file.
    browser_kwargs["accept_downloads"] = False
    browser_kwargs["auto_download_pdfs"] = False
    endpoint = running_endpoint()
    if endpoint is not None:
        # The owner's logged-in JobPilot Chrome: attach, never launch. No launch flags,
        # no saved-session file, no disable_security, and no granted site permissions.
        for key in ("headless", "storage_state", "user_data_dir", "disable_security", "args"):
            browser_kwargs.pop(key, None)
        browser_kwargs["cdp_url"] = f"http://127.0.0.1:{endpoint['port']}"
        browser_kwargs["permissions"] = []
        browser_kwargs["keep_alive"] = True
        return _Browser(**browser_kwargs)
    if settings.JOBPILOT_CHROME_VIEW_URL:
        raise RuntimeError(CONTAINER_CHROME_UNAVAILABLE)
    # Preserve the existing native fallback; containers must only attach above.
    browser_kwargs.setdefault("chromium_sandbox", False)
    extra_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    existing_args = list(browser_kwargs.get("args") or [])
    browser_kwargs["args"] = existing_args + [a for a in extra_args if a not in existing_args]
    return _Browser(**browser_kwargs)


# Identical phone-prefix guidance embedded in both strategies' fill prompts.
# The surrounding prompt scaffolding diverges meaningfully between auto and
# assisted, so only this byte-for-byte-identical fragment is shared.
PHONE_NUMBER_NOTE = (
    "\n  NOTE on phone number: Some websites auto-fill the country code prefix "
    "(e.g. +33 for France). If you see the country code is already pre-filled "
    "in the phone field, enter ONLY the local part without the country code "
    "to avoid duplication like '+33+33612345678'.\n"
)


__all__ = [
    "site_profile_key",
    "is_multi_step_site",
    "build_browser",
    "PHONE_NUMBER_NOTE",
]


#: W08: actions the preparation/submit browser agents never get — page text
#: cannot redirect the attempt, run scripts, or write files.
# ``navigate`` stays (browser-use opens the task URL through it); where it can
# go is limited by ``allowed_domains`` in build_browser.
_FORBIDDEN_ACTIONS = ("search", "switch", "close", "evaluate",
                      "write_file", "replace_file", "read_file", "save_as_pdf")


async def open_own_tab(browser: "Browser", apply_url: str) -> None:
    """Attached to the owner's Chrome: work in a new tab, not in the one they have open."""
    if running_endpoint() is None:
        return
    await browser.start()
    await browser.navigate_to(apply_url, new_tab=True)


async def run_capped(agent):
    """Run a browser agent under the step cap and the wall-clock cap.

    Raises ``TimeoutError`` past the time cap; a run that hits the step cap
    returns a history that is not done. Callers treat both as "not applied".
    """
    return await asyncio.wait_for(
        agent.run(max_steps=settings.APPLY_TIER2_MAX_STEPS),
        timeout=settings.APPLY_TIER2_TIMEOUT_SECONDS,
    )


def restricted_tools(*, allow_upload: bool):
    """browser-use ``Tools`` without navigation/script/file actions.

    Uploads stay limited to the Agent's ``available_file_paths``; the submit
    agent gets no upload action at all. ``click`` cannot be narrowed to "not
    the submit button", so preparation is additionally checked by
    ``submitted_during_preparation``.
    """
    from browser_use.tools.service import Tools  # type: ignore

    excluded = list(_FORBIDDEN_ACTIONS) + ([] if allow_upload else ["upload_file"])
    return Tools(exclude_actions=excluded)

