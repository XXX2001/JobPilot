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

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

# Canonical profile-directory key. Re-exported so the strategies have a single
# import surface for the shared helpers; this IS the captcha_handler function
# (the old per-strategy ``_site_key`` copies were removed in T4a).
from backend.applier.captcha_handler import site_profile_key

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


def build_browser(browser_kwargs: dict, saved_session_path: Path | None) -> "Browser":
    """Construct a browser-use ``Browser``, loading a saved session if present.

    Mutates *browser_kwargs* in place to add ``storage_state`` /
    ``user_data_dir`` when *saved_session_path* exists, then returns
    ``Browser(**browser_kwargs)``. Callers remain responsible for assigning the
    result to ``self._active_browser`` and for their own session-load logging.
    """
    if _Browser is None:  # pragma: no cover - guarded at the call sites
        raise RuntimeError("browser_use is not available")
    if saved_session_path is not None and saved_session_path.exists():
        browser_kwargs["storage_state"] = saved_session_path.resolve().as_posix()
        browser_kwargs["user_data_dir"] = None
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
