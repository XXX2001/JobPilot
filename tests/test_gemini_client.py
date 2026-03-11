from __future__ import annotations
import asyncio
import time
from collections import deque
from unittest.mock import patch, AsyncMock
import pytest

from backend.llm.gemini_client import GeminiClient, GeminiJSONError
from backend.llm.validators import LetterEdit


async def test_generate_json_valid():
    """GeminiClient.generate_json parses valid JSON into the schema."""
    client = GeminiClient.__new__(GeminiClient)
    client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
    client._lock = asyncio.Lock()

    valid_json = '{"edited_paragraph": "new text", "company_name": "ACME"}'

    with patch.object(client, "generate_text", new=AsyncMock(return_value=valid_json)):
        result = await client.generate_json("some prompt", LetterEdit)

    assert isinstance(result, LetterEdit)
    assert result.edited_paragraph == "new text"
    assert result.company_name == "ACME"


async def test_invalid_json_raises_gemini_error():
    """GeminiClient.generate_json raises GeminiJSONError on non-JSON response."""
    client = GeminiClient.__new__(GeminiClient)
    client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
    client._lock = asyncio.Lock()

    with patch.object(client, "generate_text", new=AsyncMock(return_value="this is not json")):
        with pytest.raises(GeminiJSONError):
            await client.generate_json("some prompt", LetterEdit)


async def test_rate_limiter_tracks_calls():
    """Rate limiter sleeps when the 15-call window is saturated."""
    client = GeminiClient.__new__(GeminiClient)
    client._lock = asyncio.Lock()

    # Fill the deque with 15 calls all within the last 5 seconds
    now = time.monotonic()
    client._call_times = deque(
        [now - i * 0.1 for i in range(GeminiClient.RPM_LIMIT)],
        maxlen=GeminiClient.RPM_LIMIT,
    )
    # Reverse so oldest is first
    client._call_times = deque(
        sorted(client._call_times),
        maxlen=GeminiClient.RPM_LIMIT,
    )

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with patch("backend.llm.gemini_client.asyncio.sleep", side_effect=fake_sleep):
        await client._wait_for_rate_limit()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


@pytest.mark.asyncio
async def test_embed_returns_vectors(monkeypatch):
    """embed() should return a list of float vectors."""
    from unittest.mock import MagicMock
    from backend.llm.gemini_client import GeminiClient

    monkeypatch.setattr("backend.config.settings.GOOGLE_API_KEY", "fake-key")
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL", "gemini-3.0-flash")
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL_FALLBACKS", "")

    client = GeminiClient()

    # Mock the underlying genai client
    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2, 0.3]
    mock_result = MagicMock()
    mock_result.embeddings = [mock_embedding, mock_embedding]
    client._client.models.embed_content = MagicMock(return_value=mock_result)

    result = await client.embed(["hello", "world"])
    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
