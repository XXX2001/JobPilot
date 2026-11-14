from __future__ import annotations

import logging
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)


def resolve_llm_provider() -> str:
    return settings.resolve_llm_provider()


def make_llm_client():
    """Return an LLMClient for the configured LLM_PROVIDER."""
    provider = resolve_llm_provider()
    if provider == "claude":
        from backend.llm.providers.cli_agents import ClaudeCLIClient
        return ClaudeCLIClient(model=settings.LLM_CLI_MODEL or "sonnet", timeout=settings.LLM_TIMEOUT_SECONDS)
    if provider == "codex":
        from backend.llm.providers.cli_agents import CodexCLIClient
        return CodexCLIClient(model=settings.LLM_CLI_MODEL, timeout=settings.LLM_TIMEOUT_SECONDS)
    if provider == "none":
        raise ValueError("no writing model")
    if provider == "openai":
        from backend.llm.providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient()
    if provider == "anthropic":
        from backend.llm.providers.anthropic_client import AnthropicClient
        return AnthropicClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (auto|claude|codex|openai|anthropic)")


def make_browser_llm():
    """Return a browser-use Chat* instance for the configured BROWSER_LLM_PROVIDER."""
    problems = settings.validate_browser_llm_config()
    if problems:
        raise ValueError("; ".join(problems))
    provider = (settings.BROWSER_LLM_PROVIDER or "openai").lower()
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
        kwargs: dict[str, Any] = {"model": settings.BROWSER_LLM_MODEL or "gpt-4o", "api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    raise ValueError(f"Unknown BROWSER_LLM_PROVIDER: {provider!r} (openai)")
