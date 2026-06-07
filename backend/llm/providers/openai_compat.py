from __future__ import annotations

import logging
from typing import Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import (
    LLMCallFailed, LLMJSONError, LLMRateLimitError, parse_json_response,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}


def _resolve_key() -> str:
    return (
        settings.LLM_API_KEY.get_secret_value()
        or settings.OPENAI_API_KEY.get_secret_value()
    )


class OpenAICompatClient:
    """Generation adapter for any OpenAI-compatible endpoint (OpenAI, DeepSeek, local)."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None) -> None:
        self._model = model or settings.LLM_MODEL or _DEFAULT_MODEL
        self._client = AsyncOpenAI(
            api_key=api_key or _resolve_key() or "not-needed",
            base_url=base_url or settings.LLM_BASE_URL or None,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )

    async def generate_text(
        self, prompt: str, *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if response_mime_type == "application/json":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
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
            try:
                text2 = await self.generate_text(prompt)
                return schema.model_validate(parse_json_response(text2))
            except Exception as retry:  # noqa: BLE001
                raise LLMJSONError(
                    f"Invalid JSON from LLM (after retry): {retry}\nRaw: {text[:200]}"
                ) from first


class OpenAICompatEmbeddingClient:
    """Embedding adapter for OpenAI-compatible endpoints (OpenAI, local Ollama, etc.)."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None) -> None:
        self._model = model or settings.EMBEDDING_MODEL or _DEFAULT_EMBED_MODEL
        self._client = AsyncOpenAI(
            api_key=(api_key or settings.EMBEDDING_API_KEY.get_secret_value()
                     or settings.OPENAI_API_KEY.get_secret_value() or "not-needed"),
            base_url=base_url or settings.EMBEDDING_BASE_URL or None,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        self._dimension = _EMBED_DIMS.get(self._model, 1536)

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.embeddings.create(model=self._model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                raise LLMRateLimitError(msg) from e
            raise LLMCallFailed(msg) from e
