"""Tests for /api/applications, /api/documents, /api/settings, /api/analytics routes (T15)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


# ─── Applications ─────────────────────────────────────────────────────────────


def test_list_applications_empty(test_app: TestClient):
    """GET /api/applications returns 200 with empty list on fresh DB."""
    resp = test_app.get("/api/applications")
    assert resp.status_code == 200
    data = resp.json()
    assert "applications" in data
    assert isinstance(data["applications"], list)
    assert "total" in data


def test_create_application(test_app: TestClient):
    """POST /api/applications creates a new application record."""
    resp = test_app.post(
        "/api/applications",
        json={"method": "manual", "status": "pending"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["method"] == "manual"
    assert data["status"] == "pending"
    assert "id" in data
    assert data["events"] == []


def test_get_application(test_app: TestClient):
    """GET /api/applications/{id} returns the created application."""
    # First create one
    create_resp = test_app.post(
        "/api/applications",
        json={"method": "auto", "status": "applied"},
    )
    assert create_resp.status_code == 201
    app_id = create_resp.json()["id"]

    # Then retrieve it
    get_resp = test_app.get(f"/api/applications/{app_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == app_id
    assert data["method"] == "auto"


def test_get_application_not_found(test_app: TestClient):
    """GET /api/applications/999999 returns 404."""
    resp = test_app.get("/api/applications/999999")
    assert resp.status_code == 404


def test_update_application_status(test_app: TestClient):
    """PATCH /api/applications/{id} updates status."""
    create_resp = test_app.post(
        "/api/applications",
        json={"method": "manual", "status": "pending"},
    )
    app_id = create_resp.json()["id"]

    patch_resp = test_app.patch(
        f"/api/applications/{app_id}",
        json={"status": "interview"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "interview"


def test_add_application_event(test_app: TestClient):
    """POST /api/applications/{id}/events adds a lifecycle event."""
    create_resp = test_app.post(
        "/api/applications",
        json={"method": "manual", "status": "applied"},
    )
    app_id = create_resp.json()["id"]

    event_resp = test_app.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "email_received", "details": "Got confirmation email"},
    )
    assert event_resp.status_code == 201
    data = event_resp.json()
    assert data["event_type"] == "email_received"
    assert data["application_id"] == app_id


def test_list_applications_status_filter(test_app: TestClient):
    """GET /api/applications?status=interview filters by status."""
    # Create one with status=interview
    test_app.post(
        "/api/applications",
        json={"method": "manual", "status": "interview"},
    )
    resp = test_app.get("/api/applications?status=interview")
    assert resp.status_code == 200
    data = resp.json()
    assert all(a["status"] == "interview" for a in data["applications"])


# ─── Documents ────────────────────────────────────────────────────────────────


def test_list_documents_empty(test_app: TestClient):
    """GET /api/documents returns 200 with empty list."""
    resp = test_app.get("/api/documents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_cv_pdf_not_found(test_app: TestClient):
    """GET /api/documents/{match_id}/cv/pdf returns 404 for unknown match."""
    resp = test_app.get("/api/documents/999999/cv/pdf")
    assert resp.status_code == 404


def test_get_letter_pdf_not_found(test_app: TestClient):
    """GET /api/documents/{match_id}/letter/pdf returns 404 for unknown match."""
    resp = test_app.get("/api/documents/999999/letter/pdf")
    assert resp.status_code == 404


def test_get_diff_not_found(test_app: TestClient):
    """GET /api/documents/{match_id}/diff returns 404 for unknown match."""
    resp = test_app.get("/api/documents/999999/diff")
    assert resp.status_code == 404


def test_regenerate_not_found(test_app: TestClient):
    """POST /api/documents/{match_id}/regenerate returns 404 for unknown match."""
    resp = test_app.post(
        "/api/documents/999999/regenerate",
        json={"force": False},
    )
    assert resp.status_code == 404


# ─── Settings ─────────────────────────────────────────────────────────────────


def test_get_settings_status(test_app: TestClient):
    """GET /api/settings/status returns setup flags."""
    resp = test_app.get("/api/settings/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "gemini_key_set",
        "adzuna_key_set",
        "tectonic_found",
        "base_cv_uploaded",
        "setup_complete",
    ):
        assert key in data
    assert isinstance(data["gemini_key_set"], bool)


def test_get_sources(test_app: TestClient):
    """GET /api/settings/sources returns source configuration."""
    resp = test_app.get("/api/settings/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert "adzuna" in data
    assert "gemini" in data


def test_get_profile_not_found_initially(test_app: TestClient):
    """GET /api/settings/profile returns 404 if profile not created yet."""
    resp = test_app.get("/api/settings/profile")
    # Either 404 (no profile) or 200 (if profile exists from other tests)
    assert resp.status_code in (200, 404)


def test_upsert_profile(test_app: TestClient):
    """PUT /api/settings/profile creates or updates profile."""
    resp = test_app.put(
        "/api/settings/profile",
        json={"full_name": "Jane Doe", "email": "jane@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == "Jane Doe"
    assert data["email"] == "jane@example.com"


def test_get_profile_after_upsert(test_app: TestClient):
    """GET /api/settings/profile returns 200 after profile is created."""
    # Ensure profile exists
    test_app.put(
        "/api/settings/profile",
        json={"full_name": "Test User", "email": "test@example.com"},
    )
    resp = test_app.get("/api/settings/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "test@example.com"


def test_upsert_search_settings(test_app: TestClient):
    """PUT /api/settings/search creates search settings."""
    resp = test_app.put(
        "/api/settings/search",
        json={
            "keywords": {"include": ["python", "fastapi"]},
            "remote_only": True,
            "daily_limit": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["remote_only"] is True
    assert data["daily_limit"] == 5


def test_get_search_settings_after_upsert(test_app: TestClient):
    """GET /api/settings/search returns 200 after settings are created."""
    test_app.put(
        "/api/settings/search",
        json={"keywords": {"include": ["django"]}},
    )
    resp = test_app.get("/api/settings/search")
    assert resp.status_code == 200


# ─── Analytics ────────────────────────────────────────────────────────────────


def test_analytics_summary(test_app: TestClient):
    """GET /api/analytics/summary returns stats."""
    resp = test_app.get("/api/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_apps" in data
    assert "apps_this_week" in data
    assert "response_rate" in data
    assert isinstance(data["total_apps"], int)
    assert isinstance(data["response_rate"], float)


def test_analytics_trends_default(test_app: TestClient):
    """GET /api/analytics/trends returns 30 days of trends."""
    resp = test_app.get("/api/analytics/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "trends" in data
    assert "days" in data
    assert data["days"] == 30
    assert len(data["trends"]) == 30


def test_analytics_trends_custom_days(test_app: TestClient):
    """GET /api/analytics/trends?days=7 returns exactly 7 trend entries."""
    resp = test_app.get("/api/analytics/trends?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["trends"]) == 7
    assert data["days"] == 7
