"""CVModifier — whole-CV LLM call that returns surgical replacements."""
from __future__ import annotations

import logging
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


class CVModifier:
    """Single LLM call: full CV text + JobContext → CVModifierOutput (≤3 replacements)."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    async def modify(
        self,
        job: JobDetails,
        cv_tex: str,
        context: JobContext,
    ) -> CVModifierOutput:
        if len(cv_tex) > 50_000:
            logger.warning("CV text exceeds 50KB (%d chars), truncating", len(cv_tex))
            cv_tex = cv_tex[:50_000]
        context_md = context.to_markdown(job.title, job.company)
        prompt = CV_MODIFIER_SKILL.format(
            job_context_md=context_md,
            cv_tex=cv_tex,
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

        if len(cv_tex) > 50_000:
            logger.warning("CV text exceeds 50KB (%d chars), truncating", len(cv_tex))
            cv_tex = cv_tex[:50_000]

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
            cv_tex=cv_tex,
        )

        raw = await self._client.generate_json(prompt, CVModifierOutput)
        return CVModifierOutput(replacements=raw.top_three())
