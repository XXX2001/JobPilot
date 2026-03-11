"""Tests for the LaTeX pipeline — new whole-CV architecture."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.latex.pipeline import CVPipeline, TailoredCV
from backend.models.schemas import JobDetails
from backend.llm.validators import CVReplacement, CVModifierOutput
from backend.llm.job_context import JobContext

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CV = FIXTURE_DIR / "sample_cv.tex"


def _make_job(job_id: int = 1) -> JobDetails:
    return JobDetails(
        id=job_id,
        title="Senior Python Engineer",
        company="Acme Corp",
        description="We need a Python engineer with experience in distributed systems.",
    )


def _make_context() -> JobContext:
    return JobContext(
        required_skills=["Python", "distributed systems"],
        nice_to_have_skills=[],
        keywords=["scalability"],
        candidate_matches=["Python ✓", "distributed systems ✓"],
        candidate_gaps=[],
        do_not_touch=["dates", "grades"],
        top_changes_hint=["Profile: emphasise distributed systems"],
    )


def _make_replacement(original_text: str, replacement_text: str) -> CVReplacement:
    return CVReplacement(
        section="Profile",
        original_text=original_text,
        replacement_text=replacement_text,
        reason="Emphasise distributed systems",
        job_requirement_matched="distributed systems",
        confidence=0.85,
    )


# ─── Pipeline: no modifiers wired (base CV passthrough) ───────────────────────

def test_cv_pipeline_no_modifiers_compiles(tmp_path: Path):
    """Without modifiers, pipeline copies + compiles the base CV unchanged."""
    if shutil.which("tectonic") is None:
        pytest.skip("Tectonic not installed")

    pipeline = CVPipeline()
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "out")
    )
    assert result.pdf_path.exists()
    assert result.cv_tailored is False
    assert result.diff == []


def test_cv_pipeline_does_not_modify_original(tmp_path: Path):
    """Original base CV file is never mutated."""
    if shutil.which("tectonic") is None:
        pytest.skip("Tectonic not installed")

    original_content = SAMPLE_CV.read_text()
    pipeline = CVPipeline()
    asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(2), tmp_path / "out2")
    )
    assert SAMPLE_CV.read_text() == original_content


# ─── Pipeline: with mocked modifiers ──────────────────────────────────────────

def test_cv_pipeline_with_modifiers_applies_replacement(tmp_path: Path):
    """When modifiers are wired, replacements are applied and diff is populated."""
    if shutil.which("tectonic") is None:
        pytest.skip("Tectonic not installed")

    cv_text = SAMPLE_CV.read_text()
    original_phrase = "Experienced software engineer with 5 years"
    assert original_phrase in cv_text, "Fixture must contain this phrase"

    replacement = _make_replacement(
        original_text=original_phrase,
        replacement_text="Expert software engineer with 5 years",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())
    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(return_value=CVModifierOutput(replacements=[replacement]))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "out3")
    )

    assert result.cv_tailored is True
    assert len(result.diff) == 1
    assert result.diff[0].section == "Profile"
    assert result.pdf_path.exists()


def test_cv_pipeline_caches_job_context(tmp_path: Path):
    """JobAnalyzer.analyze is called only once for the same job_id."""
    if shutil.which("tectonic") is None:
        pytest.skip("Tectonic not installed")

    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())
    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(return_value=CVModifierOutput(replacements=[]))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    job = _make_job(job_id=99)
    for i in range(3):
        asyncio.get_event_loop().run_until_complete(
            pipeline.generate_tailored_cv(SAMPLE_CV, job, tmp_path / f"out{i}")
        )

    assert mock_analyzer.analyze.call_count == 1


def test_cv_pipeline_modifier_failure_falls_back(tmp_path: Path):
    """If CVModifier raises, pipeline falls back to unmodified base CV."""
    if shutil.which("tectonic") is None:
        pytest.skip("Tectonic not installed")

    from backend.llm.gemini_client import GeminiJSONError
    mock_analyzer = MagicMock()
    mock_analyzer.analyze = AsyncMock(return_value=_make_context())
    mock_modifier = MagicMock()
    mock_modifier.modify = AsyncMock(side_effect=GeminiJSONError("fail"))

    from backend.latex.applicator import CVApplicator
    pipeline = CVPipeline(
        job_analyzer=mock_analyzer,
        cv_modifier=mock_modifier,
        cv_applicator=CVApplicator(),
    )
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, _make_job(), tmp_path / "fallback")
    )

    assert result.cv_tailored is False
    assert result.diff == []
    assert result.pdf_path.exists()


@pytest.mark.asyncio
async def test_generate_base_cv(tmp_path):
    """generate_base_cv should copy tex, compile, and set cv_tailored=False."""
    base_cv = tmp_path / "templates" / "cv.tex"
    base_cv.parent.mkdir(parents=True)
    base_cv.write_text(r"\documentclass{article}\begin{document}Hello\end{document}")

    out_dir = tmp_path / "output"

    compiler = MagicMock()
    compiler.compile = AsyncMock(return_value=out_dir / "cv.pdf")

    pipeline = CVPipeline(compiler=compiler)
    result = await pipeline.generate_base_cv(
        base_cv_path=base_cv,
        job=_make_job(),
        output_dir=out_dir,
    )

    assert isinstance(result, TailoredCV)
    assert result.cv_tailored is False
    assert result.diff == []
    assert (out_dir / "cv.tex").exists()
    compiler.compile.assert_awaited_once()
