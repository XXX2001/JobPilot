# backend/matching/embedder.py
"""Embedder — batch embedding of CV and job profiles via Gemini."""
from __future__ import annotations

import logging

from backend.matching.cv_parser import CVProfile
from backend.matching.job_skill_extractor import JobProfile

logger = logging.getLogger(__name__)


class Embedder:
    """Wraps GeminiClient.embed() with profile-level batch operations."""

    def __init__(self, gemini_client) -> None:
        self._client = gemini_client

    async def embed_cv_profile(self, profile: CVProfile) -> CVProfile:
        """Embed all skills in a CVProfile that don't already have embeddings."""
        to_embed: list[tuple[int, str]] = []
        for i, skill in enumerate(profile.skills):
            if not skill.embedding:
                to_embed.append((i, skill.text))

        if not to_embed:
            return profile

        texts = [text for _, text in to_embed]
        vectors = await self._client.embed(texts)

        for (idx, _), vector in zip(to_embed, vectors):
            profile.skills[idx].embedding = vector

        logger.info("Embedded %d CV skills (%d already cached)", len(to_embed),
                     len(profile.skills) - len(to_embed))
        return profile

    async def embed_job_profile(self, profile: JobProfile) -> JobProfile:
        """Embed all skills in a JobProfile."""
        to_embed: list[tuple[int, str]] = []
        for i, skill in enumerate(profile.skills):
            if not skill.embedding:
                to_embed.append((i, skill.text))

        if not to_embed:
            return profile

        texts = [text for _, text in to_embed]
        vectors = await self._client.embed(texts)

        for (idx, _), vector in zip(to_embed, vectors):
            profile.skills[idx].embedding = vector

        logger.info("Embedded %d job skills", len(to_embed))
        return profile
