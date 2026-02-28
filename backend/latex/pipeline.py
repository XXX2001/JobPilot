from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from backend.latex.compiler import LaTeXCompiler
from backend.latex.injector import LaTeXInjector
from backend.latex.parser import LaTeXParser
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
    """Generates a tailored CV PDF for a job from a base LaTeX template."""

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
        self._cv_editor = cv_editor  # backend.llm.cv_editor.CVEditor (injected)

    async def generate_tailored_cv(
        self,
        base_cv_path: Path,
        job: JobDetails,
        output_dir: Path,
    ) -> TailoredCV:
        """Produce a tailored CV PDF.

        Steps:
        1. Copy base CV .tex to output_dir / cv.tex
        2. Parse sections from the copy
        3. Ask Gemini to produce edits (if cv_editor provided)
        4. Inject edits back
        5. Compile with Tectonic
        6. Return TailoredCV with paths and diff
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_tex = output_dir / "cv.tex"

        # 1. Copy — never mutate the base file
        shutil.copy2(base_cv_path, dest_tex)

        # 2. Parse
        tex_content = dest_tex.read_text(encoding="utf-8")
        sections = self._parser.extract_sections(tex_content)

        diff: list[DiffEntry] = []
        cv_tailored = False

        # 3 + 4. Edit & inject (only when cv_editor is wired up)
        if self._cv_editor is not None and sections.has_markers:
            try:
                summary_edit = await self._cv_editor.edit_summary(job, sections)
                if summary_edit and summary_edit.edited_summary:
                    original = sections.summary or ""
                    tex_content = self._injector.inject_summary_edit(
                        tex_content, summary_edit.edited_summary
                    )
                    diff.append(
                        DiffEntry(
                            section="summary",
                            original_text=original,
                            edited_text=summary_edit.edited_summary,
                            change_description="; ".join(summary_edit.changes_made),
                        )
                    )

                exp_edit = await self._cv_editor.edit_experience(job, sections)
                if exp_edit and exp_edit.edits:
                    tex_content = self._injector.inject_experience_edits(
                        tex_content, exp_edit.edits
                    )
                    for e in exp_edit.edits:
                        diff.append(
                            DiffEntry(
                                section="experience",
                                original_text=e.original,
                                edited_text=e.edited,
                                change_description=e.reason,
                            )
                        )
            except Exception as exc:
                logger.warning("CV editor failed (%s); using base CV unchanged.", exc)
                diff = []  # no partial diff on failure
        if diff:
            cv_tailored = True
        # Write possibly-edited tex back
        dest_tex.write_text(tex_content, encoding="utf-8")

        # 5. Compile
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
            except Exception as exc:
                logger.warning("Letter editor failed (%s); using base letter.", exc)

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
