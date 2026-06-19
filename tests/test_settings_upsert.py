"""Characterization tests for the singleton upsert helper behind the settings PUT routes (M1-T3).

These lock in the exact behavior of ``backend.api.settings._upsert_singleton`` as
exercised through ``PUT /api/settings/profile`` and ``PUT /api/settings/search``:

(a) a partial PUT updates only the fields the client sent and leaves others intact;
(b) the first-ever PUT (no row yet) creates the singleton row with the correct
    create-path defaults merged with the provided values;
(c) a second PUT updates the provided fields without wiping unspecified ones.

The DB is wiped before every test (see ``conftest._reset_db_between_tests``), so the
first PUT in each test reliably hits the "no row" create branch.
"""

from __future__ import annotations

from starlette.testclient import TestClient


# ─── (b) First-ever PUT creates the row with correct defaults ─────────────────


def test_first_search_put_creates_row_with_defaults(test_app: TestClient) -> None:
    """The first PUT /api/settings/search (no existing row) must persist the
    documented create-path defaults for every field the client did not send."""
    resp = test_app.put(
        "/api/settings/search",
        json={"keywords": {"include": ["python"]}},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Provided value survives.
    assert data["keywords"] == {"include": ["python"]}

    # Helper-supplied create defaults.
    assert data["remote_only"] is False
    assert data["daily_limit"] == 10
    assert data["min_match_score"] == 30.0
    assert data["cv_modification_sensitivity"] == "balanced"
    assert data["cv_tailoring_enabled"] is True
    assert data["max_results_per_source"] == 20

    # Unsent, nullable columns default to None (ORM column defaults).
    assert data["excluded_keywords"] is None
    assert data["locations"] is None
    assert data["salary_min"] is None
    assert data["experience_min"] is None
    assert data["experience_max"] is None
    assert data["job_types"] is None
    assert data["languages"] is None
    assert data["excluded_companies"] is None
    assert data["countries"] is None
    assert data["max_job_age_days"] is None


def test_first_profile_put_creates_row_with_defaults(test_app: TestClient) -> None:
    """The first PUT /api/settings/profile (no existing row) creates the row with
    the provided values and leaves unsent optional fields as None."""
    resp = test_app.put(
        "/api/settings/profile",
        json={"full_name": "Jane Doe", "email": "jane@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["full_name"] == "Jane Doe"
    assert data["email"] == "jane@example.com"
    assert data["id"] == 1

    # Unsent optional fields stay None.
    assert data["phone"] is None
    assert data["location"] is None
    assert data["linkedin_url"] is None
    assert data["driver_license"] is None
    assert data["mobility"] is None
    assert data["base_cv_path"] is None
    assert data["base_letter_path"] is None
    assert data["additional_info"] is None


def test_first_profile_put_empty_body_uses_blank_name_email_defaults(
    test_app: TestClient,
) -> None:
    """An initial profile PUT with no fields must still create the row using the
    helper's ``full_name=""`` / ``email=""`` create defaults (NOT NULL columns)."""
    resp = test_app.put("/api/settings/profile", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == ""
    assert data["email"] == ""


# ─── (a) + (c) Partial PUT updates only provided fields ───────────────────────


def test_search_partial_put_updates_only_sent_fields(test_app: TestClient) -> None:
    """After a rich first PUT, a partial second PUT changes only the sent fields
    and preserves everything else exactly."""
    # First PUT: populate many fields with non-default values.
    r1 = test_app.put(
        "/api/settings/search",
        json={
            "keywords": {"include": ["go"]},
            "remote_only": True,
            "daily_limit": 42,
            "min_match_score": 75.5,
            "salary_min": 60000,
            "max_results_per_source": 99,
        },
    )
    assert r1.status_code == 200

    # Second PUT: change only daily_limit.
    r2 = test_app.put(
        "/api/settings/search",
        json={"daily_limit": 7},
    )
    assert r2.status_code == 200
    data = r2.json()

    # Changed field reflects the new value.
    assert data["daily_limit"] == 7

    # Every other previously-set field is untouched.
    assert data["keywords"] == {"include": ["go"]}
    assert data["remote_only"] is True
    assert data["min_match_score"] == 75.5
    assert data["salary_min"] == 60000
    assert data["max_results_per_source"] == 99


def test_profile_partial_put_updates_only_sent_fields(test_app: TestClient) -> None:
    """After populating several profile fields, a partial PUT updates only the
    sent field and preserves the rest (verified via a follow-up GET)."""
    # First PUT: populate several optional fields.
    r1 = test_app.put(
        "/api/settings/profile",
        json={
            "full_name": "Alice",
            "email": "alice@example.com",
            "phone": "+1-800-555-0100",
            "location": "Paris",
            "linkedin_url": "https://linkedin.com/in/alice",
        },
    )
    assert r1.status_code == 200

    # Second PUT: change only the location.
    r2 = test_app.put(
        "/api/settings/profile",
        json={"location": "Berlin"},
    )
    assert r2.status_code == 200

    r3 = test_app.get("/api/settings/profile")
    assert r3.status_code == 200
    data = r3.json()

    # Changed field updated; all others preserved.
    assert data["location"] == "Berlin"
    assert data["full_name"] == "Alice"
    assert data["email"] == "alice@example.com"
    assert data["phone"] == "+1-800-555-0100"
    assert data["linkedin_url"] == "https://linkedin.com/in/alice"


def test_search_explicit_null_clears_nullable_field(test_app: TestClient) -> None:
    """Sending an explicit ``null`` (a *set* field) clears a nullable column,
    distinguishing exclude_unset semantics from a simple ``is not None`` guard."""
    # First PUT: set a nullable field to a concrete value.
    r1 = test_app.put(
        "/api/settings/search",
        json={"keywords": {"include": ["rust"]}, "max_job_age_days": 30},
    )
    assert r1.status_code == 200
    assert r1.json()["max_job_age_days"] == 30

    # Second PUT: explicitly null it out — must be cleared, not preserved.
    r2 = test_app.put(
        "/api/settings/search",
        json={"max_job_age_days": None},
    )
    assert r2.status_code == 200
    assert r2.json()["max_job_age_days"] is None

    # And keywords (omitted) stays intact.
    assert r2.json()["keywords"] == {"include": ["rust"]}
