"""Resolve the local Chromium executable for browser-use / patchright."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_cached_path: str | None = None
_resolved = False


def get_chromium_executable() -> str | None:
    """Return the path to an installed Chromium binary, or None.

    Tries patchright first (patched for bot-detection avoidance), then falls
    back to playwright's Chromium.  The result is cached after the first call.
    """
    global _cached_path, _resolved
    if _resolved:
        return _cached_path

    _resolved = True

    for module in ("patchright", "playwright"):
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"from {module}.sync_api import sync_playwright; "
                    f"p = sync_playwright().start(); "
                    f"print(p.chromium.executable_path); "
                    f"p.stop()",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path and Path(path).exists():
                    logger.debug("Resolved chromium via %s: %s", module, path)
                    _cached_path = path
                    return _cached_path
        except Exception as exc:
            logger.debug("Could not resolve chromium via %s: %s", module, exc)

    logger.warning(
        "No local Chromium binary found via patchright or playwright. "
        "Browser-use will attempt its own install."
    )
    return None
