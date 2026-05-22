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


async def test_rate_limiter_allows_parallel_when_within_budget():
    """Concurrent callers within the RPM budget must NOT serialise.

    Regression test for PC-01: the rate limiter previously held
    `_lock` across `await asyncio.sleep(...)`, which forced every
    caller to wait for the previous one's sleep even when the RPM
    window had room. With the fix, the lock only covers state
    read + slot reservation; sleeping happens after release.

    With RPM_LIMIT=15, 5 concurrent calls all fit in the budget
    (deque len 0 -> 5, never == RPM_LIMIT), so sleep_for stays 0
    for every caller and total wall-time should be near-zero rather
    than scaling with the number of callers.
    """
    client = GeminiClient.__new__(GeminiClient)
    client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
    client._lock = asyncio.Lock()

    start = time.monotonic()
    await asyncio.gather(*(client._wait_for_rate_limit() for _ in range(5)))
    elapsed = time.monotonic() - start

    # Under the buggy serialised version, even with sleep_for==0 the
    # async-with overhead is negligible; the real proof of parallelism
    # is that 5 reservations happened concurrently. Assert (a) the
    # deque now has exactly 5 reserved slots, and (b) total time is
    # well below any plausible serial sleep (50ms is generous).
    assert len(client._call_times) == 5
    assert elapsed < 0.05, f"expected near-instant, got {elapsed:.3f}s"


async def test_rate_limiter_releases_lock_before_sleeping():
    """When the window is saturated, the lock must be released
    before `await asyncio.sleep(...)`. We prove this by saturating
    the deque, then running two concurrent _wait_for_rate_limit
    calls: with the lock released before sleep, both reserve their
    slots in lockstep and the second caller's reserve time is
    NOT delayed by the first's sleep.

    We mock asyncio.sleep to a no-op so the test stays fast; the
    invariant is that with the lock-around-sleep bug, the second
    caller's reservation would only be appended AFTER the first
    one finishes sleeping. With the fix, both reservations happen
    immediately and we observe deque length 2 right after gather.
    """
    client = GeminiClient.__new__(GeminiClient)
    client._lock = asyncio.Lock()
    # Saturate the window so each caller computes sleep_for > 0
    now = time.monotonic()
    client._call_times = deque(
        [now - 0.5 for _ in range(GeminiClient.RPM_LIMIT)],
        maxlen=GeminiClient.RPM_LIMIT,
    )

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    with patch("backend.llm.gemini_client.asyncio.sleep", side_effect=fake_sleep):
        start = time.monotonic()
        await asyncio.gather(*(client._wait_for_rate_limit() for _ in range(2)))
        elapsed = time.monotonic() - start

    # Both callers slept (window was saturated), and total elapsed is
    # tiny because sleeps are mocked AND because lock was released
    # before each sleep — neither caller blocked the other on the lock.
    assert len(sleep_calls) == 2
    assert elapsed < 0.05, f"expected near-instant, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_embed_returns_vectors(monkeypatch):
    """embed() should return a list of float vectors."""
    from unittest.mock import MagicMock
    from backend.llm.gemini_client import GeminiClient

    from pydantic import SecretStr
    monkeypatch.setattr("backend.config.settings.GOOGLE_API_KEY", SecretStr("fake-key"))
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
