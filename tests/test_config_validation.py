"""Tests for provider-aware runtime config validation.

These pin the rule that the app only requires the credentials the *chosen*
providers need — so a local OpenAI-compatible model needs no Google key.
"""
from __future__ import annotations

from backend.config import Settings


def _settings(monkeypatch, **env) -> Settings:
    # Start from a clean slate: clear anything the developer's real .env / shell
    # might leak in, then set only what the test wants.
    for name in (
        "GOOGLE_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
        "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "EMBEDDING_PROVIDER", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY",
        "BROWSER_LLM_PROVIDER", "BROWSER_LLM_BASE_URL", "BROWSER_LLM_API_KEY",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_local_openai_model_needs_no_google_key(monkeypatch):
    """The headline case: a local model via base_url validates with zero cloud keys."""
    s = _settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_BASE_URL="http://192.168.1.50:8080/v1",
        BROWSER_LLM_PROVIDER="openai",
        BROWSER_LLM_BASE_URL="http://192.168.1.50:8080/v1",
        # embeddings still need a capable backend; point at gemini w/ a key
        EMBEDDING_PROVIDER="gemini",
        GOOGLE_API_KEY="g",
    )
    assert s.validate_runtime_config() == []


def test_gemini_provider_requires_google_key(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="gemini")  # no GOOGLE_API_KEY
    problems = s.validate_runtime_config()
    assert any("LLM_PROVIDER=gemini requires GOOGLE_API_KEY" in p for p in problems)


def test_openai_provider_without_base_url_or_key_fails(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="openai", EMBEDDING_PROVIDER="openai",
                  EMBEDDING_BASE_URL="http://x/v1", BROWSER_LLM_PROVIDER="openai",
                  BROWSER_LLM_BASE_URL="http://x/v1")
    problems = s.validate_runtime_config()
    assert any("LLM_PROVIDER=openai requires" in p for p in problems)


def test_anthropic_provider_requires_key(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="anthropic",
                  EMBEDDING_PROVIDER="gemini", GOOGLE_API_KEY="g",
                  BROWSER_LLM_PROVIDER="gemini")
    problems = s.validate_runtime_config()
    assert any("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY" in p for p in problems)


def test_unknown_provider_flagged(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="bogus", GOOGLE_API_KEY="g")
    problems = s.validate_runtime_config()
    assert any("must be one of gemini|openai|anthropic" in p for p in problems)


def test_all_gemini_with_key_is_valid(monkeypatch):
    s = _settings(monkeypatch, GOOGLE_API_KEY="g")  # all providers default to gemini
    assert s.validate_runtime_config() == []


def test_hosted_openai_key_satisfies_all_three(monkeypatch):
    s = _settings(
        monkeypatch,
        LLM_PROVIDER="openai", EMBEDDING_PROVIDER="openai", BROWSER_LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
    )
    assert s.validate_runtime_config() == []
