from __future__ import annotations

import logging
from typing import Type, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import (
    LLMCallFailed, LLMJSONError, LLMRateLimitError, parse_json_response,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 4096


class AnthropicClient:
    """Generation adapter for the Anthropic Messages API."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._model = model or settings.LLM_MODEL or _DEFAULT_MODEL
        self._client = AsyncAnthropic(
            api_key=(api_key or settings.LLM_API_KEY.get_secret_value()
                     or settings.ANTHROPIC_API_KEY.get_secret_value()),
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )

    async def generate_text(
        self, prompt: str, *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        # Anthropic has no JSON-mode flag; when JSON is requested we steer via
        # a system instruction and parse downstream in generate_json.
        system = (
            "Respond with only a single valid JSON object, no prose, no code fences."
            if response_mime_type == "application/json" else None
        )
        try:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": _MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = await self._client.messages.create(**kwargs)
            return "".join(getattr(b, "text", "") for b in resp.content)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                raise LLMRateLimitError(msg) from e
            raise LLMCallFailed(msg) from e

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        text = await self.generate_text(prompt, response_mime_type="application/json")
        try:
            return schema.model_validate(parse_json_response(text))
        except Exception as first:  # noqa: BLE001
            raise LLMJSONError(
                f"Invalid JSON from Anthropic: {first}\nRaw: {text[:200]}"
            ) from first
