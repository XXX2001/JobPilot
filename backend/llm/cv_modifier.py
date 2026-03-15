"""CVModifier — whole-CV LLM call that returns surgical replacements."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from backend.llm.gemini_client import GeminiClient

from backend.llm.job_context import JobContext

if TYPE_CHECKING:
    from backend.matching.fit_engine import FitAssessment
from backend.llm.prompts import CV_MODIFIER_SKILL
from backend.llm.validators import CVModifierOutput
from backend.models.schemas import JobDetails
from backend.security.sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# Regex to find \begin{document} — everything before it is preamble (packages,
# macros, font config) that the AI never modifies. Stripping it saves ~30-50%
# of input tokens on typical CVs.
_BEGIN_DOC_RE = re.compile(r"\\begin\{document\}", re.IGNORECASE)


def _strip_preamble(cv_tex: str) -> str:
    """Return only the document body (from \\begin{document} onward).

    The AI only edits content sections (Profile, Skills, etc.), never the
    preamble. Sending the preamble wastes tokens and can confuse the model.
    The returned text still starts with \\begin{document} so any original_text
    the AI produces will still be found in the full CV source.
    """
    m = _BEGIN_DOC_RE.search(cv_tex)
    if m:
        return cv_tex[m.start():]
    # No \begin{document} found — send the whole thing (unusual but safe)
    return cv_tex


class CVModifier:
    """Single LLM call: full CV text + JobContext → CVModifierOutput (≤3 replacements)."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    async def modify(
        self,
        job: JobDetails,
        cv_tex: str,
        context: JobContext,
        additional_context: str = "",
    ) -> CVModifierOutput:
        body = _strip_preamble(cv_tex)
        if len(body) > 50_000:
            logger.warning("CV body exceeds 50KB (%d chars), truncating", len(body))
            body = body[:50_000]
        context_md = sanitize_for_prompt(
            context.to_markdown(job.title, job.company), 10_000, "job_context"
        )
        clean_additional = sanitize_for_prompt(
            additional_context or "None provided.", 2000, "additional_context"
        )
        prompt = CV_MODIFIER_SKILL.format(
            job_context_md=context_md,
            cv_tex=body,
            additional_context=clean_additional,
        )
        raw = await self._client.generate_json(prompt, CVModifierOutput)
        # Enforce ≤3 cap and confidence threshold at the class boundary
        return CVModifierOutput(replacements=raw.top_three())

    async def modify_from_assessment(
        self,
        job: JobDetails,  # noqa: ARG002 — kept for API consistency with modify()
        cv_tex: str,
        assessment: FitAssessment,
    ) -> CVModifierOutput:
        """Targeted CV modification using FitAssessment gap analysis."""
        from backend.llm.prompts import CV_MODIFIER_FROM_ASSESSMENT

        body = _strip_preamble(cv_tex)
        if len(body) > 50_000:
            logger.warning("CV body exceeds 50KB (%d chars), truncating", len(body))
            body = body[:50_000]

        # Build gaps section
        gaps_lines = []
        for i, gap in enumerate(assessment.critical_gaps[:5], 1):
            match_info = (
                f"closest CV skill: \"{gap.best_cv_match}\" (similarity: {gap.similarity:.2f})"
                if gap.best_cv_match else "no match on CV"
            )
            gaps_lines.append(
                f"{i}. \"{gap.skill}\" (criticality: {gap.criticality:.1f}) — {match_info}"
            )
        gaps_section = "\n".join(gaps_lines) if gaps_lines else "No critical gaps identified."

        # Build covered section
        covered_section = "\n".join(
            f"- {s}" for s in assessment.covered_skills
        ) if assessment.covered_skills else "- (none identified)"

        prompt = CV_MODIFIER_FROM_ASSESSMENT.format(
            gaps_section=gaps_section,
            covered_section=covered_section,
            cv_tex=body,
        )

        raw = await self._client.generate_json(prompt, CVModifierOutput)
        return CVModifierOutput(replacements=raw.top_three())
