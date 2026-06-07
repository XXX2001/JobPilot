from __future__ import annotations

import json
from typing import Protocol, Type, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMRateLimitError(Exception):
    """Provider returned a rate-limit (429) error."""


class LLMCallFailed(Exception):
    """A non-rate-limit provider failure (bad key, network, backend 5xx)."""


class LLMJSONError(Exception):
    """Provider returned text that could not be parsed as the expected JSON."""


def parse_json_response(raw: str) -> dict:
    """Parse model text into a dict, tolerating ```json fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


@runtime_checkable
class LLMClient(Protocol):
    """Text/JSON generation contract implemented by every provider adapter."""

    async def generate_text(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str: ...

    async def generate_json(self, prompt: str, schema: Type[T]) -> T: ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """Embedding contract implemented by embedding-capable adapters."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
