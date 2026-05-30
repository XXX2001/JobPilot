"""Pytest configuration for JobPilot.

Test-DB isolation strategy (T8):

* **One SQLite file per pytest *worker***. With ``pytest-xdist -n auto`` each
  worker process gets its own ``JOBPILOT_DATA_DIR`` under ``tempfile.mkdtemp``.
  Single-process runs get one anyway. This is selected purely by
  ``PYTEST_XDIST_WORKER`` so the layout is symmetric.
* **A function-scoped autouse fixture wipes every table between tests.** This
  keeps the test set order-independent and removes the need for the email-
  prefix workaround (``creds-``/``auth-u1``/``sync-u1``/…) that earlier Gmail
  tests had to use to dodge the unique constraint on
  ``gmail_credentials.email_address``.
* **The data-dir env var is set BEFORE any backend import.** ``backend.database``
  creates its engine at import time using ``settings.jobpilot_data_dir``;
  setting the env var here is the only way to redirect it.

This file deliberately keeps ``init_db()`` as the table-creation hook (so the
Alembic ``upgrade head`` path is exercised against every test DB), rather than
running raw ``Base.metadata.create_all``.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# ── sys.path ───────────────────────────────────────────────────────────────
# Ensure project root is in sys.path so tests can import ``backend``.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Per-worker JOBPILOT_DATA_DIR ───────────────────────────────────────────
# pytest-xdist sets PYTEST_XDIST_WORKER to ``gw0``/``gw1``/… in each worker
# process. We append it to the tmp dir name so workers never share a SQLite
# file. In a single-process run the var is unset and we fall back to ``main``.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "main")
_TEST_DATA_DIR = tempfile.mkdtemp(prefix=f"jobpilot-test-{_WORKER_ID}-")
os.environ["JOBPILOT_DATA_DIR"] = _TEST_DATA_DIR

# Seed dummy credentials so backend.config.Settings() doesn't refuse to load
# on fresh checkouts / CI machines that don't have a real ``.env``.
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("ADZUNA_APP_ID", "test-adzuna-id")
os.environ.setdefault("ADZUNA_APP_KEY", "test-adzuna-key")

atexit.register(lambda: shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True))


# ── Schema bootstrap (once per worker) ─────────────────────────────────────
# ``init_db()`` is idempotent but does a small amount of I/O each call. Run
# it exactly once per worker via a session-scoped fixture; per-test wiping
# below keeps the tables empty after that.


def _run_init_db_sync() -> None:
    """Import ``backend.database.init_db`` lazily and run it on a fresh loop."""
    from backend.database import init_db

    asyncio.run(init_db())


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_test_db():
    """Create tables once per worker, before any test runs."""
    _run_init_db_sync()
    yield


# ── Per-test wipe ──────────────────────────────────────────────────────────
# Between tests, DELETE FROM every table in dependency-safe order. This is
# significantly faster than ``drop_all`` + ``create_all`` and equivalent for
# isolation purposes since we don't depend on auto-incremented IDs being
# reset between tests (those IDs are still unique within a test).
#
# Order matters for the (few) FK relationships we declare with
# ondelete=CASCADE — children first, parents last — so an explicit list is
# more reliable than walking ``Base.metadata.sorted_tables`` in reverse.
_WIPE_ORDER: tuple[str, ...] = (
    "application_correspondence",
    "application_events",
    "applications",
    "tailored_documents",
    "job_matches",
    "jobs",
    "job_sources",
    "gmail_messages",
    "gmail_credentials",
    "site_credentials",
    "browser_sessions",
    "search_settings",
    "user_profile",
)


async def _wipe_all_tables() -> None:
    """DELETE FROM every known table. Idempotent."""
    from sqlalchemy import text

    from backend.database import engine

    async with engine.begin() as conn:
        # Defer FK checks until COMMIT so the wipe order isn't load-bearing
        # for the declared FK constraints. ``defer_foreign_keys`` is scoped to
        # the current transaction and auto-resets at COMMIT, so the persistent
        # ``foreign_keys=ON`` set by the connect listener survives — unlike
        # toggling ``foreign_keys=OFF``, which would leave pooled connections
        # with enforcement disabled for the rest of the session.
        await conn.execute(text("PRAGMA defer_foreign_keys = ON"))
        for table in _WIPE_ORDER:
            try:
                await conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                # Table not present in this engine — fine, skip.
                continue


@pytest.fixture(autouse=True)
def _reset_db_between_tests():
    """Wipe tables before each test for full isolation.

    Wiping BEFORE the test (rather than after) is a deliberate choice:
    if a test fails, its rows remain visible for post-mortem debugging
    via ``sqlite3`` against ``$JOBPILOT_DATA_DIR/jobpilot.db``.
    """
    asyncio.run(_wipe_all_tables())
    yield


# ── App + settings fixtures (unchanged from pre-T8) ────────────────────────


@pytest.fixture
def test_app():
    """Provide a TestClient for the FastAPI app."""
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    from starlette.testclient import TestClient

    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def test_settings(monkeypatch):
    """Return a Settings instance with deterministic test values."""
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ADZUNA_APP_ID", "test-adzuna-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-adzuna-key")
    monkeypatch.setenv("JOBPILOT_HOST", "127.0.0.1")
    monkeypatch.setenv("JOBPILOT_PORT", "8000")

    from backend.config import Settings

    return Settings()
