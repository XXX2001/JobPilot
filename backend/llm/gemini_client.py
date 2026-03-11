from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Type, TypeVar

from google import genai
from pydantic import BaseModel

from backend.config import settings
from backend.defaults import GEMINI_FALLBACK_MODEL

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class GeminiRateLimitError(Exception):
    pass


class GeminiJSONError(Exception):
    pass


class GeminiClient:
    """Async Gemini client with 15 RPM sliding-window rate limiter."""

    RPM_LIMIT = 15

    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        # Build ordered list of candidate models: primary + fallbacks
        primary = settings.GOOGLE_MODEL or GEMINI_FALLBACK_MODEL
        fallbacks = [m.strip() for m in (settings.GOOGLE_MODEL_FALLBACKS or "").split(",")]
        fallbacks = [m for m in fallbacks if m]
        self._candidates: list[str] = [primary] + fallbacks
        # current index into candidates
        self._candidate_idx = 0
        self._model_name = self._candidates[self._candidate_idx]
        self._call_times: deque[float] = deque(maxlen=self.RPM_LIMIT)
        self._lock = asyncio.Lock()
        self._embed_call_times: deque[float] = deque(maxlen=self.RPM_LIMIT)
        self._embed_lock = asyncio.Lock()

    async def _wait_for_rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if len(self._call_times) == self.RPM_LIMIT:
                oldest = self._call_times[0]
                window = 60.0 - (now - oldest)
                if window > 0:
                    logger.info("Rate limit: sleeping %.1fs", window)
                    await asyncio.sleep(min(window, 120.0))  # Never sleep more than 2 minutes
            self._call_times.append(time.monotonic())

    async def generate_text(self, prompt: str) -> str:
        await self._wait_for_rate_limit()
        max_attempts = len(self._candidates) if self._candidates else 1
        last_exc: Exception | None = None
        for model_try in range(max_attempts):
            self._model_name = self._candidates[model_try]
            for attempt in range(3):
                try:
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._client.models.generate_content(
                            model=self._model_name, contents=prompt
                        ),
                    )
                    return response.text
                except Exception as e:
                    last_exc = e
                    # Detect model-not-found / NOT_FOUND and break to try next candidate
                    msg = str(e)
                    if (
                        "404" in msg
                        or "NOT_FOUND" in msg
                        or "model" in msg
                        and "not found" in msg.lower()
                    ):
                        logger.warning(
                            "Model %s not found: %s — trying next candidate", self._model_name, e
                        )
                        break
                    if "429" in msg and attempt < 2:
                        await asyncio.sleep(2**attempt * 5)
                        continue
                    raise GeminiRateLimitError(str(e)) from e
        raise GeminiRateLimitError(f"All model candidates failed: {last_exc}")

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        text = await self.generate_text(prompt)

        def _parse(raw: str) -> T:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                raw = raw.rsplit("```", 1)[0]
            data = json.loads(raw.strip())
            return schema.model_validate(data)

        try:
            return _parse(text)
        except (json.JSONDecodeError, Exception) as first_exc:
            # One retry: ask Gemini to reformat the output as plain JSON
            retry_prompt = (
                f"The following text is not valid JSON. "
                f"Return ONLY the JSON object, no markdown fences, no prose:\n\n{text}"
            )
            try:
                text2 = await self.generate_text(retry_prompt)
                return _parse(text2)
            except (json.JSONDecodeError, Exception) as retry_exc:
                raise GeminiJSONError(
                    f"Invalid JSON from LLM (after retry): {retry_exc}\nRaw: {text[:200]}"
                ) from first_exc

    async def _wait_for_embed_rate_limit(self) -> None:
        async with self._embed_lock:
            now = time.monotonic()
            if len(self._embed_call_times) == self.RPM_LIMIT:
                oldest = self._embed_call_times[0]
                window = 60.0 - (now - oldest)
                if window > 0:
                    logger.info("Embed rate limit: sleeping %.1fs", window)
                    await asyncio.sleep(min(window, 120.0))
            self._embed_call_times.append(time.monotonic())

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embed texts via text-embedding-004. Returns list of 768-dim vectors."""
        from backend.defaults import EMBEDDING_MODEL

        await self._wait_for_embed_rate_limit()
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
            ),
        )
        return [e.values for e in result.embeddings]
