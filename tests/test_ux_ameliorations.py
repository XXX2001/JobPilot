"""Regression tests for the ease-of-use ameliorations.

Covers: CV-edit whitespace-tolerant matching (#6), disable-thinking passthrough
(#3a), actionable timeout wrapping (#5), and the test-connection (#2) and
model-detect (#4) endpoints.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


# ── #6: whitespace-tolerant CV-edit matching ────────────────────────────────
def test_applicator_whitespace_tolerant_match():
    from backend.latex.applicator import CVApplicator
    from backend.llm.validators import CVReplacement

    # Source has irregular spacing + a line wrap; the LLM returns single-spaced
    # original_text. The old exact-match code skipped this; now it should land.
    cv = "\\begin{rSection}{Profile}\nSoftware  engineer   with\n  experience.\n\\end{rSection}"
    repl = CVReplacement(
        section="Profile",
        original_text="Software engineer with experience.",
        replacement_text="Senior software engineer with experience.",
        reason="emphasise seniority",
        job_requirement_matched="seniority",
        confidence=0.9,
    )
    out, applied = CVApplicator().apply(cv, [repl])
    assert len(applied) == 1
    assert "Senior software engineer" in out


def test_applicator_still_skips_genuinely_absent_text():
    from backend.latex.applicator import CVApplicator
    from backend.llm.validators import CVReplacement

    cv = "\\begin{rSection}{Profile}\nBackend developer.\n\\end{rSection}"
    repl = CVReplacement(
        section="Profile", original_text="Frontend wizard with Vue.",
        replacement_text="Frontend wizard with React.", reason="x",
        job_requirement_matched="React", confidence=0.9,
    )
    out, applied = CVApplicator().apply(cv, [repl])
    assert applied == []
    assert out == cv


# ── #3a: disable-thinking passthrough ────────────────────────────────────────
@pytest.mark.asyncio
async def test_disable_thinking_sets_extra_body(monkeypatch):
    import backend.config as cfg
    from backend.llm.providers.openai_compat import OpenAICompatClient

    monkeypatch.setattr(cfg.settings, "LLM_DISABLE_THINKING", True)

    captured: dict = {}

    async def fake_create(**kw):
        captured.update(kw)
        msg = MagicMock()
        msg.content = "ok"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client._model = "m"
    client._base_url = None
    client._client = MagicMock()
    client._client.chat.completions.create = fake_create

    await client.generate_text("hi")
    assert captured.get("extra_body") == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.asyncio
async def test_thinking_enabled_by_default_no_extra_body(monkeypatch):
    import backend.config as cfg
    from backend.llm.providers.openai_compat import OpenAICompatClient

    monkeypatch.setattr(cfg.settings, "LLM_DISABLE_THINKING", False)
    captured: dict = {}

    async def fake_create(**kw):
        captured.update(kw)
        msg = MagicMock()
        msg.content = "ok"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client._model = "m"
    client._base_url = None
    client._client = MagicMock()
    client._client.chat.completions.create = fake_create
    await client.generate_text("hi")
    assert "extra_body" not in captured


# ── #5: actionable timeout/connection wrapping ───────────────────────────────
@pytest.mark.asyncio
async def test_timeout_error_is_actionable():
    from openai import APITimeoutError

    from backend.llm.base import LLMCallFailed
    from backend.llm.providers.openai_compat import OpenAICompatClient

    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client._model = "m"
    client._base_url = "http://localhost:8080/v1"
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(
        side_effect=APITimeoutError(request=httpx.Request("POST", "http://localhost:8080/v1"))
    )
    with pytest.raises(LLMCallFailed) as ei:
        await client.generate_text("hi")
    assert "LLM_TIMEOUT_SECONDS" in str(ei.value) or "LLM_DISABLE_THINKING" in str(ei.value)


@pytest.mark.asyncio
async def test_connection_error_names_endpoint():
    from openai import APIConnectionError

    from backend.llm.base import LLMCallFailed
    from backend.llm.providers.openai_compat import OpenAICompatClient

    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client._model = "m"
    client._base_url = "http://localhost:9999/v1"
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(
        side_effect=APIConnectionError(request=httpx.Request("POST", "http://localhost:9999/v1"))
    )
    with pytest.raises(LLMCallFailed) as ei:
        await client.generate_text("hi")
    assert "9999" in str(ei.value)


# ── #4: model-detect parsing + endpoint ──────────────────────────────────────
def test_parse_model_ids_handles_both_shapes():
    from backend.api.settings import _parse_model_ids

    assert _parse_model_ids({"data": [{"id": "gpt-4o"}, {"id": "emb"}]}) == ["gpt-4o", "emb"]
    assert _parse_model_ids({"models": [{"model": "qwen"}, {"name": "q2"}]}) == ["qwen", "q2"]
    assert _parse_model_ids({"nope": 1}) == []


def test_models_endpoint_no_base_url(test_app):
    # Hosted-OpenAI default has no LLM_BASE_URL in the test env.
    resp = test_app.get("/api/settings/models?role=llm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == []
    assert body["error"]


# ── #2: test-connection endpoint ─────────────────────────────────────────────
def test_test_connection_endpoint(test_app, monkeypatch):
    import backend.llm.factory as factory

    class FakeLLM:
        async def generate_text(self, *a, **k):
            return "OK"

    class FakeEmb:
        async def embed(self, texts):
            return [[0.1, 0.2, 0.3, 0.4]]

    monkeypatch.setattr(factory, "make_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(factory, "make_embedding_client", lambda: FakeEmb())

    resp = test_app.post("/api/settings/test-connection")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generation"]["ok"] is True
    assert body["embedding"]["ok"] is True
    assert body["embedding"]["detail"] == "dimension=4"
