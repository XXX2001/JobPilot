from backend.llm import base
from backend.llm.gemini_client import (
    GeminiClient, GeminiRateLimitError, GeminiJSONError, GeminiCallFailed,
)


def test_gemini_exceptions_alias_neutral():
    assert GeminiRateLimitError is base.LLMRateLimitError
    assert GeminiJSONError is base.LLMJSONError
    assert GeminiCallFailed is base.LLMCallFailed


def test_gemini_client_exposes_model_id_and_dimension(monkeypatch):
    monkeypatch.setattr(
        "backend.llm.gemini_client.genai.Client", lambda **kw: object()
    )
    c = GeminiClient()
    assert c.model_id.startswith("gemini:")
    assert c.dimension == 768
