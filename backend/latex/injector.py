from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_LATEX_SPECIALS = [
    ("\\", r"\textbackslash{}"),  # must run first
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
]


def _escape_latex(value: str) -> str:
    """Escape LaTeX-special characters so substituted text cannot inject commands."""
    # Backslash is replaced first (via a sentinel) so the braces it introduces
    # as part of ``\textbackslash{}`` are not re-escaped by the { / } passes.
    sentinel = "\x00"
    out = value.replace("\\", sentinel)
    for char, replacement in _LATEX_SPECIALS:
        if char == "\\":
            continue
        out = out.replace(char, replacement)
    return out.replace(sentinel, r"\textbackslash{}")


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
        tex = tex.replace("{company_name}", _escape_latex(company_name))
        return tex
