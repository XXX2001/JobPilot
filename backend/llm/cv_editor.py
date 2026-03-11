"""CVEditor — uses GeminiClient + prompts to produce surgical LaTeX edits (T13)."""

from __future__ import annotations

import logging
import re
from typing import Optional

from backend.latex.parser import LaTeXSections
from backend.llm.gemini_client import GeminiClient, GeminiJSONError
from backend.llm.prompts import MOTIVATION_LETTER_PROMPT
from backend.llm.validators import LetterEdit
from backend.models.schemas import JobDetails
from backend.security.sanitizer import sanitize_for_prompt

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
            job_title=sanitize_for_prompt(job.title, 300, "title"),
            company=sanitize_for_prompt(job.company, 200, "company"),
            job_description_excerpt=sanitize_for_prompt(
                self._excerpt(job.description), 500, "description"
            ),
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
