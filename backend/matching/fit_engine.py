# backend/matching/fit_engine.py
"""Fit Engine — ATS-simulated gap severity scoring and CV modification decision."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from backend.defaults import (
    GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
    GAP_SEVERITY_THRESHOLD_BALANCED,
    GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    SIMILARITY_FULL_MATCH,
    SIMILARITY_PARTIAL_MATCH,
)
from backend.matching.cv_parser import CVProfile
from backend.matching.job_skill_extractor import JobProfile, JobSkill

logger = logging.getLogger(__name__)

THRESHOLDS = {
    "conservative": GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    "balanced": GAP_SEVERITY_THRESHOLD_BALANCED,
    "aggressive": GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
}


@dataclass
class SkillGap:
    skill: str
    criticality: float
    best_cv_match: str
    similarity: float


@dataclass
class FitAssessment:
    severity: float
    should_modify: bool
    simulated_ats_score: float
    covered_skills: list[str] = field(default_factory=list)
    partial_matches: list[str] = field(default_factory=list)
    critical_gaps: list[SkillGap] = field(default_factory=list)
    preferred_gaps: list[SkillGap] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for JSON storage in DB."""
        return {
            "severity": self.severity,
            "should_modify": self.should_modify,
            "simulated_ats_score": self.simulated_ats_score,
            "covered_skills": self.covered_skills,
            "partial_matches": self.partial_matches,
            "critical_gaps": [
                {"skill": g.skill, "criticality": g.criticality,
                 "best_cv_match": g.best_cv_match, "similarity": g.similarity}
                for g in self.critical_gaps
            ],
            "preferred_gaps": [
                {"skill": g.skill, "criticality": g.criticality,
                 "best_cv_match": g.best_cv_match, "similarity": g.similarity}
                for g in self.preferred_gaps
            ],
        }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Returns 0.0 for zero vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class FitEngine:
    """Computes gap severity and decides whether CV modification is needed."""

    def assess(
        self,
        job_profile: JobProfile,
        cv_profile: CVProfile,
        sensitivity: str = "balanced",
    ) -> FitAssessment:
        """Compute gap severity and return a FitAssessment."""
        if not job_profile.skills:
            return FitAssessment(
                severity=0.0,
                should_modify=False,
                simulated_ats_score=100.0,
            )

        covered: list[str] = []
        partial: list[str] = []
        critical_gaps: list[SkillGap] = []
        preferred_gaps: list[SkillGap] = []

        total_weight = 0.0
        weighted_gaps = 0.0

        for job_skill in job_profile.skills:
            coverage, best_match_text, best_sim = self._best_match(
                job_skill, cv_profile
            )
            gap = 1.0 - coverage
            weighted_gaps += gap * job_skill.criticality
            total_weight += job_skill.criticality

            if coverage >= 1.0:
                covered.append(job_skill.text)
            elif coverage >= 0.5:
                partial.append(f"{job_skill.text} ~ {best_match_text}")
            else:
                gap_entry = SkillGap(
                    skill=job_skill.text,
                    criticality=job_skill.criticality,
                    best_cv_match=best_match_text,
                    similarity=best_sim,
                )
                if job_skill.section == "preferred":
                    preferred_gaps.append(gap_entry)
                else:
                    critical_gaps.append(gap_entry)

        severity = weighted_gaps / total_weight if total_weight > 0 else 0.0
        threshold = THRESHOLDS.get(sensitivity, THRESHOLDS["balanced"])
        should_modify = severity >= threshold
        ats_score = (1.0 - severity) * 100

        # Sort gaps by criticality descending
        critical_gaps.sort(key=lambda g: g.criticality, reverse=True)
        preferred_gaps.sort(key=lambda g: g.criticality, reverse=True)

        return FitAssessment(
            severity=severity,
            should_modify=should_modify,
            simulated_ats_score=ats_score,
            covered_skills=covered,
            partial_matches=partial,
            critical_gaps=critical_gaps,
            preferred_gaps=preferred_gaps,
        )

    def _best_match(
        self, job_skill: JobSkill, cv_profile: CVProfile
    ) -> tuple[float, str, float]:
        """Find the best CV skill match for a job skill.

        Similarity detects whether skills match; the CV skill's context weight
        scales the resulting coverage score (high-weight context = stronger signal).

        Returns: (coverage_score, best_match_text, raw_similarity)
        """
        best_sim = 0.0
        best_weight = 0.0
        best_text = ""

        for cv_skill in cv_profile.skills:
            sim = cosine_similarity(job_skill.embedding, cv_skill.embedding)
            if sim > best_sim or (sim == best_sim and cv_skill.weight > best_weight):
                best_sim = sim
                best_weight = cv_skill.weight
                best_text = cv_skill.text

        if best_sim >= SIMILARITY_FULL_MATCH:
            return 1.0, best_text, best_sim
        elif best_sim >= SIMILARITY_PARTIAL_MATCH:
            # Partial match — weight moderates coverage quality
            coverage = 0.5 + 0.5 * best_weight
            return coverage, best_text, best_sim
        else:
            return 0.0, best_text, best_sim
