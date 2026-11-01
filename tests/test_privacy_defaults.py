"""W02 / F04, F21 — startup privacy and exposure defaults."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    # A user/host that opted IN must still be overridden by JobPilot's privacy mode.
    env.update(ANONYMIZED_TELEMETRY="true", BROWSER_USE_CLOUD_SYNC="true")
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importing_backend_disables_browser_use_telemetry_client() -> None:
    # Any entry point (uvicorn backend.main:app, start.py, pytest) imports the
    # ``backend`` package before browser_use; no PostHog client may be built.
    code = (
        "import backend\n"
        "from browser_use.telemetry.service import ProductTelemetry\n"
        "from browser_use.config import CONFIG\n"
        "t = ProductTelemetry()\n"
        "print(t._posthog_client is None, CONFIG.BROWSER_USE_CLOUD_SYNC)\n"
    )
    assert _run(code) == "True False"


def test_compose_publishes_only_on_loopback() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    ports = compose["services"]["jobpilot"]["ports"]
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports), ports
