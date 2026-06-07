from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


def make_llm_client():
    """Return an LLMClient for the configured LLM_PROVIDER."""
    provider = (settings.LLM_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from backend.llm.gemini_client import GeminiClient
        return GeminiClient()
    if provider == "openai":
        from backend.llm.providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient()
    if provider == "anthropic":
        from backend.llm.providers.anthropic_client import AnthropicClient
        return AnthropicClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (gemini|openai|anthropic)")


def make_embedding_client():
    """Return an EmbeddingClient for the configured EMBEDDING_PROVIDER."""
    provider = (settings.EMBEDDING_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from backend.llm.gemini_client import GeminiClient
        return GeminiClient()
    if provider == "openai":
        from backend.llm.providers.openai_compat import OpenAICompatEmbeddingClient
        return OpenAICompatEmbeddingClient()
    if provider == "anthropic":
        raise ValueError("EMBEDDING_PROVIDER=anthropic invalid: Anthropic has no embeddings API")
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider!r} (gemini|openai)")


def make_browser_llm():
    """Return a browser-use Chat* instance for the configured BROWSER_LLM_PROVIDER."""
    provider = (settings.BROWSER_LLM_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from browser_use.llm.models import ChatGoogle
        return ChatGoogle(
            model=settings.BROWSER_LLM_MODEL or settings.GOOGLE_MODEL,
            api_key=settings.GOOGLE_API_KEY.get_secret_value(),
        )
    if provider in ("openai", "anthropic"):
        from browser_use.llm.models import ChatOpenAI
        base_url = settings.BROWSER_LLM_BASE_URL
        if provider == "anthropic" and not base_url:
            raise ValueError(
                "BROWSER_LLM_PROVIDER=anthropic needs a base_url (set BROWSER_LLM_BASE_URL); "
                "browser-use ships no ChatAnthropic; use an OpenAI-compatible endpoint"
            )
        key = (settings.BROWSER_LLM_API_KEY.get_secret_value()
               or settings.OPENAI_API_KEY.get_secret_value()
               or settings.ANTHROPIC_API_KEY.get_secret_value())
        kwargs = {"model": settings.BROWSER_LLM_MODEL or "gpt-4o", "api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    raise ValueError(f"Unknown BROWSER_LLM_PROVIDER: {provider!r} (gemini|openai)")
