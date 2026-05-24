"""Tests for ``backend.main`` lifespan startup behaviour.

The lifespan context manager runs once at app boot to instantiate every
singleton (Gemini client, batch runner, scraping orchestrator, etc.) and
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
        "gemini",
        "cv_pipeline",
        "letter_pipeline",
        "adzuna",
        "adaptive_scraper",
        "session_manager",
        "scraping_orchestrator",
        "matcher",
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
    runs this AFTER the happy-path test in the same module. Python's import
    cache means modules that already did
    ``from backend.llm.gemini_client import GeminiClient`` keep their bound
    reference even after ``monkeypatch`` reverts the source module — so
    injecting the failure must come last.
    """
    # We need to make EVERY consumer of GeminiClient blow up, not just the
    # symbol in `backend.llm.gemini_client`. Modules that did
    # ``from backend.llm.gemini_client import GeminiClient`` at import time
    # bound the original class locally; patching the source module after the
    # fact doesn't reach them. The lifespan's first construction call goes
    # through `JobAnalyzer`, so we patch *its* binding directly — that's the
    # narrow seam that proves fail-fast works without needing to enumerate
    # every consumer.
    from backend.llm import job_analyzer as ja_module

    class _Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated GeminiClient construction failure")

    monkeypatch.setattr(ja_module, "GeminiClient", _Boom)

    from backend.main import app

    with pytest.raises(RuntimeError, match="simulated GeminiClient construction failure"):
        with TestClient(app):
            pass  # pragma: no cover — we expect startup to fail before yield
