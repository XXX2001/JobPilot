from __future__ import annotations
import logging
import re
from datetime import datetime, timezone

from backend.models.schemas import JobDetails
from backend.matching.filters import JobFilters

logger = logging.getLogger(__name__)


class JobMatcher:
    """Scores and ranks jobs against user profile and filters."""

    def score(self, job: JobDetails, filters: JobFilters) -> float:
        """Return 0-100 relevance score.

        Scoring weights:
        - Keyword match (simple overlap on description): 40%
        - Location match: 20%
        - Experience level match: 15%
        - Salary match: 10%
        - Recency (newer = better): 10%
        - Exclusion penalty (blacklisted terms or company): 0 (auto-skip)
        """
        # Exclusion check — instant disqualify
        if self._has_excluded_terms(job, filters):
            return 0.0

        # Company blacklist
        if job.company.lower() in [c.lower() for c in filters.excluded_companies]:
            return 0.0

        score = 0.0

        # Keyword matching (simple overlap): 40%
        score += self._keyword_match(job.description, filters.keywords) * 40

        # Location match: 20%
        score += self._location_match(job.location, filters) * 20

        # Experience level match: 15%
        score += self._experience_match(job, filters) * 15

        # Salary match: 10%
        score += self._salary_match(job, filters) * 10

        # Recency: 10%
        posted = job.posted_date or job.posted_at
        score += self._recency_score(posted) * 10

        return min(100.0, score)

    def rank_and_filter(
        self,
        jobs: list[JobDetails],
        filters: JobFilters,
        min_score: float = 30.0,
    ) -> list[tuple[JobDetails, float]]:
        """Score all jobs, filter below threshold, return sorted by score desc."""
        scored = [(job, self.score(job, filters)) for job in jobs]
        filtered = [(j, s) for j, s in scored if s >= min_score]
        return sorted(filtered, key=lambda x: x[1], reverse=True)

    # --- Private helpers ---

    def _keyword_match(self, description: str, keywords: list[str]) -> float:
        """Simple keyword overlap ratio: matched / total keywords."""
        if not keywords:
            return 1.0
        text = description.lower()
        matched = sum(1 for kw in keywords if kw.lower() in text)
        return matched / len(keywords)

    def _has_excluded_terms(self, job: JobDetails, filters: JobFilters) -> bool:
        """Return True if any excluded keyword appears in title or description."""
        text = f"{job.title} {job.description}".lower()
        return any(term.lower() in text for term in filters.excluded_keywords)

    def _location_match(self, location: str, filters: JobFilters) -> float:
        """1.0 if remote_only + remote in location, or location in filter list."""
        if not location:
            return 0.5  # unknown location — partial credit
        loc_lower = location.lower()
        if filters.remote_only:
            return 1.0 if "remote" in loc_lower else 0.0
        if not filters.locations:
            return 1.0  # no location filter — always match
        return 1.0 if any(fl.lower() in loc_lower for fl in filters.locations) else 0.3

    def _experience_match(self, job: JobDetails, filters: JobFilters) -> float:
        """Crude heuristic: look for year ranges in description."""
        if not filters.experience_range:
            return 1.0
        min_exp, max_exp = filters.experience_range
        text = job.description.lower()
        # Look for patterns like "5+ years", "3 years", "2-4 years"
        mentions = re.findall(r"(\d+)\+?\s*year", text)
        if not mentions:
            return 0.8  # no info — mostly match
        avg_required = sum(int(m) for m in mentions) / len(mentions)
        if min_exp <= avg_required <= max_exp + 2:
            return 1.0
        if avg_required > max_exp + 2:
            return 0.2  # overqualified / senior role
        return 0.6

    def _salary_match(self, job: JobDetails, filters: JobFilters) -> float:
        """1.0 if salary_min not set or job salary exceeds it."""
        if not filters.salary_min:
            return 1.0
        if job.salary_max and job.salary_max >= filters.salary_min:
            return 1.0
        if job.salary_min and job.salary_min >= filters.salary_min:
            return 1.0
        if not job.salary_min and not job.salary_max:
            return 0.7  # unknown — partial credit
        return 0.2

    def _recency_score(self, posted: datetime | None) -> float:
        """1.0 for today, linear decay to 0 at 30 days."""
        if posted is None:
            return 0.5
        now = datetime.now(timezone.utc)
        # Normalize posted to UTC if naive
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        age_days = (now - posted).total_seconds() / 86400
        if age_days <= 0:
            return 1.0
        return max(0.0, 1.0 - age_days / 30.0)
