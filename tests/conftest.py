import asyncio
import atexit
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
import pytest

# Ensure project root is in sys.path so tests can import backend package
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── TS-01: Test DB isolation ───────────────────────────────────────────────
# Override JOBPILOT_DATA_DIR with a session-scoped tmp dir BEFORE any backend
# import. backend.database creates the SQLAlchemy engine at import time using
# settings.jobpilot_data_dir, so tests would otherwise share data/jobpilot.db
# with the running app. Doing this at conftest module scope is the only way
# to set the env var before that engine is created.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="jobpilot-test-")
os.environ["JOBPILOT_DATA_DIR"] = _TEST_DATA_DIR
# Also seed dummy credentials so backend.config.Settings() doesn't refuse to
# load when the developer's real .env isn't present (CI / fresh checkouts).
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("ADZUNA_APP_ID", "test-adzuna-id")
os.environ.setdefault("ADZUNA_APP_KEY", "test-adzuna-key")
atexit.register(lambda: shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True))


@pytest.fixture
def test_app():
    """Provide a TestClient for the FastAPI app."""
    import os

    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    from starlette.testclient import TestClient
    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def test_settings(monkeypatch):
    """Return a Settings instance with deterministic test values."""
    import os

    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ADZUNA_APP_ID", "test-adzuna-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-adzuna-key")
    monkeypatch.setenv("JOBPILOT_HOST", "127.0.0.1")
    monkeypatch.setenv("JOBPILOT_PORT", "8000")

    from backend.config import Settings

    return Settings()
