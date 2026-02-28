#!/usr/bin/env python3
"""JobPilot launcher — starts backend and opens browser."""

import asyncio
import os
import platform
import subprocess
import sys
import webbrowser
from pathlib import Path

import uvicorn  # type: ignore


def check_prerequisites():
    """Verify all dependencies are available."""
    checks = {
        "Database": Path("data/jobpilot.db").parent.exists(),
        "Frontend build": Path("frontend/build").exists(),
        "Tectonic": _find_binary("tectonic"),
    }

    for name, ok in checks.items():
        if not ok:
            print(f"⚠  {name} not found. Run the installer first.")
            sys.exit(1)


def _find_binary(name: str) -> bool:
    """Check if a binary is available (PATH or bundled)."""
    import shutil

    if shutil.which(name):
        return True
    ext = ".exe" if platform.system() == "Windows" else ""
    return (Path("bin") / f"{name}{ext}").exists()


def main():
    check_prerequisites()

    # Ensure data directories exist
    for d in ["data/cvs", "data/letters", "data/templates", "data/browser_sessions", "data/logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    host = "127.0.0.1"
    port = 8000

    print(f"\n  JobPilot starting on http://{host}:{port}\n")

    # Open browser after a short delay
    import threading

    threading.Timer(2.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    # Start FastAPI
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
