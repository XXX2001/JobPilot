from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class LaTeXInjector:
    """Marker-based text replacement for the letter-pipeline.

    The CV pipeline is marker-free (see ``backend.latex.applicator.CVApplicator``);
    only the letter pipeline still uses JOBPILOT markers. The historical
    ``inject_summary_edit`` / ``inject_experience_edits`` methods were
    removed in the 2026-05-24 dead-code purge — they relied on
    ``CVSummaryEdit`` / ``CVExperienceEdit`` types that were never
    defined.
    """

    def _replace_marker_content(self, tex: str, marker: str, new_content: str) -> str:
        """Replace content between JOBPILOT:MARKER:START and END."""
        pattern = re.compile(
            rf"(% --- JOBPILOT:{marker}:START ---\n).*?(\n% --- JOBPILOT:{marker}:END ---)",
            re.DOTALL,
        )
        replacement = rf"\g<1>{new_content}\g<2>"
        result, count = pattern.subn(replacement, tex)
        if count == 0:
            raise ValueError(f"Marker JOBPILOT:{marker} not found in LaTeX content")
        return result

    def inject_letter_edit(self, original_tex: str, new_paragraph: str, company_name: str) -> str:
        """Replace letter paragraph and {company_name} placeholders."""
        tex = self._replace_marker_content(original_tex, "LETTER:PARA", new_paragraph)
        tex = tex.replace("{company_name}", company_name)
        return tex
