# tests/test_fit_engine.py
"""Tests for the FitEngine — gap severity algorithm and modification decision."""
from __future__ import annotations

import pytest

from backend.matching.fit_engine import (
    FitEngine,
    FitAssessment,
    SkillGap,
    cosine_similarity,
)
from backend.matching.cv_parser import SkillEntry, CVProfile
from backend.matching.job_skill_extractor import JobSkill, JobProfile


def test_cosine_similarity_identical():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert cosine_similarity(a, b) == 0.0


def test_perfect_fit_low_severity():
    """When all job skills are covered by CV, severity should be near 0."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="skills_section", weight=0.6,
                       embedding=[1.0, 0.0, 0.0]),
            SkillEntry(text="Docker", context="experience_recent", weight=1.0,
                       embedding=[0.0, 1.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Docker", criticality=0.8, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity < 0.1
    assert assessment.should_modify is False
    assert assessment.simulated_ats_score > 90


def test_complete_gap_high_severity():
    """When no job skills match CV, severity should be near 1.0."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Java", context="skills_section", weight=0.6,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[0.0, 1.0, 0.0]),
        JobSkill(text="Docker", criticality=0.9, section="required",
                 embedding=[0.0, 0.0, 1.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity > 0.8
    assert assessment.should_modify is True
    assert len(assessment.critical_gaps) == 2


def test_partial_match_medium_severity():
    """One critical skill missing, one present — medium severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Docker", criticality=1.0, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert 0.3 < assessment.severity < 0.7
    assert len(assessment.critical_gaps) == 1
    assert assessment.critical_gaps[0].skill == "Docker"


def test_preferred_gap_low_severity():
    """Missing only preferred skills should produce low severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="Jira", criticality=0.3, section="preferred",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity < 0.3
    assert assessment.should_modify is False


def test_sensitivity_conservative_modifies_more():
    """Conservative threshold should trigger modification at lower severity."""
    cv = CVProfile(
        skills=[
            SkillEntry(text="Python", context="experience_recent", weight=1.0,
                       embedding=[1.0, 0.0, 0.0]),
        ],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[
        JobSkill(text="Python", criticality=1.0, section="required",
                 embedding=[1.0, 0.0, 0.0]),
        JobSkill(text="AWS", criticality=0.8, section="required",
                 embedding=[0.0, 1.0, 0.0]),
    ])

    engine = FitEngine()
    conservative = engine.assess(job, cv, sensitivity="conservative")
    balanced = engine.assess(job, cv, sensitivity="balanced")
    aggressive = engine.assess(job, cv, sensitivity="aggressive")

    assert conservative.severity == balanced.severity == aggressive.severity
    assert conservative.should_modify is True
    assert aggressive.should_modify is False


def test_empty_job_skills():
    cv = CVProfile(
        skills=[SkillEntry(text="Python", context="skills_section", weight=0.6, embedding=[1.0])],
        raw_text_hash="abc",
    )
    job = JobProfile(skills=[])
    engine = FitEngine()
    assessment = engine.assess(job, cv)
    assert assessment.severity == 0.0
    assert assessment.should_modify is False
