#!/usr/bin/env python3
"""JobPilot launcher — starts backend and opens browser."""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import uvicorn  # type: ignore

from backend.config import PROJECT_ROOT


def check_prerequisites():
    """Verify all dependencies are available."""
    checks = {
        "Database": (PROJECT_ROOT / "data").exists(),
        "Frontend build": (PROJECT_ROOT / "frontend" / "build").exists(),
        "Tectonic": _find_binary("tectonic"),
    }

    for name, ok in checks.items():
        if not ok:
            print(f"⚠  {name} not found. Run the installer first.")
            sys.exit(1)


def _find_binary(name: str) -> bool:
    """Check if a binary is available (PATH or bundled)."""
    if shutil.which(name):
        return True
    ext = ".exe" if platform.system() == "Windows" else ""
    return (PROJECT_ROOT / "bin" / f"{name}{ext}").exists()


def free_port(port: int) -> None:
    """Kill any process currently listening on *port* so we can bind cleanly."""
    import signal
    import socket

    # Quick check: is the port actually in use?
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return  # port is free, nothing to do

    print(f"  Port {port} is in use — stopping existing process…")

    if platform.system() == "Windows":
        # netstat to find PID, then taskkill
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                break
    else:
        pids: list[str] = []
        if shutil.which("lsof"):
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split()
        elif shutil.which("fuser"):
            result = subprocess.run(
                ["fuser", "-n", "tcp", str(port)],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split()

        for pid_str in pids:
            try:
                pid = int(pid_str)
                os.kill(pid, signal.SIGTERM)
            except (ValueError, ProcessLookupError):
                pass
        if pids:
            import time

            time.sleep(1.5)  # give process time to exit gracefully
            # Force-kill anything still alive
            for pid_str in pids:
                try:
                    pid = int(pid_str)
                    os.kill(pid, signal.SIGKILL)
                except (ValueError, ProcessLookupError):
                    pass
            time.sleep(0.5)

    print(f"  Port {port} freed.")


def main():
    check_prerequisites()

    Path(PROJECT_ROOT).mkdir(parents=True, exist_ok=True)

    # Ensure data directories exist
    for d in [
        "data/cvs",
        "data/letters",
        "data/templates",
        "data/browser_sessions",
        "data/browser_profiles",
        "data/logs",
    ]:
        (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)

    host = "127.0.0.1"
    port = 8000

    free_port(port)

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
