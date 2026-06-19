"""Tests for provider-aware runtime config validation.

These pin the rule that the app only requires the credentials the *chosen*
providers need — so a local OpenAI-compatible model needs no cloud key.
"""
from __future__ import annotations

from backend.config import Settings


def _settings(monkeypatch, **env) -> Settings:
    # Start from a clean slate: clear anything the developer's real .env / shell
    # might leak in, then set only what the test wants.
    for name in (
        "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
        "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "EMBEDDING_PROVIDER", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY",
        "BROWSER_LLM_PROVIDER", "BROWSER_LLM_BASE_URL", "BROWSER_LLM_API_KEY",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_local_openai_model_needs_no_cloud_key(monkeypatch):
    """The headline case: a local model via base_url validates with zero cloud keys."""
    s = _settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_BASE_URL="http://192.168.1.50:8080/v1",
        BROWSER_LLM_PROVIDER="openai",
        BROWSER_LLM_BASE_URL="http://192.168.1.50:8080/v1",
        # embeddings still need a capable backend; point at the same local server
        EMBEDDING_PROVIDER="openai",
        EMBEDDING_BASE_URL="http://192.168.1.50:8080/v1",
    )
    assert s.validate_runtime_config() == []


def test_openai_provider_without_base_url_or_key_fails(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="openai", EMBEDDING_PROVIDER="openai",
                  EMBEDDING_BASE_URL="http://x/v1", BROWSER_LLM_PROVIDER="openai",
                  BROWSER_LLM_BASE_URL="http://x/v1")
    problems = s.validate_runtime_config()
    assert any("LLM_PROVIDER=openai requires" in p for p in problems)


def test_anthropic_provider_requires_key(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="anthropic",
                  EMBEDDING_PROVIDER="openai", EMBEDDING_BASE_URL="http://x/v1",
                  BROWSER_LLM_PROVIDER="openai", BROWSER_LLM_BASE_URL="http://x/v1")
    problems = s.validate_runtime_config()
    assert any("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY" in p for p in problems)


def test_embedding_provider_anthropic_rejected(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test",
                  EMBEDDING_PROVIDER="anthropic")
    problems = s.validate_runtime_config()
    assert any("EMBEDDING_PROVIDER='anthropic' must be openai" in p for p in problems)


def test_unknown_provider_flagged(monkeypatch):
    s = _settings(monkeypatch, LLM_PROVIDER="bogus", OPENAI_API_KEY="sk-test")
    problems = s.validate_runtime_config()
    assert any("must be one of openai|anthropic" in p for p in problems)


def test_hosted_openai_key_satisfies_all_three(monkeypatch):
    s = _settings(
        monkeypatch,
        LLM_PROVIDER="openai", EMBEDDING_PROVIDER="openai", BROWSER_LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
    )
    assert s.validate_runtime_config() == []
