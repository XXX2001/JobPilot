"""Integration tests for the full JobPilot pipeline (T32).

These tests exercise multiple layers together (DB + API + business logic)
using the real FastAPI TestClient with an in-memory SQLite database.
All external calls (Gemini, Adzuna, browser-use) are mocked.
"""

from __future__ import annotations

import asyncio
import unittest.mock as mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(test_app: TestClient):
    """Alias fixture for the shared test_app client."""
    return test_app


# ─── T1: Adzuna → Job Queue flow ──────────────────────────────────────────────


def test_job_search_flow(client: TestClient):
    """POST /api/jobs/search returns 200 and stores jobs."""
    from backend.models.schemas import RawJob

    fake_raw = RawJob(
        title="Python Backend Engineer",
        company="TechCorp",
        location="Paris, France",
        description="Python, FastAPI, Docker",
        url="https://api.adzuna.com/jobs/1",
        apply_url="https://techcorp.com/apply/1",
        apply_method="redirect",
        source_name="adzuna",
        salary_text="50000-70000 EUR",
    )

    # Patch AdzunaClient.search so no real HTTP call is made
    with patch(
        "backend.scraping.adzuna_client.AdzunaClient.search",
        new_callable=AsyncMock,
        return_value=[fake_raw],
    ):
        resp = client.post(
            "/api/jobs/search",
            json={"keywords": ["python", "fastapi"], "location": "Paris"},
        )

    # Search endpoint must return 200 with stored count
    assert resp.status_code == 200
    data = resp.json()
    assert "stored" in data or "jobs" in data or isinstance(data, list)
def test_job_list_returns_200(client: TestClient):
    """GET /api/jobs returns 200 with a jobs list."""
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data or isinstance(data, list)


def test_queue_list_returns_200(client: TestClient):
    """GET /api/queue returns 200 with a queue list."""
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    # Response shape: {"matches": [...], "total": int}
    assert "matches" in data or "items" in data or isinstance(data, list)

# ─── T2: CV tailoring pipeline (unit-level integration) ───────────────────────


@pytest.mark.asyncio
async def test_cv_tailoring_pipeline_end_to_end(tmp_path):
    """CVPipeline+LaTeXInjector runs on a real .tex file without crashing."""
    from backend.latex.pipeline import CVPipeline
    from backend.models.schemas import JobDetails

    base_tex = tmp_path / "base.tex"
    base_tex = tmp_path / "base.tex"
    # Minimal valid LaTeX with JOBPILOT markers in the correct format
    # The parser uses: % --- JOBPILOT:SUMMARY:START ---
    base_tex.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "% --- JOBPILOT:SUMMARY:START ---\n"
        "Senior Python developer with 5 years experience.\n"
        "% --- JOBPILOT:SUMMARY:END ---\n"
        "\\end{document}\n"
    )

    output_dir = tmp_path / "out"

    # Mock compiler so we don't need Tectonic installed
    fake_pdf = tmp_path / "cv.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")
    fake_compiler = MagicMock()

    async def _compile(tex_path, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        return fake_pdf

    fake_compiler.compile = _compile

    # Mock CV editor that returns a valid summary edit
    from pydantic import BaseModel

    class FakeSummaryEdit(BaseModel):
        edited_summary: str = "Experienced Python dev with FastAPI expertise."
        changes_made: list[str] = ["Added FastAPI mention"]

    fake_editor = MagicMock()
    fake_editor.edit_summary = AsyncMock(return_value=FakeSummaryEdit())
    fake_editor.edit_experience = AsyncMock(return_value=None)

    pipeline = CVPipeline(compiler=fake_compiler, cv_editor=fake_editor)

    job = JobDetails(
        title="Senior FastAPI Developer",
        company="StartupXYZ",
        location="Remote",
        description="We need an expert in Python and FastAPI.",
        url="https://startupxyz.com/jobs/42",
        apply_url="https://startupxyz.com/jobs/42/apply",
        apply_method="redirect",
    )

    result = await pipeline.generate_tailored_cv(base_tex, job, output_dir)

    assert result.pdf_path.exists()
    assert result.cv_tailored is True
    assert len(result.diff) >= 1
    assert result.diff[0].section == "summary"


# ─── T3: Manual apply flow (API) ──────────────────────────────────────────────


def test_manual_apply_flow(client: TestClient):
    """Create application → update status → add event: full lifecycle."""
    # 1. Create application
    resp = client.post(
        "/api/applications",
        json={"method": "manual", "status": "pending"},
    )
    assert resp.status_code == 201
    app_id = resp.json()["id"]

    # 2. Update status to applied
    patch_resp = client.patch(
        f"/api/applications/{app_id}",
        json={"status": "applied"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "applied"

    # 3. Add a lifecycle event
    event_resp = client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "confirmation_email", "details": "Got email from recruiter"},
    )
    assert event_resp.status_code == 201

    # 4. Retrieve final state
    get_resp = client.get(f"/api/applications/{app_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["status"] == "applied"
    assert len(data["events"]) >= 1


# ─── T4: Settings persistence ─────────────────────────────────────────────────


def test_settings_persistence(client: TestClient):
    """PUT /api/settings/search persists and GET retrieves correctly."""
    payload = {
        "keywords": {"include": ["python", "django"], "exclude": ["junior"]},
        "remote_only": True,
        "daily_limit": 7,
        "min_match_score": 45.0,
    }

    put_resp = client.put("/api/settings/search", json=payload)
    assert put_resp.status_code == 200

    get_resp = client.get("/api/settings/search")
    assert get_resp.status_code == 200
    data = get_resp.json()

    assert data["remote_only"] is True
    assert data["daily_limit"] == 7
    assert data["min_match_score"] == 45.0
    assert "python" in data["keywords"]["include"]


def test_profile_persistence(client: TestClient):
    """PUT /api/settings/profile persists full_name and email."""
    put_resp = client.put(
        "/api/settings/profile",
        json={
            "full_name": "Alice Smith",
            "email": "alice@example.com",
            "phone": "+33 6 12 34 56 78",
        },
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/api/settings/profile")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["full_name"] == "Alice Smith"
    assert data["email"] == "alice@example.com"
    assert data["phone"] == "+33 6 12 34 56 78"


# ─── T5: Health endpoint with Tectonic flag ───────────────────────────────────


def test_health_endpoint_structure(client: TestClient):
    """GET /api/health returns required keys."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "tectonic" in data
    assert "gemini_key_set" in data
    assert isinstance(data["tectonic"], bool)


def test_health_tectonic_hint_when_missing(client: TestClient):
    """GET /api/health includes tectonic_hint when Tectonic not found."""
    with (
        patch("shutil.which", return_value=None),
        patch("pathlib.Path.exists", return_value=False),
    ):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # If tectonic is False, hint should be present
    if not data.get("tectonic", True):
        assert "tectonic_hint" in data


# ─── T6: Analytics summary ────────────────────────────────────────────────────


def test_analytics_end_to_end(client: TestClient):
    """Create applications then verify analytics totals reflect them."""
    # Baseline
    baseline = client.get("/api/analytics/summary").json()
    baseline_total = baseline.get("total_apps", 0)

    # Create 2 new applications
    for _ in range(2):
        client.post(
            "/api/applications",
            json={"method": "manual", "status": "applied"},
        )

    summary = client.get("/api/analytics/summary").json()
    assert summary["total_apps"] >= baseline_total + 2
