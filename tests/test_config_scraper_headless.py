"""Tests for the JOBPILOT_SCRAPER_HEADLESS config flag."""


def _make_settings(monkeypatch, **extra_env):
    """Return a fresh Settings instance with required fields set via env vars."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    from backend.config import Settings
    return Settings()


def test_default_is_headless(monkeypatch):
    """jobpilot_scraper_headless defaults to True when env var is not set."""
    settings = _make_settings(monkeypatch)
    assert settings.jobpilot_scraper_headless is True


def test_env_can_disable_headless(monkeypatch):
    """Setting JOBPILOT_SCRAPER_HEADLESS=false makes jobpilot_scraper_headless False."""
    settings = _make_settings(monkeypatch, JOBPILOT_SCRAPER_HEADLESS="false")
    assert settings.jobpilot_scraper_headless is False
