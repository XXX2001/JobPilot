def test_health_endpoint(test_app):
    r = test_app.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"


def test_config_loads(test_settings):
    # test_settings fixture yields the patched settings object
    assert test_settings.jobpilot_host == "127.0.0.1"


def test_app_starts_without_error():
    # Sanity: ensure creating a TestClient succeeds
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    assert client is not None


def test_stub_routes_respond(test_app):
    # Optional smoke checks for stub endpoints created by T3
    for path in ("/api/jobs", "/api/queue", "/api/applications"):
        resp = test_app.get(path)
        # If route doesn't exist it's okay; ensure we don't raise during request
        assert resp.status_code in (200, 404)
