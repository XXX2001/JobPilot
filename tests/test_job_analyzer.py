"""Tests for JobAnalyzer — all using mocked GeminiClient."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.llm.job_analyzer import JobAnalyzer
from backend.llm.job_context import JobContext
from backend.models.schemas import JobDetails


def _make_job() -> JobDetails:
    return JobDetails(
        id=42,
        title="Quality Control Technician",
        company="Nestlé",
        description="Requires HACCP, GMP, aseptic sampling. ISO 22000 preferred.",
    )


def _mock_client(return_value) -> MagicMock:
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=return_value)
    return client


def _make_context() -> JobContext:
    return JobContext(
        required_skills=["HACCP", "GMP", "aseptic sampling"],
        nice_to_have_skills=["ISO 22000"],
        keywords=["food safety", "quality control"],
        candidate_matches=["HACCP ✓", "GMP ✓", "aseptic sampling ✓"],
        candidate_gaps=["ISO 22000"],
        do_not_touch=["education dates", "grades", "company names", "certifications"],
        top_changes_hint=["Profile: add motivation to learn ISO 22000"],
    )


@pytest.mark.asyncio
async def test_job_analyzer_returns_job_context():
    analyzer = JobAnalyzer(client=_mock_client(_make_context()))
    result = await analyzer.analyze(_make_job())
    assert isinstance(result, JobContext)
    assert "HACCP" in result.required_skills


@pytest.mark.asyncio
async def test_job_analyzer_context_markdown_is_valid():
    analyzer = JobAnalyzer(client=_mock_client(_make_context()))
    ctx = await analyzer.analyze(_make_job())
    md = ctx.to_markdown("Quality Control Technician", "Nestlé")
    assert len(md) > 100
    assert "Nestlé" in md
    assert "HACCP" in md


@pytest.mark.asyncio
async def test_job_analyzer_propagates_gemini_error():
    from backend.llm.gemini_client import GeminiJSONError
    client = MagicMock()
    client.generate_json = AsyncMock(side_effect=GeminiJSONError("bad json"))
    analyzer = JobAnalyzer(client=client)
    with pytest.raises(GeminiJSONError):
        await analyzer.analyze(_make_job())
