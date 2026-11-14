"""JobMatcher: the public matching interface, delegating to the gazetteer scorer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.database import AsyncSessionLocal
from backend.matching import matcher as matcher_module
from backend.matching.filters import JobFilters
from backend.matching.gazetteer import GAZETTEER_VERSION
from backend.matching.matcher import JobMatcher, load_matcher
from backend.matching.scorer import ScoreResult
from backend.models.schemas import JobDetails
from backend.models.user import SearchSettings, UserProfile

# Long enough for the description to count as a posting, names no skill.
BODY = "\nWe are a small team shipping to industrial customers every week, from our office."


@pytest.fixture(autouse=True)
def _no_stray_cv(monkeypatch, tmp_path):
    """Other tests leave ``templates/*.tex`` in the shared data dir: ``load_matcher``
    would read one as the owner's CV."""
    monkeypatch.setattr(
        matcher_module, "settings", SimpleNamespace(jobpilot_data_dir=str(tmp_path))
    )


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
        description=description + BODY,
        salary_min=salary_min,
        salary_max=salary_max,
        posted_date=posted_date or datetime.now(timezone.utc),
        url="http://example.com",
    )


def test_perfect_match_scores_high():
    """A job requiring exactly the owner's skills, in the right city, scores >= 80."""
    matcher = JobMatcher()
    filters = JobFilters(
        keywords=["python", "machine learning"],
        locations=["Paris"],
    )
    job = _make_job(description="Requirements\n- Python\n- Machine learning\n- Paris based\n")
    score = matcher.score(job, filters)
    assert score >= 80.0


def test_excluded_term_in_title_returns_zero():
    """A job whose title contains an excluded keyword scores 0."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["python"], excluded_keywords=["intern", "clearance"])
    job = _make_job(title="Python Intern", description="python developer")
    assert matcher.score(job, filters) == 0.0
    assert matcher.explain(job, filters).reject_reason == "excluded_keyword"


def test_excluded_term_matches_whole_words_only():
    """ "intern" does not exclude an "International" title (it did, as a substring)."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["python"], excluded_keywords=["intern"])
    job = _make_job(title="Python Developer, International Team", description="python developer")
    assert matcher.score(job, filters) > 0.0


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
    """matched_keywords returns only the owner's skills present in the posting."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["python", "django", "kubernetes"])
    job = _make_job(
        title="Backend Engineer", description="senior python developer working with django"
    )
    assert matcher.matched_keywords(job, filters) == ["django", "python"]


def test_matched_keywords_is_case_insensitive_and_returns_canonical_names():
    """Matching ignores case on both sides; the result names the canonical skills."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["Python", "FastAPI"])
    job = _make_job(title="Backend Engineer", description="we use PYTHON and fastapi daily")
    assert matcher.matched_keywords(job, filters) == ["fastapi", "python"]


def test_matched_keywords_needs_whole_words():
    """ "java" is not found in "javascript", nor "go" in "going"."""
    matcher = JobMatcher()
    filters = JobFilters(keywords=["java", "go"])
    job = _make_job(title="Frontend Engineer", description="javascript, and going fast")
    assert matcher.matched_keywords(job, filters) == []


def test_matched_keywords_empty_when_no_keywords_configured():
    """No configured keywords and no CV → empty list (not a full match)."""
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
    job_partial = _make_job(
        title="Data Engineer", company="ThirdCo", description="python, spark, airflow, kafka"
    )
    job_weak = _make_job(
        title="Java Developer",
        company="OtherCo",
        location="London",
        description="java spring boot microservices",
    )
    job_blacklisted = _make_job(company="SkipMe", description="python machine learning")

    results = matcher.rank_and_filter(
        [job_partial, job_good, job_weak, job_blacklisted],
        filters,
        min_score=30.0,
    )

    scores = [s for _, s in results]
    companies = [j.company for j, _ in results]

    # Blacklisted company and wrong city must not appear
    assert "SkipMe" not in companies
    assert "OtherCo" not in companies
    # Scores must be in descending order
    assert scores == sorted(scores, reverse=True)
    # Best job should be first
    assert results[0][0].title == "ML Engineer"


def test_explain_returns_the_score_result():
    matcher = JobMatcher()
    filters = JobFilters(keywords=["python"], locations=["Paris"])
    result = matcher.explain(_make_job(description="Requirements\n- Python\n- Rust\n"), filters)
    assert isinstance(result, ScoreResult)
    assert result.passed_filters and result.score == matcher.score(
        _make_job(description="Requirements\n- Python\n- Rust\n"), filters
    )
    assert "python" in result.covered and "rust" in result.missing
    rejected = matcher.explain(_make_job(location="Lyon"), filters)
    assert (rejected.score, rejected.passed_filters, rejected.reject_reason) == (
        0.0,
        False,
        "location",
    )


def test_cv_text_is_the_skill_set_and_family_credit_is_listed():
    """With a CV, the keywords are no longer the skill set; a same-family skill counts."""
    matcher = JobMatcher(cv_text="Python, Django, PostgreSQL")
    filters = JobFilters(keywords=["kubernetes"])
    job = _make_job(
        title="Backend Engineer", description="Requirements\n- Python\n- FastAPI\n- Kubernetes\n"
    )
    result = matcher.explain(job, filters)
    assert (result.covered, result.partial, result.missing) == (
        ["python"],
        ["fastapi"],
        ["kubernetes"],
    )
    assert matcher.matched_keywords(job, filters) == ["python", "fastapi"]


def test_extra_terms_make_an_uncovered_profession_count():
    """The owner's own keywords are skills even when the curated list has none of them."""
    filters = JobFilters(keywords=["underwater welding"])
    job = _make_job(title="Welder", description="Requirements\n- Underwater welding\n")
    assert JobMatcher().matched_keywords(job, filters) == []
    matcher = JobMatcher(extra_terms=filters.keywords)
    assert matcher.matched_keywords(job, filters) == ["underwater welding"]


def test_score_basis_names_the_cv_and_the_gazetteer():
    basis = JobMatcher(cv_text="Python").score_basis
    assert re.fullmatch(r"[0-9a-f]{12}:" + re.escape(GAZETTEER_VERSION), basis)
    assert basis == JobMatcher(cv_text="Python").score_basis
    assert basis != JobMatcher(cv_text="Python, Rust").score_basis
    # the keywords extend the gazetteer: changing them makes stored scores stale too
    assert basis != JobMatcher(cv_text="Python", extra_terms=["welding"]).score_basis


async def test_load_matcher_reads_the_profile_cv_and_the_search_keywords(tmp_path):
    cv = tmp_path / "cv.tex"
    cv.write_text(r"\section{Skills} Python, Django", encoding="utf-8")
    async with AsyncSessionLocal() as db:
        db.add(UserProfile(full_name="A", email="a@example.com", base_cv_path=str(cv)))
        db.add(SearchSettings(keywords={"include": ["underwater welding"]}))
        await db.flush()
        matcher = await load_matcher(db)
    assert matcher.score_basis == JobMatcher(cv.read_text(), ["underwater welding"]).score_basis
    job = _make_job(
        title="Engineer", description="Requirements\n- Python\n- Underwater welding\n- Rust\n"
    )
    # the CV is the skill set; the keyword is known to the gazetteer but not in the CV
    result = matcher.explain(job, JobFilters(keywords=["underwater welding"]))
    assert result.covered == ["python"]
    assert set(result.missing) == {"underwater welding", "rust"}


async def test_load_matcher_without_profile_or_settings_falls_back_to_keywords():
    async with AsyncSessionLocal() as db:
        matcher = await load_matcher(db)
    filters = JobFilters(keywords=["python"])
    job = _make_job(title="Engineer", description="Requirements\n- Python\n")
    assert matcher.matched_keywords(job, filters) == ["python"]
