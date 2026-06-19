"""CVApplicator — validates and applies CVReplacement items to a LaTeX string."""
from __future__ import annotations

import logging
import re

from backend.llm.validators import CVReplacement

logger = logging.getLogger(__name__)

_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _has_new_latex_commands(original: str, edited: str) -> bool:
    orig_cmds = set(_LATEX_CMD_RE.findall(original))
    edit_cmds = set(_LATEX_CMD_RE.findall(edited))
    return bool(edit_cmds - orig_cmds)


def _locate(haystack: str, needle: str) -> str | None:
    """Return the substring of *haystack* that matches *needle*, or None.

    Tries an exact match first, then a whitespace-tolerant match: LLMs routinely
    return ``original_text`` with the right words but different run-of-spaces or
    line-wrapping than the source (e.g. a wrapped LaTeX table cell). Collapsing
    every whitespace run in the needle to ``\\s+`` lets those near-misses still
    anchor — returning the *actual* source span so the caller replaces and
    LaTeX-validates against what's really in the document, not the LLM's guess.
    """
    if needle in haystack:
        return needle
    collapsed = needle.strip()
    if not collapsed:
        return None
    pattern = re.compile(r"\s+".join(re.escape(tok) for tok in collapsed.split()))
    m = pattern.search(haystack)
    return m.group(0) if m else None


class CVApplicator:
    """Applies a list of CVReplacement items to a LaTeX string with per-item validation.

    Validation order per replacement:
    1. confidence >= 0.7
    2. original_text exists verbatim in current tex
    3. replacement_text introduces no new LaTeX commands

    Enforced cap: at most MAX_REPLACEMENTS applied, taken by highest confidence first.
    """

    MAX_REPLACEMENTS = 3

    def apply(
        self,
        cv_tex: str,
        replacements: list[CVReplacement],
    ) -> tuple[str, list[CVReplacement]]:
        """Apply validated replacements to cv_tex.

        Returns:
            (modified_tex, applied_replacements) — modified_tex equals cv_tex if nothing applied.
        """
        # Sort by confidence descending, cap at MAX_REPLACEMENTS
        candidates = sorted(replacements, key=lambda r: r.confidence, reverse=True)
        candidates = candidates[: self.MAX_REPLACEMENTS]

        result = cv_tex
        applied: list[CVReplacement] = []

        for r in candidates:
            if not r.is_applicable():
                logger.debug(
                    "Skipping replacement (confidence=%.2f < 0.7): %r",
                    r.confidence, r.original_text[:50],
                )
                continue

            span = _locate(result, r.original_text)
            if span is None:
                logger.warning(
                    "original_text not found in CV — skipping: %r", r.original_text[:80]
                )
                continue

            if _has_new_latex_commands(span, r.replacement_text):
                logger.warning(
                    "Replacement introduces new LaTeX commands — skipping: %r",
                    r.replacement_text[:80],
                )
                continue

            result = result.replace(span, r.replacement_text, 1)
            applied.append(r)
            logger.info(
                "Applied replacement section=%s confidence=%.2f: %s",
                r.section, r.confidence, r.reason,
            )

        return result, applied
