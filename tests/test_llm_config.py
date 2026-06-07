from backend.config import settings


def test_provider_defaults_are_gemini():
    assert settings.LLM_PROVIDER == "gemini"
    assert settings.EMBEDDING_PROVIDER == "gemini"
    assert settings.BROWSER_LLM_PROVIDER == "gemini"
    assert settings.EMBEDDING_MODEL == "text-embedding-004"


def test_provider_optional_fields_default_empty():
    assert settings.LLM_BASE_URL == ""
    assert settings.LLM_MODEL == ""
    assert settings.OPENAI_API_KEY.get_secret_value() == ""
    assert settings.ANTHROPIC_API_KEY.get_secret_value() == ""
