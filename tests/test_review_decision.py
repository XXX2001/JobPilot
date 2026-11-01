"""W07 / F11 — review decisions name the reviewed snapshot and are acknowledged once."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from backend.applier.engine import ApplicationEngine, ReviewConflict

MID = 4242


@pytest.fixture
def engine() -> ApplicationEngine:
    e = ApplicationEngine(daily_limit=10)
    e._confirm_events[MID] = asyncio.Event()
    e._cancel_events[MID] = asyncio.Event()
    return e


def _review(engine: ApplicationEngine) -> dict:
    return engine.record_pending_review(MID, filled_fields={"#name": "Ada"}, screenshot_b64=None)


def _approve(engine, snap, key="key-one", fields=None):
    return engine.decide(
        MID,
        review_id=snap["review_id"],
        revision=snap["revision"],
        decision="approve",
        fields=fields or {},
        idempotency_key=key,
    )


def test_premature_approval_before_any_review_is_rejected(engine):
    with pytest.raises(ReviewConflict):
        engine.decide(
            MID, review_id="x", revision=1, decision="approve", fields={}, idempotency_key="k"
        )
    assert not engine._confirm_events[MID].is_set()


def test_approval_of_the_current_review_confirms_and_carries_corrections(engine):
    snap = _review(engine)
    ack = _approve(engine, snap, fields={"#name": "Ada L."})
    assert ack["decision"] == "approve" and ack["revision"] == snap["revision"]
    assert engine._confirm_events[MID].is_set()
    assert engine.get_pending_patches(MID) == {"#name": "Ada L."}


def test_stale_revision_is_rejected(engine):
    """Two tabs: tab A still shows revision 1 after the snapshot changed."""
    old = _review(engine)
    _review(engine)  # page re-filled → revision 2
    with pytest.raises(ReviewConflict):
        _approve(engine, old)
    assert not engine._confirm_events[MID].is_set()


def test_duplicate_command_returns_the_saved_ack_once(engine):
    snap = _review(engine)
    first = _approve(engine, snap, key="same-key")
    assert _approve(engine, snap, key="same-key") == first
    with pytest.raises(ReviewConflict):  # same key, different payload
        _approve(engine, snap, key="same-key", fields={"#name": "Other"})
    with pytest.raises(ReviewConflict):  # consumed review, new key
        _approve(engine, snap, key="another-key")


def test_cancel_decision_cancels(engine):
    snap = _review(engine)
    engine.decide(
        MID,
        review_id=snap["review_id"],
        revision=snap["revision"],
        decision="cancel",
        fields={},
        idempotency_key="c",
    )
    assert engine._cancel_events[MID].is_set() and not engine._confirm_events[MID].is_set()


def test_http_decision_returns_ack_and_409_on_conflict(test_app: TestClient):
    eng: ApplicationEngine = test_app.app.state.apply_engine  # type: ignore[attr-defined]
    eng._confirm_events[MID] = asyncio.Event()
    eng._cancel_events[MID] = asyncio.Event()
    snap = eng.record_pending_review(MID, filled_fields={}, screenshot_b64=None)
    body = {
        "review_id": snap["review_id"],
        "revision": snap["revision"],
        "decision": "approve",
        "fields": {},
        "idempotency_key": "http-key-1",
    }
    try:
        state = test_app.get(f"/api/applications/{MID}/review-state").json()
        assert state["review_id"] == snap["review_id"] and state["revision"] == snap["revision"]

        ok = test_app.post(f"/api/applications/{MID}/review-decision", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json()["decision"] == "approve"
        assert (
            test_app.post(f"/api/applications/{MID}/review-decision", json=body).json() == ok.json()
        )
        stale = test_app.post(
            f"/api/applications/{MID}/review-decision", json={**body, "idempotency_key": "http-key-2"}
        )
        assert stale.status_code == 409
    finally:
        for d in (eng._confirm_events, eng._cancel_events):
            d.pop(MID, None)


def test_legacy_ws_confirm_no_longer_approves(test_app: TestClient):
    eng: ApplicationEngine = test_app.app.state.apply_engine  # type: ignore[attr-defined]
    eng._confirm_events[MID] = asyncio.Event()
    try:
        eng.record_pending_review(MID, filled_fields={}, screenshot_b64=None)
        with test_app.websocket_connect("/ws", headers={"Origin": "http://localhost:8000"}) as ws:
            ws.send_json({"type": "confirm_submit", "job_id": MID})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
        assert not eng._confirm_events[MID].is_set()
    finally:
        eng._confirm_events.pop(MID, None)
