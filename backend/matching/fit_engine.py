# backend/matching/fit_engine.py
"""Fit Engine: gap severity from gazetteer coverage, and the CV modification decision."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from backend.defaults import (
    GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
    GAP_SEVERITY_THRESHOLD_BALANCED,
    GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
)
from backend.matching.cv_parser import CVProfile
from backend.matching.gazetteer import Gazetteer
from backend.matching.job_skills import NICE
from backend.matching.scorer import FAMILY_CREDIT, coverage, skill_credit

THRESHOLDS = {
    "conservative": GAP_SEVERITY_THRESHOLD_CONSERVATIVE,
    "balanced": GAP_SEVERITY_THRESHOLD_BALANCED,
    "aggressive": GAP_SEVERITY_THRESHOLD_AGGRESSIVE,
}


@dataclass
class SkillGap:
    skill: str  # canonical gazetteer name, never posting text
    criticality: float


@dataclass
class FitAssessment:
    severity: float  # 1 - coverage
    should_modify: bool
    simulated_ats_score: float  # 100 * coverage
    covered_skills: list[str] = field(default_factory=list)  # on the CV, then same-family
    partial_matches: list[str] = field(default_factory=list)  # the same-family ones
    critical_gaps: list[SkillGap] = field(default_factory=list)
    preferred_gaps: list[SkillGap] = field(default_factory=list)  # nice-to-have only

    def to_dict(self) -> dict:
        """Serialize for JSON storage in DB."""
        return asdict(self)


class FitEngine:
    """Computes gap severity and decides whether CV modification is needed."""

    def assess(
        self,
        job_skills: dict[str, float],
        cv_profile: CVProfile,
        gaz: Gazetteer,
        sensitivity: str = "balanced",
    ) -> FitAssessment:
        """``job_skills``: canonical skill -> criticality (see ``extract_job_skills``)."""
        if not job_skills:
            # Nothing recognised in the posting: no gap to close, and no evidence of a fit.
            return FitAssessment(severity=1.0, should_modify=False, simulated_ats_score=0.0)

        cv = frozenset(s.text for s in cv_profile.skills)
        credit = skill_credit(job_skills, cv, gaz)
        covered_share = coverage(job_skills, credit)
        partial = sorted(s for s, c in credit.items() if c == FAMILY_CREDIT)
        gaps = [
            SkillGap(skill=s, criticality=job_skills[s])
            for s in sorted(job_skills, key=lambda s: (-job_skills[s], s))
            if credit[s] == 0.0
        ]
        severity = 1.0 - covered_share
        return FitAssessment(
            severity=severity,
            should_modify=severity >= THRESHOLDS.get(sensitivity, THRESHOLDS["balanced"]),
            simulated_ats_score=100 * covered_share,
            covered_skills=sorted(s for s, c in credit.items() if c == 1.0) + partial,
            partial_matches=partial,
            critical_gaps=[g for g in gaps if g.criticality > NICE],
            preferred_gaps=[g for g in gaps if g.criticality <= NICE],
        )
