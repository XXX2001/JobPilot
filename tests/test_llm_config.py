from backend.config import settings


def test_provider_defaults_are_auto_and_openai():
    assert settings.LLM_PROVIDER == "auto"
    assert settings.BROWSER_LLM_PROVIDER == "openai"


def test_provider_optional_fields_default_empty():
    assert settings.LLM_BASE_URL == ""
    assert settings.LLM_MODEL == ""
    assert settings.OPENAI_API_KEY.get_secret_value() == ""
    assert settings.ANTHROPIC_API_KEY.get_secret_value() == ""
