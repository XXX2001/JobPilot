"""Tests for error handling, retry logic, and graceful degradation (T31)."""

from __future__ import annotations

import asyncio
import unittest.mock as mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Scraper retry logic ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scraper_retries_on_agent_failure():
    """AdaptiveScraper retries up to 3 times on browser-use agent failure."""
    from backend.scraping.adaptive_scraper import AdaptiveScraper

    scraper = AdaptiveScraper(gemini_api_key="test-key")

    call_count = 0

    async def _failing_agent_run():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated browser failure")

    fake_agent = MagicMock()
    fake_agent.run = _failing_agent_run

    fake_browser = MagicMock()
    fake_browser.close = AsyncMock()

    fake_llm = MagicMock()
    scraper._make_llm = MagicMock(return_value=fake_llm)

    # Patch at the browser_use package level — the code does:
    #   from browser_use import Agent, Browser
    # so patching browser_use.Agent replaces the attribute before the local
    # `from … import` resolves it at call-time.
    with (
        patch("browser_use.Agent", return_value=fake_agent),
        patch("browser_use.Browser", return_value=fake_browser),
        patch("asyncio.sleep", new_callable=AsyncMock),  # speed up test
    ):
        result = await scraper.scrape_job_listings(
            url="https://example.com/jobs",
            keywords=["python"],
        )

    # Should return empty list (graceful degradation) after 2 failed attempts
    assert result == []
    assert call_count == 2


@pytest.mark.asyncio
async def test_scraper_succeeds_on_second_attempt():
    """AdaptiveScraper returns results when second attempt succeeds."""
    from backend.scraping.adaptive_scraper import AdaptiveScraper

    scraper = AdaptiveScraper(gemini_api_key="test-key")

    attempt = 0

    async def _flaky_run():
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("first attempt fails")
        # Second attempt: return a fake result object
        result = MagicMock()
        result.final_result.return_value = (
            '[{"title": "Python Dev", "company": "ACME", "location": "Remote",'
            ' "apply_url": "https://acme.com/apply", "apply_method": "redirect"}]'
        )
        return result

    fake_agent = MagicMock()
    fake_agent.run = _flaky_run

    fake_browser = MagicMock()
    fake_browser.close = AsyncMock()

    scraper._make_llm = MagicMock(return_value=MagicMock())

    with (
        patch("browser_use.Agent", return_value=fake_agent),
        patch("browser_use.Browser", return_value=fake_browser),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await scraper.scrape_job_listings(
            url="https://example.com/jobs",
            keywords=["python"],
        )

    assert len(result) == 1
    assert result[0].title == "Python Dev"
    assert attempt == 2


# ─── CV Pipeline fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_pipeline_fallback_when_gemini_fails(tmp_path):
    """CVPipeline returns base CV (cv_tailored=False) when Gemini modifier fails."""
    from backend.latex.pipeline import CVPipeline
    from backend.models.schemas import JobDetails

    # Create a minimal base CV
    base_tex = tmp_path / "base.tex"
    base_tex.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")

    output_dir = tmp_path / "out"

    # job_analyzer and cv_modifier that always raise
    failing_analyzer = MagicMock()
    failing_analyzer.analyze = AsyncMock(side_effect=RuntimeError("Gemini down"))
    failing_modifier = MagicMock()
    failing_modifier.modify = AsyncMock(side_effect=RuntimeError("Gemini down"))

    # Compiler that always succeeds (returns a fake PDF path)
    fake_pdf = tmp_path / "out" / "cv.pdf"
    fake_compiler = MagicMock()

    async def _fake_compile(tex_path, out_dir):  # noqa: ARG001
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_pdf.parent.mkdir(parents=True, exist_ok=True)
        fake_pdf.write_bytes(b"%PDF-1.4 fake")
        return fake_pdf

    fake_compiler.compile = _fake_compile

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        compiler=fake_compiler,
        job_analyzer=failing_analyzer,
        cv_modifier=failing_modifier,
        cv_applicator=CVApplicator(),
    )

    job = JobDetails(
        title="Software Engineer",
        company="TestCorp",
        location="Remote",
        description="We need a Python developer.",
        url="https://testcorp.com/jobs/1",
        apply_url="https://testcorp.com/jobs/1/apply",
        apply_method="redirect",
    )

    result = await pipeline.generate_tailored_cv(base_tex, job, output_dir)

    # Pipeline must not raise; cv_tailored flag is False; diff is empty
    assert result.cv_tailored is False
    assert result.diff == []
    assert result.pdf_path.exists()


# ─── Gemini JSON parse retry ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_json_retry_on_malformed_output():
    """GeminiClient retries JSON parsing once, then raises GeminiJSONError."""
    from backend.llm.gemini_client import GeminiClient, GeminiJSONError
    from pydantic import BaseModel

    class DummySchema(BaseModel):
        value: int

    client = GeminiClient.__new__(GeminiClient)
    client._call_times = __import__("collections").deque(maxlen=15)
    client._lock = asyncio.Lock()
    client._model_name = "gemini-2.0-flash"

    # Both calls return garbage JSON
    call_count = 0

    async def _bad_generate(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return "not valid json at all"

    client.generate_text = _bad_generate  # type: ignore[method-assign]

    with pytest.raises(GeminiJSONError):
        await client.generate_json("test prompt", DummySchema)

    # Should have tried twice (original + 1 retry)
    assert call_count == 2


@pytest.mark.asyncio
async def test_gemini_json_succeeds_on_retry():
    """GeminiClient returns valid schema if retry produces valid JSON."""
    from backend.llm.gemini_client import GeminiClient
    from pydantic import BaseModel

    class DummySchema(BaseModel):
        value: int

    client = GeminiClient.__new__(GeminiClient)
    client._call_times = __import__("collections").deque(maxlen=15)
    client._lock = asyncio.Lock()
    client._model_name = "gemini-2.0-flash"

    call_count = 0

    async def _flaky_generate(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "```garbage```"
        return '{"value": 42}'

    client.generate_text = _flaky_generate  # type: ignore[method-assign]

    result = await client.generate_json("test prompt", DummySchema)
    assert result.value == 42
    assert call_count == 2


# ─── Global exception handler ────────────────────────────────────────────────


def test_exception_handlers_registered():
    """The JobPilot app registers a catch-all Exception handler."""
    from backend.main import app

    # FastAPI stores handlers in app.exception_handlers (a dict keyed by exc type)
    # We verify that the generic Exception handler was registered.
    assert Exception in app.exception_handlers


def test_global_exception_handler_returns_json():
    """Unhandled exceptions return JSON {error, code} instead of raw 500 HTML."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    # Build a minimal app and attach the same exception handler used by main.py
    from backend.main import app as main_app

    mini_app = FastAPI()

    # Copy the exception handler from the main app
    generic_handler = main_app.exception_handlers.get(Exception)
    if generic_handler is not None:
        mini_app.add_exception_handler(Exception, generic_handler)

    @mini_app.get("/api/explode")
    async def explode():
        raise RuntimeError("intentional test explosion")

    with TestClient(mini_app, raise_server_exceptions=False) as c:
        resp = c.get("/api/explode")

    assert resp.status_code == 500
    data = resp.json()
    assert data.get("code") == "internal_error"
    assert "error" in data
    # Must NOT expose internal details
    assert "intentional test explosion" not in data.get("error", "")
