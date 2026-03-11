from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from backend.latex.compiler import LaTeXCompiler
from backend.latex.injector import LaTeXInjector
from backend.latex.parser import LaTeXParser
from backend.llm.gemini_client import GeminiJSONError, GeminiRateLimitError
from backend.models.schemas import JobDetails

logger = logging.getLogger(__name__)


@dataclass
class TailoredCV:
    """Result of a successful CV tailoring run."""

    job_id: int | None
    tex_path: Path
    pdf_path: Path
    diff: list["DiffEntry"]
    cv_tailored: bool = True  # False when Gemini editing failed and base CV was used


@dataclass
class TailoredLetter:
    """Result of a successful letter tailoring run."""

    job_id: int | None
    tex_path: Path
    pdf_path: Path


class CVPipeline:
    """Generates a tailored CV PDF for a job from a base LaTeX template.

    New architecture (marker-free):
    1. Copy base CV .tex to output_dir/cv.tex
    2. Run JobAnalyzer → JobContext (cached per job_id)
    3. Run CVModifier (whole CV text + context) → CVModifierOutput
    4. Run CVApplicator → apply validated replacements
    5. Compile with Tectonic
    6. Return TailoredCV with paths and diff
    """

    def __init__(
        self,
        compiler: LaTeXCompiler | None = None,
        job_analyzer=None,
        cv_modifier=None,
        cv_applicator=None,
    ) -> None:
        self._compiler = compiler or LaTeXCompiler()
        self._job_analyzer = job_analyzer   # backend.llm.job_analyzer.JobAnalyzer
        self._cv_modifier = cv_modifier     # backend.llm.cv_modifier.CVModifier
        self._cv_applicator = cv_applicator # backend.latex.applicator.CVApplicator
        self._context_cache: dict[int, tuple[float, object]] = {}  # job_id → (timestamp, context)

    async def generate_base_cv(
        self,
        base_cv_path: Path,
        job: JobDetails,
        output_dir: Path,
    ) -> TailoredCV:
        """Copy base CV and compile PDF without any LLM modification."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_tex = output_dir / "cv.tex"

        shutil.copy2(base_cv_path, dest_tex)
        for support_file in base_cv_path.parent.iterdir():
            if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
                shutil.copy2(support_file, output_dir / support_file.name)

        pdf_path = await self._compiler.compile(dest_tex, output_dir)

        return TailoredCV(
            job_id=job.id,
            tex_path=dest_tex,
            pdf_path=pdf_path,
            diff=[],
            cv_tailored=False,
        )

    async def generate_tailored_cv(
        self,
        base_cv_path: Path,
        job: JobDetails,
        output_dir: Path,
        additional_context: str = "",
        fit_assessment=None,  # Optional FitAssessment
    ) -> TailoredCV:
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_tex = output_dir / "cv.tex"

        # 1. Copy — never mutate the base file.
        shutil.copy2(base_cv_path, dest_tex)
        for support_file in base_cv_path.parent.iterdir():
            if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
                shutil.copy2(support_file, output_dir / support_file.name)
        cv_tex = dest_tex.read_text(encoding="utf-8")

        diff: list[DiffEntry] = []
        cv_tailored = False

        # 2-4. Analyze + modify
        if self._cv_modifier is not None and self._cv_applicator is not None:
            try:
                if fit_assessment is not None and hasattr(self._cv_modifier, "modify_from_assessment"):
                    # Assessment-driven path — skip JobAnalyzer entirely
                    modifier_output = await self._cv_modifier.modify_from_assessment(
                        job, cv_tex, fit_assessment
                    )
                elif self._job_analyzer is not None:
                    # Fallback path — original JobAnalyzer + CVModifier flow
                    job_id = job.id
                    context = None
                    if job_id is not None and job_id in self._context_cache:
                        ts, cached = self._context_cache[job_id]
                        if monotonic() - ts < 3600:
                            context = cached
                        else:
                            del self._context_cache[job_id]
                    if context is None:
                        context = await self._job_analyzer.analyze(job, cv_content=cv_tex)
                        if job_id is not None:
                            if len(self._context_cache) >= 100:
                                oldest_key = next(iter(self._context_cache))
                                del self._context_cache[oldest_key]
                            self._context_cache[job_id] = (monotonic(), context)

                    modifier_output = await self._cv_modifier.modify(
                        job, cv_tex, context, additional_context=additional_context
                    )
                else:
                    modifier_output = None

                if modifier_output is not None:
                    cv_tex, applied = self._cv_applicator.apply(
                        cv_tex, modifier_output.replacements
                    )
                    diff = [
                        DiffEntry(
                            section=r.section,
                            original_text=r.original_text,
                            edited_text=r.replacement_text,
                            change_description=r.reason,
                        )
                        for r in applied
                    ]
                    cv_tailored = bool(diff)

            except (GeminiRateLimitError, GeminiJSONError) as exc:
                logger.warning("CV modifier LLM error (%s); using base CV unchanged.", exc)
                cv_tex = dest_tex.read_text(encoding="utf-8")
                diff = []
            except Exception as exc:
                logger.error(
                    "CV modifier unexpected failure (%s: %s); using base CV unchanged.",
                    type(exc).__name__, exc, exc_info=True,
                )
                cv_tex = dest_tex.read_text(encoding="utf-8")
                diff = []

        dest_tex.write_text(cv_tex, encoding="utf-8")
        pdf_path = await self._compiler.compile(dest_tex, output_dir)

        return TailoredCV(
            job_id=job.id,
            tex_path=dest_tex,
            pdf_path=pdf_path,
            diff=diff,
            cv_tailored=cv_tailored,
        )


class LetterPipeline:
    """Generates a tailored motivation letter PDF for a job."""

    def __init__(
        self,
        compiler: LaTeXCompiler | None = None,
        parser: LaTeXParser | None = None,
        injector: LaTeXInjector | None = None,
        cv_editor=None,
    ) -> None:
        self._compiler = compiler or LaTeXCompiler()
        self._parser = parser or LaTeXParser()
        self._injector = injector or LaTeXInjector()
        self._cv_editor = cv_editor

    async def generate_tailored_letter(
        self,
        base_letter_path: Path,
        job: JobDetails,
        output_dir: Path,
    ) -> TailoredLetter:
        """Produce a tailored letter PDF."""
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_tex = output_dir / "letter.tex"

        shutil.copy2(base_letter_path, dest_tex)
        for support_file in base_letter_path.parent.iterdir():
            if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
                shutil.copy2(support_file, output_dir / support_file.name)
        tex_content = dest_tex.read_text(encoding="utf-8")
        sections = self._parser.extract_sections(tex_content)

        if self._cv_editor is not None and sections.has_markers:
            try:
                letter_edit = await self._cv_editor.edit_letter(job, sections)
                if letter_edit and letter_edit.edited_paragraph:
                    tex_content = self._injector.inject_letter_edit(
                        tex_content,
                        letter_edit.edited_paragraph,
                        letter_edit.company_name,
                    )
            except (GeminiRateLimitError, GeminiJSONError) as exc:
                logger.warning("Letter editor LLM error (%s); using base letter.", exc)
            except Exception as exc:
                logger.error(
                    "Letter editor unexpected failure (%s: %s); using base letter.",
                    type(exc).__name__, exc, exc_info=True,
                )

        dest_tex.write_text(tex_content, encoding="utf-8")
        pdf_path = await self._compiler.compile(dest_tex, output_dir)

        return TailoredLetter(
            job_id=job.id,
            tex_path=dest_tex,
            pdf_path=pdf_path,
        )


# ─── Diff helpers ────────────────────────────────────────────────────────────


@dataclass
class DiffEntry:
    """One changed section between original and edited CV/letter."""

    section: str
    original_text: str
    edited_text: str
    change_description: str


def generate_diff(
    original_sections,
    edits,
) -> list[DiffEntry]:
    """Produce structured diff entries from editor outputs.

    Args:
        original_sections: LaTeXSections parsed from the base template.
        edits: A tuple/list of (CVSummaryEdit | None, CVExperienceEdit | None, LetterEdit | None).
    """
    diff: list[DiffEntry] = []
    summary_edit, exp_edit, letter_edit = edits if len(edits) == 3 else (None, None, None)

    if summary_edit and summary_edit.edited_summary:
        diff.append(
            DiffEntry(
                section="summary",
                original_text=original_sections.summary or "",
                edited_text=summary_edit.edited_summary,
                change_description="; ".join(summary_edit.changes_made),
            )
        )

    if exp_edit:
        for e in exp_edit.edits:
            diff.append(
                DiffEntry(
                    section="experience",
                    original_text=e.original,
                    edited_text=e.edited,
                    change_description=e.reason,
                )
            )

    if letter_edit and letter_edit.edited_paragraph:
        diff.append(
            DiffEntry(
                section="letter",
                original_text=original_sections.letter_paragraph or "",
                edited_text=letter_edit.edited_paragraph,
                change_description=f"Customized for {letter_edit.company_name}",
            )
        )

    return diff
