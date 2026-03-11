# tests/test_embedder.py
"""Tests for the Embedder — CV profile and job profile embedding."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.matching.embedder import Embedder
from backend.matching.cv_parser import CVProfile, SkillEntry
from backend.matching.job_skill_extractor import JobProfile, JobSkill


def _mock_gemini_client(dim: int = 3) -> MagicMock:
    client = MagicMock()

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[float(hash(t) % 100) / 100.0] * dim for t in texts]

    client.embed = fake_embed
    return client


@pytest.mark.asyncio
async def test_embed_cv_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6, embedding=[]),
            SkillEntry(text="Docker", context="experience_recent", weight=1.0, embedding=[]),
        ],
        raw_text_hash="abc123",
    )

    result = await embedder.embed_cv_profile(profile)
    assert all(len(s.embedding) == 3 for s in result.skills)
    assert result.raw_text_hash == "abc123"


@pytest.mark.asyncio
async def test_embed_job_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = JobProfile(
        skills=[
            JobSkill(text="Python", criticality=1.0, section="required", embedding=[]),
            JobSkill(text="AWS", criticality=0.8, section="required", embedding=[]),
        ],
    )

    result = await embedder.embed_job_profile(profile)
    assert all(len(s.embedding) == 3 for s in result.skills)


@pytest.mark.asyncio
async def test_embed_skips_already_embedded():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6,
                       embedding=[1.0, 2.0, 3.0]),  # Already embedded
        ],
        raw_text_hash="abc123",
    )

    result = await embedder.embed_cv_profile(profile)
    assert result.skills[0].embedding == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_embed_empty_profile():
    client = _mock_gemini_client()
    embedder = Embedder(client)

    profile = CVProfile(skills=[], raw_text_hash="abc123")
    result = await embedder.embed_cv_profile(profile)
    assert result.skills == []
