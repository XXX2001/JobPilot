"""CVModifier — whole-CV LLM call that returns surgical replacements."""
from __future__ import annotations

import logging

from backend.llm.gemini_client import GeminiClient
from backend.llm.job_context import JobContext
from backend.llm.prompts import CV_MODIFIER_SKILL
from backend.llm.validators import CVModifierOutput
from backend.models.schemas import JobDetails

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
        context_md = context.to_markdown(job.title, job.company)
        prompt = CV_MODIFIER_SKILL.format(
            job_context_md=context_md,
            cv_tex=cv_tex,
        )
        return await self._client.generate_json(prompt, CVModifierOutput)
