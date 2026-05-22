"""Verify the WS broadcaster emits wire payloads that validate as ws_models.WSMessage.

After PR-7a, every helper in ``backend.api.ws`` must route through a Pydantic
model in ``backend.api.ws_models`` so the frontend can rely on the
discriminated-union schema. This file is the contract-level guard.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import TypeAdapter

from backend.api.ws import (
    ConnectionManager,
    broadcast_job_assessment,
    broadcast_status,
    manager,
)
from backend.api.ws_models import (
    ApplyResult,
    CaptchaDetected,
    CaptchaResolved,
    JobAssessment,
    LoginRequired,
    Pong,
    Status,
    WSMessage,
)


_ws_adapter = TypeAdapter(WSMessage)


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _validate_payload(payload: str) -> WSMessage:
    """Parse one wire payload through the discriminated union."""
    return _ws_adapter.validate_python(json.loads(payload))


# ---------------------------------------------------------------------------
# manager.broadcast / send_to encoding
# ---------------------------------------------------------------------------


def test_broadcast_encodes_pydantic_model_as_json():
    mgr = ConnectionManager()
    fake = _FakeWS()
    mgr.active_connections["c1"] = fake  # type: ignore[assignment]

    asyncio.run(mgr.broadcast(Status(message="hello", progress=0.25)))

    assert len(fake.sent) == 1
    msg = _validate_payload(fake.sent[0])
    assert isinstance(msg, Status)
    assert msg.message == "hello"
    assert msg.progress == 0.25
    assert msg.type == "status"


def test_send_to_unknown_client_is_noop():
    mgr = ConnectionManager()
    # No connection registered — must not raise.
    asyncio.run(mgr.send_to("ghost", Status(message="x")))


def test_send_to_known_client_emits_typed_payload():
    mgr = ConnectionManager()
    fake = _FakeWS()
    mgr.active_connections["c1"] = fake  # type: ignore[assignment]

    asyncio.run(mgr.send_to("c1", Pong()))

    msg = _validate_payload(fake.sent[0])
    assert isinstance(msg, Pong)
    assert msg.type == "pong"


# ---------------------------------------------------------------------------
# Top-level helpers route through ws_models
# ---------------------------------------------------------------------------


def test_broadcast_status_helper_emits_status_model(monkeypatch):
    fake = _FakeWS()
    # Use the real module-level manager so we exercise the real wiring,
    # but isolate by clearing connections.
    manager.active_connections.clear()
    manager.active_connections["c1"] = fake  # type: ignore[assignment]
    try:
        asyncio.run(broadcast_status("scanning", progress=0.42))
    finally:
        manager.active_connections.clear()

    msg = _validate_payload(fake.sent[0])
    assert isinstance(msg, Status)
    assert msg.message == "scanning"
    assert msg.progress == 0.42


def test_broadcast_job_assessment_helper_emits_jobassessment_model():
    fake = _FakeWS()
    manager.active_connections.clear()
    manager.active_connections["c1"] = fake  # type: ignore[assignment]
    try:
        asyncio.run(
            broadcast_job_assessment(
                match_id=42,
                ats_score=87.4321,
                gap_severity=0.16789,
                decision="modify",
                covered=["python", "django"],
                gaps=[{"skill": "kubernetes", "criticality": 0.8}],
            )
        )
    finally:
        manager.active_connections.clear()

    msg = _validate_payload(fake.sent[0])
    assert isinstance(msg, JobAssessment)
    assert msg.type == "job_progress"  # wire-format discriminator unchanged
    assert msg.match_id == 42
    # broadcaster rounds ats_score to 1dp and gap_severity to 3dp
    assert msg.ats_score == pytest.approx(87.4)
    assert msg.gap_severity == pytest.approx(0.168)
    assert msg.decision == "modify"
    assert msg.covered == ["python", "django"]
    assert msg.gaps == [{"skill": "kubernetes", "criticality": 0.8}]


# ---------------------------------------------------------------------------
# Spot-check that other caller-side models also validate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        LoginRequired(site="indeed.com", browser_window_title="JobPilot login"),
        CaptchaDetected(site="indeed.com", job_id=7, message="please solve"),
        CaptchaResolved(job_id=7),
        ApplyResult(job_id=7, status="submitted", method="auto"),
    ],
)
def test_caller_side_models_validate_as_wsmessage(model):
    """Models built by captcha_handler / session_manager / auto_apply must
    serialize and round-trip through the discriminated union."""
    mgr = ConnectionManager()
    fake = _FakeWS()
    mgr.active_connections["c1"] = fake  # type: ignore[assignment]

    asyncio.run(mgr.broadcast(model))
    parsed = _validate_payload(fake.sent[0])
    assert type(parsed) is type(model)
