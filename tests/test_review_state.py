"""Tests for the pending-review cache wiring (M1-T1).

The engine owns a ``_pending_reviews`` cache populated by
``record_pending_review``. Strategies must call that method at
``apply_review`` broadcast time via an ``on_review`` callback injected by
the engine, so a reconnecting client can re-fetch the in-flight review
over ``GET /api/applications/{job_id}/review-state``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.applier.engine import ApplicationEngine


def _make_engine() -> ApplicationEngine:
    return ApplicationEngine(api_key="test-key", daily_limit=10)


# ══════════════════════════════════════════════════════════════════════════════
#  Engine injects record_pending_review as on_review into the strategies
# ══════════════════════════════════════════════════════════════════════════════


def test_engine_injects_on_review_into_strategies():
    """The engine must wire record_pending_review into auto/assisted strategies."""
    engine = _make_engine()

    assert engine._auto._on_review == engine.record_pending_review
    assert engine._assisted._on_review == engine.record_pending_review


def test_engine_injects_on_review_into_form_filler():
    """The Tier-1 form filler (owner of its own apply_review broadcast) must
    also receive the callback threaded down through the strategy."""
    engine = _make_engine()

    # The form filler is constructed inside the strategy; the callback must be
    # threaded down to it so the Tier-1 broadcast site can also populate the cache.
    if engine._auto._form_filler is not None:
        assert engine._auto._form_filler._on_review == engine.record_pending_review


def test_on_review_callback_populates_cache():
    """Invoking the injected callback populates the pending-review cache."""
    engine = _make_engine()
    callback = engine._auto._on_review
    assert callback is not None

    callback(42, filled_fields={"#name": "Alice"}, screenshot_b64="abc123")

    payload = engine.get_pending_review(42)
    assert payload == {
        "job_id": 42,
        "filled_fields": {"#name": "Alice"},
        "screenshot_b64": "abc123",
    }


def test_pending_review_cleared_on_confirm():
    engine = _make_engine()
    engine.record_pending_review(7, filled_fields={"#x": "y"}, screenshot_b64=None)
    assert engine.get_pending_review(7) is not None

    engine.signal_confirm(7)
    assert engine.get_pending_review(7) is None


def test_pending_review_cleared_on_cancel():
    engine = _make_engine()
    engine.record_pending_review(8, filled_fields={"#x": "y"}, screenshot_b64=None)
    assert engine.get_pending_review(8) is not None

    engine.signal_cancel(8)
    assert engine.get_pending_review(8) is None


# ══════════════════════════════════════════════════════════════════════════════
#  Auto Tier-2 broadcast site records the snapshot before waiting
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auto_tier2_broadcast_records_pending_review(monkeypatch):
    """The Tier-2 broadcast site must call on_review with the SAME field
    mapping + screenshot it sends over WS, before waiting for confirm/cancel."""
    import backend.applier.auto_apply as mod
    from backend.applier.auto_apply import AutoApplyStrategy

    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", True)
    import backend.llm.factory as _factory
    monkeypatch.setattr(_factory, "make_browser_llm", lambda: MagicMock())
    monkeypatch.setattr(mod, "Browser", MagicMock())

    def fake_agent(task, llm, browser, **_kwargs):
        m = MagicMock()
        from unittest.mock import AsyncMock

        m.run = AsyncMock(
            return_value=MagicMock(
                final_result=MagicMock(return_value='{"name": "Alice"}')
            )
        )
        return m

    monkeypatch.setattr(mod, "Agent", fake_agent)

    recorded: list[dict] = []

    def on_review(job_id, *, filled_fields, screenshot_b64):
        recorded.append(
            {
                "job_id": job_id,
                "filled_fields": filled_fields,
                "screenshot_b64": screenshot_b64,
            }
        )

    strategy = AutoApplyStrategy(api_key="key", on_review=on_review)

    # Cancel immediately so the wait returns without blocking.
    cancel = asyncio.Event()
    cancel.set()

    await strategy._browser_use_apply(
        job_id=55,
        apply_url="https://example.com/job",
        cancel_event=cancel,
        confirm_event=asyncio.Event(),
    )

    assert recorded, "on_review was never called at broadcast time"
    assert recorded[0]["job_id"] == 55
    assert recorded[0]["filled_fields"] == {"name": "Alice"}


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP: GET /review-state returns the cached payload once recorded
# ══════════════════════════════════════════════════════════════════════════════


def test_review_state_http_returns_cached_payload(test_app: TestClient):
    """Once a review is cached, the HTTP endpoint returns the payload."""
    engine = _make_engine()
    engine.record_pending_review(
        123, filled_fields={"#email": "a@b.com"}, screenshot_b64="ZZZ"
    )
    test_app.app.state.apply_engine = engine  # type: ignore[attr-defined]

    resp = test_app.get("/api/applications/123/review-state")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "job_id": 123,
        "filled_fields": {"#email": "a@b.com"},
        "screenshot_b64": "ZZZ",
    }


def test_review_state_http_404_when_no_pending(test_app: TestClient):
    """No cached review → 404."""
    engine = _make_engine()
    test_app.app.state.apply_engine = engine  # type: ignore[attr-defined]

    resp = test_app.get("/api/applications/999/review-state")
    assert resp.status_code == 404
