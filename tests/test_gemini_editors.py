"""Tests for CVEditor (T13) — all using mocked GeminiClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.cv_editor import CVEditor, _has_new_latex_commands
from backend.llm.gemini_client import GeminiJSONError
from backend.llm.validators import CVSummaryEdit, CVExperienceEdit, BulletEdit, LetterEdit
from backend.latex.parser import LaTeXSections
from backend.models.schemas import JobDetails


def _make_job() -> JobDetails:
    return JobDetails(
        id=1,
        title="Backend Engineer",
        company="StartupX",
        description="We use Python, FastAPI, and distributed systems heavily.",
    )


def _make_sections() -> LaTeXSections:
    return LaTeXSections(
        summary="Software engineer with 5 years experience in Python and cloud.",
        experience_bullets=[
            "Built distributed pipeline processing 10TB/day",
            "Led migration to microservices reducing deploy time 80%",
        ],
        letter_paragraph="I am excited to apply to {company_name} because of its innovative culture.",
        has_markers=True,
    )


def _mock_client(return_value) -> MagicMock:
    """Return a mock GeminiClient whose generate_json awaits return_value."""
    client = MagicMock()
    client.generate_json = AsyncMock(return_value=return_value)
    return client


# ─── edit_summary ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_summary_returns_cv_summary_edit():
    """edit_summary returns a CVSummaryEdit instance with the mocked response."""
    expected = CVSummaryEdit(
        edited_summary="Python backend engineer with 5 years and FastAPI experience.",
        changes_made=["Added FastAPI mention"],
    )
    editor = CVEditor(client=_mock_client(expected))
    result = await editor.edit_summary(_make_job(), _make_sections())

    assert isinstance(result, CVSummaryEdit)
    assert result.edited_summary is not None
    assert "FastAPI" in result.edited_summary


@pytest.mark.asyncio
async def test_edit_summary_none_when_no_summary():
    """edit_summary returns None when sections has no summary."""
    editor = CVEditor(client=_mock_client(None))
    sections = LaTeXSections(has_markers=True)  # summary=None
    result = await editor.edit_summary(_make_job(), sections)
    assert result is None


@pytest.mark.asyncio
async def test_edit_summary_rejects_latex_commands():
    """edit_summary discards edits that introduce new LaTeX commands."""
    bad_edit = CVSummaryEdit(
        edited_summary="\\textbf{Python} backend engineer with 5 years experience.",
        changes_made=["Added bold"],
    )
    editor = CVEditor(client=_mock_client(bad_edit))
    result = await editor.edit_summary(_make_job(), _make_sections())

    # Should be discarded → edited_summary=None
    assert result is not None
    assert result.edited_summary is None


@pytest.mark.asyncio
async def test_edit_summary_raises_on_gemini_json_error():
    """edit_summary propagates GeminiJSONError."""
    client = MagicMock()
    client.generate_json = AsyncMock(side_effect=GeminiJSONError("bad json"))
    editor = CVEditor(client=client)
    with pytest.raises(GeminiJSONError):
        await editor.edit_summary(_make_job(), _make_sections())


# ─── edit_experience ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_experience_returns_experience_edit():
    """edit_experience returns a CVExperienceEdit with changed bullets only."""
    expected = CVExperienceEdit(
        edits=[
            BulletEdit(
                index=0,
                original="Built distributed pipeline processing 10TB/day",
                edited="Built FastAPI-based distributed pipeline processing 10TB/day",
                reason="Added FastAPI relevance",
            )
        ]
    )
    editor = CVEditor(client=_mock_client(expected))
    result = await editor.edit_experience(_make_job(), _make_sections())

    assert isinstance(result, CVExperienceEdit)
    assert len(result.edits) == 1
    assert "FastAPI" in result.edits[0].edited


@pytest.mark.asyncio
async def test_edit_experience_skips_latex_command_edits():
    """edit_experience discards bullets that introduce new LaTeX commands."""
    bad_edit = CVExperienceEdit(
        edits=[
            BulletEdit(
                index=0,
                original="Built distributed pipeline",
                edited="\\textit{Built} distributed pipeline",
                reason="Added italic",
            )
        ]
    )
    editor = CVEditor(client=_mock_client(bad_edit))
    result = await editor.edit_experience(_make_job(), _make_sections())

    assert result is not None
    # The bad bullet should be filtered out
    assert len(result.edits) == 0


# ─── edit_letter ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_letter_returns_letter_edit():
    """edit_letter returns a LetterEdit with company_name populated."""
    expected = LetterEdit(
        edited_paragraph="I am excited to apply to StartupX because of its Python-driven culture.",
        company_name="StartupX",
    )
    editor = CVEditor(client=_mock_client(expected))
    result = await editor.edit_letter(_make_job(), _make_sections())

    assert isinstance(result, LetterEdit)
    assert result.company_name == "StartupX"
    assert "StartupX" in result.edited_paragraph


@pytest.mark.asyncio
async def test_edit_letter_none_when_no_paragraph():
    """edit_letter returns None when no letter paragraph exists in sections."""
    editor = CVEditor(client=_mock_client(None))
    sections = LaTeXSections(has_markers=True)  # letter_paragraph=None
    result = await editor.edit_letter(_make_job(), sections)
    assert result is None


# ─── _has_new_latex_commands helper ───────────────────────────────────────────


def test_has_new_latex_commands_detects_additions():
    original = "Some plain text."
    edited = "Some \\textbf{plain} text."
    assert _has_new_latex_commands(original, edited) is True


def test_has_new_latex_commands_no_new():
    original = "Text with \\textbf{bold} already."
    edited = "Text with \\textbf{bold} and more stuff."
    assert _has_new_latex_commands(original, edited) is False


def test_has_new_latex_commands_same_commands():
    original = "Use \\item for bullets."
    edited = "Use \\item for Python bullets."
    assert _has_new_latex_commands(original, edited) is False
