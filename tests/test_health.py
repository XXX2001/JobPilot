"""Tests for GET /api/health — OBS-03.

The endpoint must:
- Actually ping the DB (SELECT 1), not just hard-code "connected".
- Return 503 when the DB is unreachable.
- Never leak raw exception text — only a short error code.
- Include version, ISO-8601 UTC timestamp, tectonic and gemini_key_set flags.
"""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient


# ─── Happy path ───────────────────────────────────────────────────────────────


def test_health_happy_path_returns_200_and_db_ok(test_app: TestClient) -> None:
    """DB is reachable in the test env — endpoint should report db == 'ok'."""
    resp = test_app.get("/api/health")
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["version"] == "0.1.0"
    # Error code must be absent / None on the happy path.
    assert data.get("db_error_code") in (None,)


def test_health_response_shape_matches_healthout_schema(test_app: TestClient) -> None:
    """Response body has the documented HealthOut shape."""
    resp = test_app.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()

    expected_keys = {
        "status",
        "version",
        "timestamp",
        "db",
        "tectonic",
        "gemini_key_set",
        "tectonic_hint",
        "db_error_code",
    }
    assert expected_keys.issubset(data.keys()), (
        f"Missing keys: {expected_keys - set(data.keys())}"
    )


def test_health_timestamp_is_iso8601_utc_aware(test_app: TestClient) -> None:
    """`timestamp` is a parseable ISO-8601 string with a timezone offset."""
    resp = test_app.get("/api/health")
    assert resp.status_code == 200
    ts = resp.json()["timestamp"]
    assert isinstance(ts, str)

    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "timestamp must be timezone-aware"
    # Should be roughly "now" — within a generous skew, just to catch
    # accidental Epoch-zero / fixed-string regressions.
    skew = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert skew < 60, f"timestamp drift {skew}s — likely hard-coded"


def test_health_flags_reflect_test_env(test_app: TestClient) -> None:
    """tectonic and gemini_key_set are booleans matching the test env.

    conftest seeds GOOGLE_API_KEY='test-key-not-real', so gemini_key_set
    must be True (it's not in {"", None, "placeholder"}). Tectonic
    presence depends on whether the test host has it installed — we just
    assert the value is a bool.
    """
    resp = test_app.get("/api/health")
    data = resp.json()

    assert isinstance(data["tectonic"], bool)
    assert isinstance(data["gemini_key_set"], bool)
    assert data["gemini_key_set"] is True, (
        "conftest sets GOOGLE_API_KEY=test-key-not-real, which should count as set"
    )

    # If tectonic is missing the hint should be populated; if present, hint is None.
    if data["tectonic"]:
        assert data["tectonic_hint"] is None
    else:
        assert isinstance(data["tectonic_hint"], str)
        assert "tectonic" in data["tectonic_hint"].lower()


# ─── DB failure path ──────────────────────────────────────────────────────────


def test_health_returns_503_when_db_session_factory_raises(
    test_app: TestClient,
    monkeypatch,
) -> None:
    """If the session factory blows up, /api/health must return 503 with db=='error'."""
    from backend import database as db_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated DB outage — connection refused on /tmp/xyz")

    # Patch the symbol the endpoint actually reads (it does
    # `from backend import database as _db; _db.AsyncSessionLocal()`).
    monkeypatch.setattr(db_module, "AsyncSessionLocal", _boom)

    resp = test_app.get("/api/health")
    assert resp.status_code == 503

    data = resp.json()
    assert data["db"] == "error"
    assert data["status"] == "degraded"
    assert data["db_error_code"] == "db_unreachable"

    # EH-05: raw exception text must NOT be leaked in the response body.
    body_blob = resp.text.lower()
    assert "simulated db outage" not in body_blob
    assert "connection refused" not in body_blob
    assert "/tmp/xyz" not in body_blob
    assert "traceback" not in body_blob


def test_health_returns_503_when_select1_returns_wrong_value(
    test_app: TestClient,
    monkeypatch,
) -> None:
    """A DB that responds but returns garbage is still treated as unhealthy."""
    from backend import database as db_module

    class _BadResult:
        def scalar_one(self):
            return 42  # wrong — endpoint expects 1

    class _BadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_args, **_kwargs):
            return _BadResult()

    def _factory(*_args, **_kwargs):
        return _BadSession()

    monkeypatch.setattr(db_module, "AsyncSessionLocal", _factory)

    resp = test_app.get("/api/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["db"] == "error"
    assert data["status"] == "degraded"
    assert data["db_error_code"] == "db_unreachable"
