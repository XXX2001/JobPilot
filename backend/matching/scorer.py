# backend/matching/scorer.py
"""Score a posting against the CV's skills: hard filters, coverage, title factor.

    score = 100 * coverage * (0.4 + 0.6 * T)

Coverage is the criticality-weighted share of the posting's skills the CV covers, the
required tier and the rest scored separately. T is the title factor. A posting that
fails a hard filter scores 0 and is flagged, never down-weighted. No recency, no
embeddings, no LLM, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from backend.matching.filters import JobFilters
from backend.matching.gazetteer import Gazetteer, fold
from backend.matching.job_skills import REQUIRED, extract_job_skills
from backend.models.schemas import JobDetails

REQUIRED_SHARE = 0.65  # coverage = 0.65 * cov(required) + 0.35 * cov(other)
SHRINK = 0.5  # pseudo-weight of unknown skills: a one-skill posting cannot reach 100
FAMILY_CREDIT = 0.5  # the CV has a skill of the same family (Django for FastAPI)
TITLE_WEIGHT = 0.6
TITLE_MATCH = 1.0
TITLE_NEUTRAL = 0.6
TITLE_NON_TECHNICAL = 0.1
PROVISIONAL_MIN_CHARS = 80  # a shorter description is a card, not a posting
PROVISIONAL_KEYWORD_SCORE = 70.0
PROVISIONAL_CV_SKILL_SCORE = 45.0

# Both run on accent-folded, lowercased text.
_NON_TECHNICAL = re.compile(
    r"\b(?:sales|account (?:executive|manager)|commercial|business developer|marketing"
    r"|comptab\w*|accountant|legal|juriste|counsel|compliance|aml|recruit\w*|talent"
    r"|customer (?:care|success|support)|biostatisti\w*|analyst|chef de projet)\b"
)
# ponytail: a marker anywhere in the description counts, so "no remote work" passes the
# remote filter. Parse the workplace type per source if that shows up in practice.
_REMOTE = re.compile(
    r"\b(?:remote|teletravail|home[- ]?office|work(?:ing)? from home|a distance|distanciel)\b"
)


@dataclass(frozen=True)
class ScoreResult:
    score: float  # 0-100
    passed_filters: bool
    reject_reason: str | None  # "location" | "excluded_keyword" | "excluded_company" | None
    covered: list[str]
    partial: list[str]  # same-family credit
    missing: list[str]  # sorted by criticality desc
    provisional: bool  # no description: title-only score


@lru_cache(maxsize=16)
def cv_skills(cv_text: str, gaz: Gazetteer) -> frozenset[str]:
    """Canonical skills named in the CV, cached per (CV text, gazetteer)."""
    return frozenset(gaz.skills(cv_text))


@lru_cache(maxsize=1024)
def _phrase(phrase: str) -> re.Pattern[str] | None:
    """Whole-word pattern for a phrase, to run on folded text. None for a blank phrase."""
    words = fold(phrase).split()
    if not words:
        return None
    # Not \b: a phrase may start or end with a non-word character ("c++", ".net").
    return re.compile(r"(?<![^\W_])" + r"\s+".join(map(re.escape, words)) + r"(?![^\W_])")


def _has_any(phrases: list[str], folded_text: str) -> bool:
    return any((p := _phrase(phrase)) is not None and p.search(folded_text) for phrase in phrases)


def _reject_reason(job: JobDetails, filters: JobFilters) -> str | None:
    if _has_any(filters.excluded_keywords, fold(job.title)):
        return "excluded_keyword"
    company = fold(job.company).strip()
    if any(company == fold(c).strip() for c in filters.excluded_companies):
        return "excluded_company"
    location = fold(job.location).strip()
    if filters.remote_only:
        # The configured cities do not apply to a remote search ("Remote, France").
        if not _REMOTE.search(location) and not _REMOTE.search(fold(job.description)):
            return "location"
    elif filters.locations and location and not _has_any(filters.locations, location):
        return "location"
    return None


def _coverage(weights: dict[str, float], credit: dict[str, float]) -> float:
    def cov(skills: list[str]) -> float:
        if not skills:
            return 0.0
        total = sum(weights[s] for s in skills)
        return sum(weights[s] * credit[s] for s in skills) / (total + SHRINK)

    required = [s for s, w in weights.items() if w >= REQUIRED]
    other = [s for s, w in weights.items() if w < REQUIRED]
    if not required:
        return cov(other)
    return REQUIRED_SHARE * cov(required) + (1 - REQUIRED_SHARE) * cov(other)


def score_job(
    job: JobDetails, cv: frozenset[str], filters: JobFilters, gaz: Gazetteer
) -> ScoreResult:
    """Score one posting against the CV's skills (see :func:`cv_skills`)."""
    reason = _reject_reason(job, filters)
    if reason is not None:
        return ScoreResult(0.0, False, reason, [], [], [], False)

    provisional = len(job.description.strip()) < PROVISIONAL_MIN_CHARS
    weights = extract_job_skills(job.title, "" if provisional else job.description, gaz)
    credit = {
        skill: 1.0
        if skill in cv
        else FAMILY_CREDIT
        if any(gaz.related(skill, have) for have in cv)
        else 0.0
        for skill in weights
    }
    covered = sorted(s for s, c in credit.items() if c == 1.0)
    partial = sorted(s for s, c in credit.items() if c == FAMILY_CREDIT)
    missing = sorted((s for s, c in credit.items() if c == 0.0), key=lambda s: (-weights[s], s))

    title = fold(job.title)
    keyword_in_title = _has_any(filters.keywords, title)
    cv_skill_in_title = not cv.isdisjoint(gaz.skills(job.title))
    if provisional:
        if keyword_in_title:
            score = PROVISIONAL_KEYWORD_SCORE
        elif cv_skill_in_title:
            score = PROVISIONAL_CV_SKILL_SCORE
        else:
            score = 0.0
    else:
        # A non-technical title beats a CV skill named in it ("Sales Engineer, Python
        # products") but not one of the owner's own search keywords.
        if keyword_in_title:
            factor = TITLE_MATCH
        elif _NON_TECHNICAL.search(title):
            factor = TITLE_NON_TECHNICAL
        elif cv_skill_in_title:
            factor = TITLE_MATCH
        else:
            factor = TITLE_NEUTRAL
        score = 100 * _coverage(weights, credit) * ((1 - TITLE_WEIGHT) + TITLE_WEIGHT * factor)
    return ScoreResult(round(score, 2), True, None, covered, partial, missing, provisional)
