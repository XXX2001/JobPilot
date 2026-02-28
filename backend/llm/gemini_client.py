from __future__ import annotations
import asyncio
import json
import logging
import time
from collections import deque
from typing import TypeVar, Type

from google import genai
from pydantic import BaseModel

from backend.config import settings

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
        self._model_name = "gemini-2.0-flash"
        self._call_times: deque[float] = deque(maxlen=self.RPM_LIMIT)
        self._lock = asyncio.Lock()

    async def _wait_for_rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if len(self._call_times) == self.RPM_LIMIT:
                oldest = self._call_times[0]
                window = 60.0 - (now - oldest)
                if window > 0:
                    logger.info("Rate limit: sleeping %.1fs", window)
                    await asyncio.sleep(window)
            self._call_times.append(time.monotonic())

    async def generate_text(self, prompt: str) -> str:
        await self._wait_for_rate_limit()
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
                if "429" in str(e) and attempt < 2:
                    await asyncio.sleep(2**attempt * 5)
                    continue
                raise GeminiRateLimitError(str(e)) from e
        raise GeminiRateLimitError("Exhausted retries")

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
