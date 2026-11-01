"""W07 / F04, F11 — local access boundary for HTTP commands and the WebSocket."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

GOOD_ORIGIN = "http://localhost:8000"


@pytest.fixture
def anon(test_app: TestClient) -> TestClient:
    """The shared client with its session cookie removed."""
    test_app.cookies.clear()
    return test_app


def test_session_endpoint_sets_a_strict_httponly_cookie(anon):
    resp = anon.get("/api/session")
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert "jobpilot_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "jobpilot_session" not in resp.text  # the secret is not echoed in the body


def test_state_change_without_session_is_rejected(anon):
    assert anon.post("/api/applications", json={"method": "manual"}).status_code == 401


def test_state_change_with_session_passes(anon):
    anon.get("/api/session")
    assert anon.post("/api/applications", json={"method": "manual"}).status_code == 201


def test_untrusted_origin_cannot_change_state_even_with_session(anon):
    anon.get("/api/session")
    resp = anon.post(
        "/api/applications", json={"method": "manual"}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403


def test_cross_site_fetch_metadata_is_rejected(anon):
    anon.get("/api/session")
    resp = anon.post(
        "/api/applications", json={"method": "manual"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert resp.status_code == 403


def test_unknown_host_header_is_rejected_for_reads_too(anon):
    """DNS rebinding: a page on attacker.example resolving to 127.0.0.1."""
    assert anon.get("/api/applications", headers={"Host": "attacker.example"}).status_code == 403


def test_reads_do_not_need_a_session(anon):
    assert anon.get("/api/applications").status_code == 200


def test_websocket_requires_session_and_trusted_origin(anon):
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws", headers={"Origin": GOOD_ORIGIN}):
            pass  # pragma: no cover — no session cookie
    anon.get("/api/session")
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
            pass  # pragma: no cover
    with anon.websocket_connect("/ws", headers={"Origin": GOOD_ORIGIN}) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
