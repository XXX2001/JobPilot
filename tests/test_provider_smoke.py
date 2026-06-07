def test_openai_and_anthropic_importable():
    import openai  # noqa: F401
    import anthropic  # noqa: F401


def test_exception_handlers_use_aliases():
    # main.py imports GeminiJSONError/GeminiRateLimitError which now alias neutral types
    from backend.llm.base import LLMJSONError, LLMRateLimitError
    from backend.llm.gemini_client import GeminiJSONError, GeminiRateLimitError
    assert GeminiJSONError is LLMJSONError
    assert GeminiRateLimitError is LLMRateLimitError
