"""Tests for job description NLP skill extraction."""
from __future__ import annotations

from backend.matching.job_skill_extractor import JobProfile, JobSkillExtractor

JOB_DESCRIPTION = """
About us:
We are a fast-growing fintech startup building the future of payments.

Requirements:
- 3+ years of experience with Python
- Strong knowledge of SQL and PostgreSQL
- Experience with Docker and Kubernetes
- Must have excellent problem-solving skills

Nice to have:
- Familiarity with Terraform
- Exposure to Apache Kafka
- AWS certification is a plus

Benefits:
- Competitive salary
- Remote work options
"""


def test_extractor_finds_required_skills():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    skill_texts = [s.text.lower() for s in profile.skills]
    assert "python" in skill_texts
    assert "sql" in skill_texts or "postgresql" in skill_texts


def test_extractor_assigns_high_criticality_to_required():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    required_skills = [s for s in profile.skills if s.section == "required"]
    assert len(required_skills) > 0
    assert all(s.criticality >= 0.5 for s in required_skills)


def test_extractor_assigns_low_criticality_to_preferred():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    preferred_skills = [s for s in profile.skills if s.section == "preferred"]
    assert len(preferred_skills) > 0
    assert all(s.criticality <= 0.5 for s in preferred_skills)


def test_extractor_detects_knockout_filters():
    extractor = JobSkillExtractor()
    profile = extractor.extract(JOB_DESCRIPTION)
    assert any("3" in k and "year" in k.lower() for k in profile.knockout_filters)


def test_extractor_handles_empty_description():
    extractor = JobSkillExtractor()
    profile = extractor.extract("")
    assert isinstance(profile, JobProfile)
    assert len(profile.skills) == 0


def test_extractor_handles_no_sections():
    desc = "We need someone who knows Python, Docker, and AWS. Terraform is a bonus."
    extractor = JobSkillExtractor()
    profile = extractor.extract(desc)
    assert len(profile.skills) >= 2
