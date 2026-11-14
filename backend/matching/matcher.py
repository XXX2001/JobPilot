from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.defaults import MIN_MATCH_SCORE
from backend.latex.artifacts import resolve_base_source
from backend.matching.cv_parser import latex_to_text
from backend.matching.filters import JobFilters
from backend.matching.gazetteer import GAZETTEER_VERSION, Gazetteer
from backend.matching.scorer import ScoreResult, cv_skills, score_job
from backend.models.schemas import JobDetails
from backend.models.user import SearchSettings, UserProfile

logger = logging.getLogger(__name__)


class JobMatcher:
    """Scores and ranks jobs against the owner's CV and filters (see ``scorer``).

    ``extra_terms`` are the owner's search keywords: they extend the gazetteer, so a
    profession the curated list does not cover still counts. Without a CV the skills
    named in ``filters.keywords`` stand in for it. ``cv_text`` may be LaTeX source.
    """

    def __init__(self, cv_text: str = "", extra_terms: Iterable[str] = ()) -> None:
        terms = sorted({" ".join(t.lower().split()) for t in extra_terms} - {""})
        self._gaz = Gazetteer.load(terms)
        cv_text = latex_to_text(cv_text)  # "R\\&D" is not the skill R
        self._cv = cv_skills(cv_text, self._gaz) if cv_text.strip() else None
        # What a stored score was computed from: a match with another basis is stale.
        digest = hashlib.sha256("\0".join([cv_text, *terms]).encode()).hexdigest()
        self.score_basis = f"{digest[:12]}:{GAZETTEER_VERSION}"

    def explain(self, job: JobDetails, filters: JobFilters) -> ScoreResult:
        cv = self._cv
        if cv is None:
            cv = frozenset(s for kw in filters.keywords for s in self._gaz.skills(kw))
        return score_job(job, cv, filters, self._gaz)

    def score(self, job: JobDetails, filters: JobFilters) -> float:
        """Return 0-100 relevance score; 0 when a hard filter rejects the job."""
        return self.explain(job, filters).score

    def rank_and_filter(
        self,
        jobs: list[JobDetails],
        filters: JobFilters,
        min_score: float = MIN_MATCH_SCORE,
    ) -> list[tuple[JobDetails, float]]:
        """Score all jobs, filter below threshold, return sorted by score desc."""
        scored = [(job, self.score(job, filters)) for job in jobs]
        filtered = [(j, s) for j, s in scored if s >= min_score]
        return sorted(filtered, key=lambda x: x[1], reverse=True)

    def matched_keywords(self, job: JobDetails, filters: JobFilters) -> list[str]:
        """The posting's skills the CV covers, then those it covers by family."""
        result = self.explain(job, filters)
        return result.covered + result.partial


@lru_cache(maxsize=4)
def _matcher(cv_text: str, keywords: tuple[str, ...]) -> JobMatcher:
    return JobMatcher(cv_text, keywords)


async def load_matcher(db: AsyncSession) -> JobMatcher:
    """The matcher for the owner's base CV and search keywords, for the monitor and the batch."""
    # Imported here: batch_runner imports the monitor store, which imports this module.
    from backend.scheduler.batch_runner import _extract_json_list  # noqa: PLC0415

    profile = (await db.execute(select(UserProfile).limit(1))).scalar_one_or_none()
    row = (await db.execute(select(SearchSettings).limit(1))).scalar_one_or_none()
    cv_path = resolve_base_source(
        getattr(profile, "base_cv_path", None), Path(settings.jobpilot_data_dir), "*.tex"
    )
    cv_text = ""
    if cv_path is not None:
        try:
            cv_text = cv_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            logger.warning("Base CV %s unreadable, matching on keywords only: %s", cv_path, exc)
    keywords = _extract_json_list(row.keywords, "include") if row is not None else []
    return _matcher(cv_text, tuple(keywords))
