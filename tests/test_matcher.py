from __future__ import annotations
from datetime import datetime, timezone

from backend.matching.matcher import JobMatcher
from backend.matching.filters import JobFilters
from backend.models.schemas import JobDetails


def _make_job(
    title: str = "ML Engineer",
    company: str = "ACME",
    location: str = "Paris",
    description: str = "python machine learning data pipeline",
    salary_min: int | None = None,
    salary_max: int | None = None,
    posted_date: datetime | None = None,
) -> JobDetails:
    return JobDetails(
        title=title,
        company=company,
        location=location,
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        posted_date=posted_date or datetime.now(timezone.utc),
        url="http://example.com",
    )


def test_perfect_match_scores_high():
    """A job matching all keywords and location scores >= 80."""
    matcher = JobMatcher()
    filters = JobFilters(
        keywords=["python", "machine learning"],
        locations=["Paris"],
    )
    job = _make_job(description="python machine learning engineer role in Paris")
    score = matcher.score(job, filters)
    assert score >= 80.0


def test_excluded_term_returns_zero():
    """A job containing an excluded keyword scores 0."""
    matcher = JobMatcher()
    filters = JobFilters(
        keywords=["python"],
        excluded_keywords=["10+ years", "clearance"],
    )
    job = _make_job(description="python developer, requires 10+ years experience and clearance")
    score = matcher.score(job, filters)
    assert score == 0.0


def test_blacklisted_company_returns_zero():
    """A job from a blacklisted company scores 0."""
    matcher = JobMatcher()
    filters = JobFilters(
        keywords=["python"],
        excluded_companies=["BadCorp"],
    )
    job = _make_job(company="BadCorp", description="python developer role")
    score = matcher.score(job, filters)
    assert score == 0.0


def test_matched_keywords_returns_subset_found_in_description():
    """matched_keywords returns only the include-keywords present in the description."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["python", "django", "kubernetes"])
    job = _make_job(description="senior python developer working with django")
    assert matcher.matched_keywords(job, filters) == ["python", "django"]


def test_matched_keywords_is_case_insensitive_and_preserves_casing():
    """Matching ignores case but the returned keywords keep their original casing."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["Python", "FastAPI"])
    job = _make_job(description="we use python and fastapi in production")
    assert matcher.matched_keywords(job, filters) == ["Python", "FastAPI"]


def test_matched_keywords_empty_when_no_keywords_configured():
    """No configured keywords → empty list (not a full match)."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=[])
    job = _make_job(description="python machine learning")
    assert matcher.matched_keywords(job, filters) == []


def test_rank_and_filter_sorted_descending():
    """rank_and_filter returns jobs sorted by score descending, below threshold excluded."""
    matcher = JobMatcher()
    filters = JobFilters(
        keywords=["python", "machine learning"],
        locations=["Paris"],
        excluded_companies=["SkipMe"],
    )
    job_good = _make_job(
        title="ML Engineer", description="python machine learning engineer, Paris based"
    )
    job_weak = _make_job(
        title="Java Developer",
        company="OtherCo",
        location="London",
        description="java spring boot microservices",
    )
    job_blacklisted = _make_job(company="SkipMe", description="python machine learning")

    results = matcher.rank_and_filter(
        [job_good, job_weak, job_blacklisted],
        filters,
        min_score=30.0,
    )

    scores = [s for _, s in results]
    companies = [j.company for j, _ in results]

    # Blacklisted company must not appear
    assert "SkipMe" not in companies
    # Scores must be in descending order
    assert scores == sorted(scores, reverse=True)
    # Best job should be first
    assert results[0][0].title == "ML Engineer"
