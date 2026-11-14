"""Tests for ``backend.main`` lifespan startup behaviour.

The lifespan context manager runs once at app boot to instantiate every
singleton (LLM client, batch runner, scraping orchestrator, etc.) and
attach them to ``app.state``. Prior to this fix the ``except`` clause
demoted any failure to a ``logger.warning(...)`` — production could boot
half-broken with no signal, and the first real request would 5xx because
``app.state.apply_engine`` was never set.

These tests verify that:
  1. A singleton-init failure now aborts startup with a re-raised exception
     (deep-dive CRIT —
     ``docs/reports/2026-05-23-codebase-deep-dive/01-app-shell-and-api.md``).
  2. The happy-path lifespan still boots the app cleanly and populates
     ``app.state`` (regression guard so the fail-fast rule doesn't go too
     far the other way).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


def test_happy_path_lifespan_populates_app_state(test_app: TestClient) -> None:
    """Smoke regression — fail-fast must not break the normal startup path.

    Uses the shared ``test_app`` fixture which already drives lifespan once.
    If singleton init now raises spuriously, this test would never reach the
    assertions because the fixture itself would error out.

    Ordered FIRST so the lifespan boots once cleanly before the
    failure-injection test in this module monkeypatches anything (avoids
    Python's per-module import-binding cache from poisoning later tests).
    """
    state = test_app.app.state  # type: ignore[attr-defined]

    # Spot-check the singletons the lifespan promises. We only verify
    # *presence* (not exact types) so this test stays decoupled from internal
    # signatures; type-level guarantees live in pyright.
    for attr in (
        "llm",
        "cv_pipeline",
        "letter_pipeline",
        "adzuna",
        "adaptive_scraper",
        "session_manager",
        "scraping_orchestrator",
        "apply_engine",
        "batch_runner",
    ):
        assert getattr(state, attr, None) is not None, (
            f"app.state.{attr} missing after lifespan startup — singleton "
            "init may have silently failed"
        )


def test_zz_singleton_init_failure_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A boom in singleton init must NOT be swallowed — startup must abort.

    Pre-fix behaviour: ``lifespan`` caught the exception and called
    ``logger.warning(...)``. The app finished booting with ``app.state``
    missing ``apply_engine`` / ``batch_runner`` / etc., and every request
    that needed them 5xx'd. The test client lifespan ENTRY would succeed,
    hiding the failure entirely.

    Post-fix: the exception is re-raised, ``TestClient.__enter__`` propagates
    it, and the operator sees the original traceback.

    Named with a ``zz_`` prefix so pytest's default name-ordered collection
    runs this AFTER the happy-path test in the same module.
    """
    # The lifespan builds every generation client through the provider factory
    # (``backend.llm.factory.make_llm_client``); its very first construction
    # call in the warmup is ``gen_client = make_llm_client()``. Because the
    # warmup imports the factory function-locally, patching the factory module
    # attribute is resolved at call time — that's the narrow seam that proves
    # fail-fast works without enumerating every consumer.
    from backend.llm import factory as factory_module

    def _boom() -> object:
        raise RuntimeError("simulated LLM client construction failure")

    monkeypatch.setattr(factory_module, "make_llm_client", _boom)

    from backend.main import app

    with pytest.raises(RuntimeError, match="simulated LLM client construction failure"):
        with TestClient(app):
            pass  # pragma: no cover — we expect startup to fail before yield


def test_zz_migration_failure_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """W02 / F20 — a failed schema migration must abort startup, not log and continue."""
    from backend import database

    async def _boom() -> None:
        raise RuntimeError("simulated alembic upgrade failure")

    monkeypatch.setattr(database, "init_db", _boom)

    from backend.main import app

    with pytest.raises(RuntimeError, match="simulated alembic upgrade failure"):
        with TestClient(app):
            pass  # pragma: no cover


@pytest.mark.parametrize("enabled", [False, True])
def test_lifespan_registers_monitor_tick_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """The monitor polls nothing unless the owner turned it on (MONITOR_ENABLED)."""
    from backend.config import Settings, settings
    from backend.main import app

    assert Settings.model_fields["MONITOR_ENABLED"].default is False
    # Set both ways explicitly: a developer's .env must not decide this test.
    monkeypatch.setattr(settings, "MONITOR_ENABLED", enabled)
    monkeypatch.setattr(settings, "MONITOR_TICK_SECONDS", 45)

    with TestClient(app) as client:
        scheduler = client.app.state.scheduler  # type: ignore[attr-defined]
        job = scheduler.get_job("monitor_tick")
        assert scheduler.get_job("gmail_poll") is not None
        if not enabled:
            assert job is None
            return
        assert job is not None
        assert job.trigger.interval.total_seconds() == 45
        assert job.max_instances == 1 and job.coalesce is True
