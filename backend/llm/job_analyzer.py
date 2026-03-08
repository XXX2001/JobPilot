"""JobAnalyzer — converts a raw JobDetails into a structured JobContext."""
from __future__ import annotations

import logging

from backend.llm.gemini_client import GeminiClient
from backend.llm.job_context import JobContext
from backend.llm.prompts import JOB_ANALYZER_PROMPT
from backend.models.schemas import JobDetails

logger = logging.getLogger(__name__)


class JobAnalyzer:
    """Single LLM call: job description → structured JobContext."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self._client = client or GeminiClient()

    async def analyze(self, job: JobDetails) -> JobContext:
        prompt = JOB_ANALYZER_PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description=job.description[:2000],
        )
        return await self._client.generate_json(prompt, JobContext)
