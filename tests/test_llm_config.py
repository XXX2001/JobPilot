from backend.config import settings

def test_provider_defaults_are_openai():
    assert settings.LLM_PROVIDER == "openai"
    assert settings.EMBEDDING_PROVIDER == "openai"
    assert settings.BROWSER_LLM_PROVIDER == "openai"
    assert settings.EMBEDDING_MODEL == "text-embedding-3-small"

def test_provider_optional_fields_default_empty():
    assert settings.LLM_BASE_URL == ""
    assert settings.LLM_MODEL == ""
    assert settings.OPENAI_API_KEY.get_secret_value() == ""
    assert settings.ANTHROPIC_API_KEY.get_secret_value() == ""
