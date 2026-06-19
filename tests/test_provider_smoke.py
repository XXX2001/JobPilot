def test_openai_and_anthropic_importable():
    import openai  # noqa: F401
    import anthropic  # noqa: F401


def test_provider_neutral_exceptions_importable():
    # main.py and the provider adapters share these provider-neutral exceptions.
    from backend.llm.base import LLMCallFailed, LLMJSONError, LLMRateLimitError
    assert issubclass(LLMJSONError, Exception)
    assert issubclass(LLMRateLimitError, Exception)
    assert issubclass(LLMCallFailed, Exception)
