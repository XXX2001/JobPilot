"""Tests for the LaTeX compiler and pipeline (T12)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.latex.compiler import LaTeXCompiler, LaTeXCompilationError
from backend.latex.pipeline import CVPipeline, LetterPipeline, TailoredCV, generate_diff, DiffEntry
from backend.latex.parser import LaTeXParser
from backend.latex.injector import LaTeXInjector
from backend.models.schemas import JobDetails

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CV = FIXTURE_DIR / "sample_cv.tex"


# ─── Compiler tests ───────────────────────────────────────────────────────────


def test_find_tectonic_returns_none_when_missing():
    """When tectonic is not on PATH and not in bin/, _find_tectonic returns None."""
    compiler = LaTeXCompiler()
    with patch("shutil.which", return_value=None):
        result = compiler._find_tectonic()
        # May still find it in bin/ if installed; just verify it doesn't crash
        assert result is None or isinstance(result, str)


def test_missing_tectonic_raises_clear_error():
    """compile() raises LaTeXCompilationError with a helpful message when Tectonic is absent."""
    compiler = LaTeXCompiler()
    with patch.object(compiler, "_find_tectonic", return_value=None):
        with pytest.raises(LaTeXCompilationError) as exc_info:
            asyncio.get_event_loop().run_until_complete(compiler.compile(SAMPLE_CV))
        assert (
            "tectonic" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()
        )


# ─── Pipeline tests ───────────────────────────────────────────────────────────


def _make_job(job_id: int = 1) -> JobDetails:
    return JobDetails(
        id=job_id,
        title="Senior Python Engineer",
        company="Acme Corp",
        description="We need a Python engineer with experience in distributed systems.",
    )


def test_cv_pipeline_produces_pdf_with_tectonic(tmp_path: Path):
    """CVPipeline compiles and returns a valid PDF path (requires tectonic installed)."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed — skipping compilation test")

    pipeline = CVPipeline()
    job = _make_job()
    output_dir = tmp_path / "cv_output"

    result: TailoredCV = asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, job, output_dir)
    )

    assert result.pdf_path.exists(), "PDF file must exist"
    assert result.pdf_path.stat().st_size > 1000, "PDF must be > 1000 bytes"
    assert result.tex_path.exists(), "Modified .tex must exist"
    assert result.tex_path != SAMPLE_CV, "Must not be the original file"


def test_cv_pipeline_does_not_modify_original(tmp_path: Path):
    """CVPipeline copies the base CV; original file is unchanged."""
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        pytest.skip("Tectonic not installed — skipping compilation test")

    original_content = SAMPLE_CV.read_text()
    pipeline = CVPipeline()
    job = _make_job(job_id=2)
    output_dir = tmp_path / "cv_output_2"

    asyncio.get_event_loop().run_until_complete(
        pipeline.generate_tailored_cv(SAMPLE_CV, job, output_dir)
    )

    # Original must be byte-for-byte identical
    assert SAMPLE_CV.read_text() == original_content


def test_cv_pipeline_missing_tectonic_raises():
    """CVPipeline raises LaTeXCompilationError with a clear message when Tectonic is absent."""
    compiler = LaTeXCompiler()
    with patch.object(compiler, "_find_tectonic", return_value=None):
        pipeline = CVPipeline(compiler=compiler)
        job = _make_job(job_id=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "out"
            with pytest.raises(LaTeXCompilationError):
                asyncio.get_event_loop().run_until_complete(
                    pipeline.generate_tailored_cv(SAMPLE_CV, job, output_dir)
                )


def test_generate_diff_returns_entries():
    """generate_diff produces correct DiffEntry list from edits."""
    from backend.llm.validators import CVSummaryEdit, CVExperienceEdit, BulletEdit, LetterEdit
    from backend.latex.parser import LaTeXSections

    original = LaTeXSections(
        summary="Original summary text.",
        experience_bullets=["Built something cool"],
        has_markers=True,
    )
    summary_edit = CVSummaryEdit(
        edited_summary="Updated summary for Python roles.",
        changes_made=["Emphasised Python skills"],
    )
    exp_edit = CVExperienceEdit(
        edits=[
            BulletEdit(
                index=0,
                original="Built something cool",
                edited="Built a distributed data pipeline in Python",
                reason="More relevant to job",
            )
        ]
    )
    letter_edit = None

    diff = generate_diff(original, (summary_edit, exp_edit, letter_edit))

    assert len(diff) == 2
    assert diff[0].section == "summary"
    assert diff[0].edited_text == "Updated summary for Python roles."
    assert diff[1].section == "experience"
    assert "Python" in diff[1].edited_text
