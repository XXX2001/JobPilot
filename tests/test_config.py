"""Characterization tests for ``backend.config.Settings`` env-var mapping.

These tests pin the EXACT environment-variable names each setting reads from.
They were written before migrating away from the deprecated
``Field(..., env=...)`` form so they characterize current behavior first, then
prove the migration kept every env var name identical.
"""

from __future__ import annotations

import re
from pathlib import Path


def _fresh_settings(monkeypatch, **env):
    """Build a Settings instance from explicit env vars only (no .env file)."""
    # Required (no-default) credentials must always be present.
    monkeypatch.setenv("GOOGLE_API_KEY", "google-secret")
    monkeypatch.setenv("ADZUNA_APP_ID", "adzuna-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "adzuna-secret")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from backend.config import Settings

    # _env_file=None => ignore the developer's real .env so the test is
    # hermetic and only sees the vars we set above.
    return Settings(_env_file=None)


def test_required_credentials_read_from_their_env_names(monkeypatch):
    settings = _fresh_settings(monkeypatch)
    assert settings.GOOGLE_API_KEY.get_secret_value() == "google-secret"
    assert settings.ADZUNA_APP_ID == "adzuna-id"
    assert settings.ADZUNA_APP_KEY.get_secret_value() == "adzuna-secret"


def test_optional_secrets_read_from_their_env_names(monkeypatch):
    settings = _fresh_settings(
        monkeypatch,
        SERPAPI_KEY="serp",
        CREDENTIAL_KEY="cred",
        GMAIL_CLIENT_SECRET="gmail-secret",
    )
    assert settings.SERPAPI_KEY.get_secret_value() == "serp"
    assert settings.CREDENTIAL_KEY.get_secret_value() == "cred"
    assert settings.GMAIL_CLIENT_SECRET.get_secret_value() == "gmail-secret"


def test_app_settings_use_uppercase_env_names(monkeypatch):
    """Lowercase attributes must read from their UPPERCASE env-var names.

    This is the "non-default env name" case: the Python attribute is
    ``jobpilot_host`` but the env var is ``JOBPILOT_HOST`` — exactly what the
    deprecated ``env="JOBPILOT_HOST"`` used to spell out explicitly.
    """
    settings = _fresh_settings(
        monkeypatch,
        JOBPILOT_HOST="0.0.0.0",
        JOBPILOT_PORT="9999",
        JOBPILOT_LOG_LEVEL="debug",
        JOBPILOT_SCRAPER_HEADLESS="false",
        JOBPILOT_DATA_DIR="/tmp/jobpilot-data",
        JOBPILOT_ALLOWED_ORIGINS="http://example.com",
    )
    assert settings.jobpilot_host == "0.0.0.0"
    assert settings.jobpilot_port == 9999
    assert settings.jobpilot_log_level == "debug"
    assert settings.jobpilot_scraper_headless is False
    assert settings.jobpilot_data_dir == "/tmp/jobpilot-data"
    assert settings.jobpilot_allowed_origins == "http://example.com"


def test_model_and_feature_flags_read_from_their_env_names(monkeypatch):
    settings = _fresh_settings(
        monkeypatch,
        GOOGLE_MODEL="gemini-x",
        GOOGLE_MODEL_FALLBACKS="a,b",
        SCRAPLING_ENABLED="false",
        APPLY_TIER1_ENABLED="false",
    )
    assert settings.GOOGLE_MODEL == "gemini-x"
    assert settings.GOOGLE_MODEL_FALLBACKS == "a,b"
    assert settings.SCRAPLING_ENABLED is False
    assert settings.APPLY_TIER1_ENABLED is False


def test_gmail_settings_read_from_their_env_names(monkeypatch):
    settings = _fresh_settings(
        monkeypatch,
        GMAIL_CLIENT_ID="client-123",
        GMAIL_REDIRECT_URI="http://localhost:1234/cb",
        GMAIL_BACKFILL_DAYS="7",
        GMAIL_POLL_INTERVAL_MINUTES="15",
    )
    assert settings.GMAIL_CLIENT_ID == "client-123"
    assert settings.GMAIL_REDIRECT_URI == "http://localhost:1234/cb"
    assert settings.GMAIL_BACKFILL_DAYS == 7
    assert settings.GMAIL_POLL_INTERVAL_MINUTES == 15


def test_defaults_are_preserved_when_env_unset(monkeypatch):
    """Unset optional vars must fall back to the documented defaults."""
    for name in (
        "JOBPILOT_HOST",
        "JOBPILOT_PORT",
        "JOBPILOT_LOG_LEVEL",
        "JOBPILOT_SCRAPER_HEADLESS",
        "JOBPILOT_DATA_DIR",
        "JOBPILOT_ALLOWED_ORIGINS",
        "GOOGLE_MODEL",
        "GOOGLE_MODEL_FALLBACKS",
        "SCRAPLING_ENABLED",
        "APPLY_TIER1_ENABLED",
        "GMAIL_CLIENT_ID",
        "GMAIL_REDIRECT_URI",
        "GMAIL_BACKFILL_DAYS",
        "GMAIL_POLL_INTERVAL_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = _fresh_settings(monkeypatch)
    assert settings.jobpilot_host == "127.0.0.1"
    assert settings.jobpilot_port == 8000
    assert settings.jobpilot_log_level == "info"
    assert settings.jobpilot_scraper_headless is True
    assert settings.jobpilot_data_dir == "./data"
    assert settings.GOOGLE_MODEL == "gemini-3-flash-preview"
    assert settings.GOOGLE_MODEL_FALLBACKS == ""
    assert settings.SCRAPLING_ENABLED is True
    assert settings.APPLY_TIER1_ENABLED is True
    assert settings.GMAIL_CLIENT_ID == ""
    assert settings.GMAIL_BACKFILL_DAYS == 30
    assert settings.GMAIL_POLL_INTERVAL_MINUTES == 5


def test_no_deprecated_field_env_kwarg_remains():
    """The deprecated ``Field(..., env=...)`` form must be fully removed."""
    source = Path(__file__).resolve().parents[1] / "backend" / "config.py"
    text = source.read_text(encoding="utf-8")
    assert not re.search(r"Field\([^)]*\benv\s*=", text), (
        "backend/config.py still uses the deprecated Field(env=...) form"
    )
