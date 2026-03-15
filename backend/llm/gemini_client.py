from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Type, TypeVar

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from backend.config import settings
from backend.defaults import GEMINI_FALLBACK_MODEL

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Regex to extract retry delay from Gemini error messages / details.
# Gemini free-tier 429 responses typically include:
#   "retry_delay { seconds: 30 }" or "retryDelay": "30s" or "Retry after 42 seconds"
_RETRY_SECONDS_RE = re.compile(
    r"""(?:retry.?delay\s*\{?\s*seconds:\s*(\d+))   # retry_delay { seconds: 30 }
       |(?:"retryDelay":\s*"(\d+)s")                # "retryDelay": "30s"
       |(?:retry\s+after\s+(\d+)\s*s)               # Retry after 30 seconds
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_retry_seconds(exc: Exception) -> float | None:
    """Parse a retry delay (in seconds) from a Gemini 429 error.

    Checks the stringified exception, and if the exception has a `response`
    attribute (httpx.Response), also checks the Retry-After header.
    """
    # Check Retry-After header on the raw response (if available)
    response = getattr(exc, "response", None)
    if response is not None:
        header = None
        if hasattr(response, "headers"):
            header = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass

    # Fall back to parsing the error message / details
    text = str(exc)
    # Also check the .details attribute (google.genai.errors.APIError stores response JSON there)
    details = getattr(exc, "details", None)
    if details:
        text += " " + str(details)

    m = _RETRY_SECONDS_RE.search(text)
    if m:
        for group in m.groups():
            if group:
                return float(group)
    return None


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

    def _is_model_not_found(self, msg: str) -> bool:
        return (
            "404" in msg
            or "NOT_FOUND" in msg
            or ("model" in msg and "not found" in msg.lower())
        )

    async def generate_text(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        """Generate text from the model.

        Args:
            prompt: The input prompt.
            response_mime_type: Optional MIME type (e.g. "application/json") to
                force structured output from the model.
            response_schema: Optional JSON schema dict for structured output.
        """
        await self._wait_for_rate_limit()

        # Build generation config if structured output is requested
        config = None
        if response_mime_type:
            config = genai_types.GenerateContentConfig(
                response_mime_type=response_mime_type,
                response_schema=response_schema,
            )

        max_attempts = len(self._candidates) if self._candidates else 1
        last_exc: Exception | None = None
        for model_try in range(max_attempts):
            self._model_name = self._candidates[model_try]
            for attempt in range(3):
                try:
                    _model = self._model_name
                    _config = config
                    response = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: self._client.models.generate_content(
                            model=_model,
                            contents=prompt,
                            config=_config,
                        ),
                    )
                    return response.text
                except Exception as e:
                    last_exc = e
                    msg = str(e)
                    # Detect model-not-found / NOT_FOUND and break to try next candidate
                    if self._is_model_not_found(msg):
                        logger.warning(
                            "Model %s not found: %s — trying next candidate", self._model_name, e
                        )
                        break
                    if "429" in msg and attempt < 2:
                        # Parse retry delay from the error; fall back to exponential backoff
                        delay = _extract_retry_seconds(e)
                        if delay is None:
                            delay = 2**attempt * 5
                        else:
                            # Add a small jitter to avoid thundering herd
                            delay = min(delay + 1.0, 300.0)
                        logger.info(
                            "Rate limited (429), waiting %.1fs before retry %d/3",
                            delay, attempt + 2,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise GeminiRateLimitError(str(e)) from e
        raise GeminiRateLimitError(f"All model candidates failed: {last_exc}")

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        """Generate structured JSON output from the model.

        Uses Gemini's native JSON mode (response_mime_type="application/json")
        to get valid JSON directly, avoiding an expensive retry call when the
        model returns markdown-fenced or invalid JSON.
        """
        # Build a JSON schema from the Pydantic model for structured output
        json_schema = schema.model_json_schema()

        text = await self.generate_text(
            prompt,
            response_mime_type="application/json",
            response_schema=json_schema,
        )

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
            # JSON mode should prevent this, but if it still fails, try one
            # more time without JSON mode (some schemas may not be supported)
            logger.warning(
                "JSON mode parse failed (%s), retrying without JSON mode",
                first_exc,
            )
            try:
                text2 = await self.generate_text(prompt)
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
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
            ),
        )
        return [e.values for e in result.embeddings]
