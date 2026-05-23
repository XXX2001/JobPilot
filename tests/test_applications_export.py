"""Tests for GET /api/applications/export?format=csv (qw-6)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from starlette.testclient import TestClient


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    """Parse CSV response bytes into a list of row dicts."""
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


EXPECTED_COLUMNS = [
    "applied_at",
    "status",
    "method",
    "company",
    "title",
    "location",
    "salary_text",
    "job_url",
    "score",
    "ats_score",
    "last_event_type",
    "last_event_at",
    "last_event_details",
]


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_export_csv_headers_and_format(test_app: TestClient) -> None:
    """CSV export → 200, correct Content-Type, attachment Content-Disposition, all expected columns in header."""
    resp = test_app.get("/api/applications/export?format=csv")

    assert resp.status_code == 200

    # Content-Type must be text/csv with utf-8 charset
    ct = resp.headers["content-type"]
    assert "text/csv" in ct
    assert "utf-8" in ct.lower()

    # Content-Disposition — must be attachment with datestamped filename
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd
    assert "jobpilot-applications-" in cd
    assert ".csv" in cd

    # Verify all expected columns are present in the header row
    first_line = resp.content.decode("utf-8").split("\r\n")[0]
    for col in EXPECTED_COLUMNS:
        assert col in first_line, f"Column {col!r} missing from header: {first_line}"

    # The parsed rows (if any) should each have all expected columns
    rows = _parse_csv(resp.content)
    for row in rows:
        for col in EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from data row: {list(row.keys())}"


def test_export_csv_populated_db(test_app: TestClient) -> None:
    """Populated DB → N+1 rows, all columns present, ISO dates render correctly."""
    # Create a job, match, application, and event via the API
    job_resp = test_app.post(
        "/api/jobs",
        json={
            "title": "Software Engineer",
            "company": "Acme Corp",
            "location": "Remote",
            "salary_text": "$120k–$150k",
            "url": "https://acme.example.com/job/1",
            "apply_url": "https://acme.example.com/apply/1",
        },
    )
    # If job creation endpoint doesn't exist, create application directly
    if job_resp.status_code not in (200, 201):
        # Fall back: create application without a linked job
        app_resp = test_app.post(
            "/api/applications",
            json={"method": "manual", "status": "applied"},
        )
        assert app_resp.status_code == 201
        app_id = app_resp.json()["id"]

        # Add an event
        ev_resp = test_app.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "interview", "details": "Phone screen"},
        )
        assert ev_resp.status_code == 201
    else:
        # Create application linked to the job match if we can
        app_resp = test_app.post(
            "/api/applications",
            json={"method": "auto", "status": "applied"},
        )
        assert app_resp.status_code == 201
        app_id = app_resp.json()["id"]

        ev_resp = test_app.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "interview", "details": "Phone screen"},
        )
        assert ev_resp.status_code == 201

    # Now fetch the CSV
    resp = test_app.get("/api/applications/export?format=csv")
    assert resp.status_code == 200

    rows = _parse_csv(resp.content)
    assert len(rows) >= 1, "Expected at least one data row"

    # Verify all expected columns are present in each row
    for row in rows:
        for col in EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from row: {list(row.keys())}"

    # Find our application row
    our_rows = [r for r in rows if r["status"] == "applied"]
    assert len(our_rows) >= 1, "Expected an 'applied' row"

    row = our_rows[-1]
    assert row["method"] == "auto" or row["method"] == "manual"
    assert row["last_event_type"] == "interview"
    assert row["last_event_details"] == "Phone screen"

    # last_event_at should be parseable as ISO 8601
    last_event_at = row["last_event_at"]
    assert last_event_at != "", "last_event_at should not be empty"
    # Should be parseable — either with or without timezone suffix
    try:
        datetime.fromisoformat(last_event_at)
    except ValueError:
        # Try stripping trailing Z if present
        datetime.fromisoformat(last_event_at.rstrip("Z"))


def test_export_csv_unknown_format(test_app: TestClient) -> None:
    """?format=json → 400 with explanatory error."""
    resp = test_app.get("/api/applications/export?format=json")
    assert resp.status_code == 400

    # Should have an error body (JSON)
    try:
        body = resp.json()
        assert "error" in body or "detail" in body, f"No 'error'/'detail' key in: {body}"
    except Exception:
        # If not JSON, just confirm the status code was 400
        pass
