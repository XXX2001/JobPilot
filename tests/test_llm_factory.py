import pytest
from pydantic import SecretStr
from backend.llm import factory
from backend.llm.providers.openai_compat import OpenAICompatClient
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

def test_make_browser_llm_anthropic_requires_base_url(monkeypatch):
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="base_url"):
        factory.make_browser_llm()


@pytest.mark.parametrize("provider,default_model", [("claude", "sonnet"), ("codex", "")])
def test_cli_factory_uses_cli_model_not_api_model(monkeypatch, provider, default_model):
    from backend.llm.providers.cli_agents import ClaudeCLIClient, CodexCLIClient

    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(factory.settings, "LLM_MODEL", "api-model")
    monkeypatch.setattr(factory.settings, "LLM_CLI_MODEL", "")
    client = factory.make_llm_client()
    assert isinstance(client, ClaudeCLIClient if provider == "claude" else CodexCLIClient)
    assert client._model == default_model
    monkeypatch.setattr(factory.settings, "LLM_CLI_MODEL", "cli-model")
    assert factory.make_llm_client()._model == "cli-model"


def test_make_browser_llm_without_configuration_raises(monkeypatch):
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "openai")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", "")
    for name in ("BROWSER_LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setattr(factory.settings, name, SecretStr(""))
    with pytest.raises(ValueError, match="BROWSER_LLM"):
        factory.make_browser_llm()
