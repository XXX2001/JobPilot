"""Integration smoke test for the multi-provider LLM abstraction against a real
OpenAI-compatible local model (llama.cpp / Ollama / LM Studio / vLLM, …).

Unlike the rest of the suite (which mocks the provider transport), these tests
make REAL network calls through ``OpenAICompatClient`` / the factory, proving
the abstraction works end-to-end with no Gemini and no cloud keys.

They **auto-skip** when the endpoint is unreachable, so the default suite stays
green offline and in CI. Point them at any OpenAI-compatible server via env:

    JOBPILOT_LOCAL_LLM_URL    (default http://192.168.1.50:8080/v1)
    JOBPILOT_LOCAL_LLM_MODEL  (default Qwen3.6-27B-MTP)

Run just these:  uv run pytest tests/test_local_provider_smoke.py -v
Skip them:       uv run pytest -m "not integration"
"""
from __future__ import annotations

import os
import urllib.request

import pytest
from pydantic import BaseModel

from backend.llm import base, factory
from backend.llm.providers.openai_compat import (
    OpenAICompatClient,
    OpenAICompatEmbeddingClient,
)

LOCAL_URL = os.environ.get("JOBPILOT_LOCAL_LLM_URL", "http://192.168.1.50:8080/v1")
LOCAL_MODEL = os.environ.get("JOBPILOT_LOCAL_LLM_MODEL", "Qwen3.6-27B-MTP")


def _endpoint_reachable(base_url: str, timeout: float = 3.0) -> bool:
    """True if the OpenAI-compatible ``/models`` listing answers in time."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _endpoint_reachable(LOCAL_URL),
        reason=f"local OpenAI-compatible LLM not reachable at {LOCAL_URL}",
    ),
]


class _Person(BaseModel):
    name: str
    age: int


def _gen_client() -> OpenAICompatClient:
    return OpenAICompatClient(api_key="not-needed", model=LOCAL_MODEL, base_url=LOCAL_URL)


async def test_generate_text_roundtrip():
    """A real generation call returns non-empty text (reasoning models emit
    `reasoning_content` separately — the adapter must return the final answer)."""
    out = await _gen_client().generate_text("Reply with exactly one word: PONG")
    assert isinstance(out, str)
    assert out.strip() != ""


async def test_generate_json_roundtrip():
    """generate_json drives the model in JSON mode and validates into a schema."""
    out = await _gen_client().generate_json(
        'Return only this JSON object and nothing else: {"name": "Alice", "age": 30}',
        _Person,
    )
    assert isinstance(out, _Person)
    assert out.name.strip() != ""
    assert isinstance(out.age, int)


async def test_factory_routes_generation_to_local(monkeypatch):
    """make_llm_client() honours LLM_PROVIDER=openai + LLM_BASE_URL and the
    returned client really talks to the local model."""
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(factory.settings, "LLM_BASE_URL", LOCAL_URL)
    monkeypatch.setattr(factory.settings, "LLM_MODEL", LOCAL_MODEL)

    client = factory.make_llm_client()
    assert isinstance(client, OpenAICompatClient)

    out = await client.generate_text("Reply with exactly one word: OK")
    assert out.strip() != ""


async def test_embeddings_surface_cleanly():
    """Embeddings may or may not be served by a given build. Either way the
    adapter must behave: return well-formed vectors, or translate the provider
    error into the neutral LLMCallFailed (never leak a raw SDK exception).

    The default target (llama.cpp Qwen build) answers 501 here, so this asserts
    the clean-error path and skips; a build that supports embeddings asserts the
    vector shape instead."""
    emb = OpenAICompatEmbeddingClient(
        api_key="not-needed", model=LOCAL_MODEL, base_url=LOCAL_URL
    )
    try:
        vectors = await emb.embed(["hello world"])
    except base.LLMCallFailed:
        pytest.skip("endpoint does not serve embeddings — error correctly translated to LLMCallFailed")
    else:
        assert vectors and isinstance(vectors[0], list)
        assert len(vectors[0]) > 0


def test_factory_browser_llm_points_at_local(monkeypatch):
    """make_browser_llm() with BROWSER_LLM_PROVIDER=openai + base_url builds a
    browser-use ChatOpenAI bound to the local endpoint (constructed, not run)."""
    from browser_use.llm.models import ChatOpenAI

    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "openai")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", LOCAL_URL)
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_MODEL", LOCAL_MODEL)

    llm = factory.make_browser_llm()
    assert isinstance(llm, ChatOpenAI)
