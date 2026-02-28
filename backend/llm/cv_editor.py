"""CVEditor — uses GeminiClient + prompts to produce surgical LaTeX edits (T13)."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.llm.gemini_client import GeminiClient, GeminiJSONError
from backend.llm.prompts import CV_SUMMARY_PROMPT, CV_EXPERIENCE_PROMPT, MOTIVATION_LETTER_PROMPT
from backend.llm.validators import CVSummaryEdit, CVExperienceEdit, BulletEdit, LetterEdit
from backend.latex.parser import LaTeXSections
from backend.models.schemas import JobDetails

logger = logging.getLogger(__name__)

# LaTeX command pattern — used to reject edits that sneak in LaTeX markup
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _has_new_latex_commands(original: str, edited: str) -> bool:
    """Return True if edited introduces LaTeX commands not present in original."""
    orig_cmds = set(_LATEX_CMD_RE.findall(original))
    edit_cmds = set(_LATEX_CMD_RE.findall(edited))
    new_cmds = edit_cmds - orig_cmds
    return bool(new_cmds)


class CVEditor:
    """High-level editor that orchestrates Gemini prompts for CV tailoring."""

    MAX_DESCRIPTION_CHARS = 500

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    def _excerpt(self, text: str) -> str:
        """Truncate job description to MAX_DESCRIPTION_CHARS."""
        if len(text) <= self.MAX_DESCRIPTION_CHARS:
            return text
        return text[: self.MAX_DESCRIPTION_CHARS] + "…"

    async def edit_summary(
        self,
        job: JobDetails,
        sections: LaTeXSections,
    ) -> Optional[CVSummaryEdit]:
        """Return CVSummaryEdit for the summary section, or None if nothing to edit."""
        if not sections.summary:
            logger.debug("No summary section found; skipping summary edit.")
            return None

        prompt = CV_SUMMARY_PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description_excerpt=self._excerpt(job.description),
            current_summary=sections.summary,
        )

        try:
            edit = await self._client.generate_json(prompt, CVSummaryEdit)
        except GeminiJSONError as exc:
            logger.warning("Gemini JSON error for summary edit: %s", exc)
            raise

        # Validate: reject if LLM injected new LaTeX commands
        if edit.edited_summary and _has_new_latex_commands(sections.summary, edit.edited_summary):
            logger.warning("Summary edit contains new LaTeX commands — discarding.")
            return CVSummaryEdit(edited_summary=None, changes_made=[])

        return edit

    async def edit_experience(
        self,
        job: JobDetails,
        sections: LaTeXSections,
    ) -> Optional[CVExperienceEdit]:
        """Return CVExperienceEdit for experience bullets, or None if no bullets."""
        if not sections.experience_bullets:
            logger.debug("No experience bullets; skipping experience edit.")
            return None

        bullets_json = json.dumps(
            [{"index": i, "text": b} for i, b in enumerate(sections.experience_bullets)],
            ensure_ascii=False,
        )

        # Build a short list of key requirements from the description
        words = re.findall(r"\b[A-Za-z][a-z]+\b", job.description)
        key_reqs = ", ".join(sorted(set(words))[:12]) if words else job.description[:100]

        prompt = CV_EXPERIENCE_PROMPT.format(
            job_title=job.title,
            company=job.company,
            key_requirements=key_reqs,
            bullets_json=bullets_json,
        )

        try:
            edit = await self._client.generate_json(prompt, CVExperienceEdit)
        except GeminiJSONError as exc:
            logger.warning("Gemini JSON error for experience edit: %s", exc)
            raise

        # Validate each bullet edit — reject ones that add LaTeX commands
        clean_edits: list[BulletEdit] = []
        for e in edit.edits:
            orig = (
                sections.experience_bullets[e.index]
                if e.index < len(sections.experience_bullets)
                else ""
            )
            if _has_new_latex_commands(orig, e.edited):
                logger.warning(
                    "Experience edit at index %d introduces LaTeX commands — skipping.",
                    e.index,
                )
                continue
            clean_edits.append(e)

        return CVExperienceEdit(edits=clean_edits)

    async def edit_letter(
        self,
        job: JobDetails,
        sections: LaTeXSections,
    ) -> Optional[LetterEdit]:
        """Return LetterEdit for the customizable paragraph, or None if absent."""
        if not sections.letter_paragraph:
            logger.debug("No letter paragraph section; skipping letter edit.")
            return None

        # Reconstruct a minimal letter view showing the customizable section
        letter_content = (
            "...\n"
            "% --- JOBPILOT:LETTER:PARA:START ---\n"
            f"{sections.letter_paragraph}\n"
            "% --- JOBPILOT:LETTER:PARA:END ---\n"
            "..."
        )

        prompt = MOTIVATION_LETTER_PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description_excerpt=self._excerpt(job.description),
            letter_content=letter_content,
        )

        try:
            edit = await self._client.generate_json(prompt, LetterEdit)
        except GeminiJSONError as exc:
            logger.warning("Gemini JSON error for letter edit: %s", exc)
            raise

        # Validate: letter paragraph must not introduce new LaTeX commands
        if _has_new_latex_commands(sections.letter_paragraph, edit.edited_paragraph):
            logger.warning("Letter edit introduces LaTeX commands — using original.")
            return LetterEdit(
                edited_paragraph=sections.letter_paragraph,
                company_name=job.company,
            )

        return edit
