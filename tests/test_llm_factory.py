import pytest
from backend.llm import factory
from backend.llm.providers.openai_compat import (
    OpenAICompatClient, OpenAICompatEmbeddingClient,
)
from backend.llm.providers.anthropic_client import AnthropicClient

def test_make_llm_client_openai(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "openai")
    assert isinstance(factory.make_llm_client(), OpenAICompatClient)

def test_make_llm_client_anthropic(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "anthropic")
    assert isinstance(factory.make_llm_client(), AnthropicClient)

def test_make_llm_client_unknown_raises(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        factory.make_llm_client()

def test_make_embedding_anthropic_unsupported(monkeypatch):
    monkeypatch.setattr(factory.settings, "EMBEDDING_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="no embeddings"):
        factory.make_embedding_client()

def test_make_embedding_openai(monkeypatch):
    monkeypatch.setattr(factory.settings, "EMBEDDING_PROVIDER", "openai")
    assert isinstance(factory.make_embedding_client(), OpenAICompatEmbeddingClient)

def test_make_browser_llm_anthropic_requires_base_url(monkeypatch):
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="base_url"):
        factory.make_browser_llm()
