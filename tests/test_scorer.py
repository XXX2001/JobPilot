"""Tests for the coverage scorer: hard filters, coverage, title factor, provisional score."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.matching.filters import JobFilters
from backend.matching.gazetteer import Gazetteer
from backend.matching.scorer import ScoreResult, cv_skills, score_job
from backend.models.schemas import JobDetails

CV_TEXT = """
Ingénieur robotique. Python, C++, ROS 2, MoveIt, Docker, Linux, Git.
Projets web : Django, PostgreSQL.
"""
REAL_DESCRIPTION = """About the role
You will build the motion stack of our mobile manipulators.

Requirements
- Python and C++
- ROS 2

Nice to have
- Docker
- Kubernetes
"""


@pytest.fixture(scope="module")
def g() -> Gazetteer:
    return Gazetteer.load()


@pytest.fixture(scope="module")
def cv(g) -> frozenset[str]:
    return cv_skills(CV_TEXT, g)


def job(
    title: str = "Robotics Software Engineer",
    description: str = REAL_DESCRIPTION,
    location: str = "Paris",
    company: str = "ACME",
    **extra,
) -> JobDetails:
    return JobDetails(
        title=title, company=company, location=location, description=description, **extra
    )


def filters(**kwargs) -> JobFilters:
    return JobFilters(**kwargs)


# --- CV skills ---------------------------------------------------------------------


def test_cv_skills_are_canonical_and_cached(g, cv):
    assert {"python", "c++", "ros 2", "django", "postgresql", "docker"} <= cv
    assert cv_skills(CV_TEXT, g) is cv


def test_cv_skills_follow_the_gazetteer_extra_terms(g):
    text = "Soudure TIG, Python"
    assert "soudure tig" not in cv_skills(text, g)
    assert "soudure tig" in cv_skills(text, Gazetteer.load(extra_terms=["Soudure TIG"]))


# --- ranking -----------------------------------------------------------------------


def test_keyword_stuffed_sales_job_scores_below_real_match(g, cv):  # Review Focus 2
    f = filters(keywords=["robotics"])
    stuffed = job(
        title="Sales Account Executive",
        description=(
            "Join our sales team and close deals with industrial customers across Europe.\n"
            "Keywords: Python, C++, ROS 2, MoveIt, Docker, Linux, Git, Django, PostgreSQL."
        ),
    )
    real = score_job(job(), cv, f, g)
    sales = score_job(stuffed, cv, f, g)
    assert real.passed_filters and sales.passed_filters
    assert sales.covered and not sales.missing  # every keyword is there
    assert sales.score < real.score
    # Same posting under a neutral title: the non-technical title is what sinks it,
    # by (0.4 + 0.6 * 0.1) / (0.4 + 0.6 * 0.6).
    neutral = score_job(stuffed.model_copy(update={"title": "Team member"}), cv, f, g)
    assert sales.score == pytest.approx(neutral.score * 0.46 / 0.76, abs=0.1)


def test_keyword_in_title_overrides_the_non_technical_family(g, cv):
    description = "Requirements\n- Python\n- ROS 2\n\nOur stack: Docker, Linux and Git every day."
    plain = score_job(job(title="Technical Sales", description=description), cv, filters(), g)
    wanted = score_job(
        job(title="Technical Sales", description=description),
        cv,
        filters(keywords=["technical sales"]),
        g,
    )
    assert wanted.score > plain.score


def test_title_factor_values(g, cv):
    description = "We work with Python, Docker and Linux on a daily basis, in a team of ten people."
    coverage = 1.8 / 2.3  # three neutral skills, all covered
    cases = {
        "Python Developer": 1.0,  # CV skill in the title (which also makes it required)
        "Platform Engineer": 0.6,
        "Marketing Manager": 0.1,
    }
    for title, factor in cases.items():
        r = score_job(job(title=title, description=description), cv, filters(), g)
        if title == "Python Developer":
            expected = 0.65 * (1.0 / 1.5) + 0.35 * (1.2 / 1.7)
        else:
            expected = coverage
        assert r.score == pytest.approx(100 * expected * (0.4 + 0.6 * factor), abs=0.1), title


def test_keyword_must_be_a_whole_word_in_the_title(g, cv):
    description = "We work with Python, Docker and Linux on a daily basis, in a team of ten people."
    whole = score_job(job(title="Lead Go-to-market", description=description), cv, filters(), g)
    inside = score_job(
        job(title="Lead Go-to-market", description=description), cv, filters(keywords=["mark"]), g
    )
    assert inside.score == whole.score


# --- hard filters ------------------------------------------------------------------


def test_wrong_city_is_filtered_not_downscored(g, cv):
    r = score_job(job(location="Lyon"), cv, filters(locations=["Paris"]), g)
    assert not r.passed_filters and r.reject_reason == "location"
    assert r.score == 0


def test_location_matches_whole_words_accent_folded(g, cv):
    f = filters(locations=["Île-de-France", "Paris"])
    assert score_job(job(location="Massy, Ile-de-France, FR"), cv, f, g).passed_filters
    assert score_job(job(location="PARIS 15e"), cv, f, g).passed_filters
    assert not score_job(job(location="Parisot, Tarn"), cv, f, g).passed_filters


def test_unknown_location_passes_filter(g, cv):
    r = score_job(job(location=""), cv, filters(locations=["Paris"]), g)
    assert r.passed_filters and r.reject_reason is None and r.score > 0


def test_remote_only_needs_a_remote_marker(g, cv):
    f = filters(remote_only=True, locations=["Paris"])
    onsite = score_job(job(location="Paris"), cv, f, g)
    assert not onsite.passed_filters and onsite.reject_reason == "location"
    assert score_job(job(location="Remote, France"), cv, f, g).passed_filters
    in_text = job(location="Lyon", description=REAL_DESCRIPTION + "\nPoste en télétravail complet.")
    assert score_job(in_text, cv, f, g).passed_filters


def test_excluded_keyword_is_a_whole_word_in_the_title(g, cv):
    f = filters(excluded_keywords=["intern", "Stagiaire"])
    r = score_job(job(title="Robotics Intern"), cv, f, g)
    assert not r.passed_filters and r.reject_reason == "excluded_keyword" and r.score == 0
    assert not score_job(job(title="Stagiaire robotique"), cv, f, g).passed_filters
    assert score_job(job(title="International Robotics Engineer"), cv, f, g).passed_filters
    # Only the title is checked: a description that mentions interns does not reject.
    mentions = job(description=REAL_DESCRIPTION + "\nYou will mentor one intern.")
    assert score_job(mentions, cv, f, g).passed_filters


def test_excluded_company(g, cv):
    r = score_job(job(company="  Évil Corp "), cv, filters(excluded_companies=["evil corp"]), g)
    assert not r.passed_filters and r.reject_reason == "excluded_company" and r.score == 0


# --- coverage ----------------------------------------------------------------------


def test_coverage_lists(g, cv):
    r = score_job(job(), cv, filters(), g)
    assert isinstance(r, ScoreResult)
    # "robotics" comes from the title and is in the CV ("robotique").
    assert r.covered == ["c++", "docker", "python", "robotics", "ros 2"]
    assert r.partial == ["kubernetes"]  # same family as Docker
    assert r.missing == []
    assert not r.provisional
    required = 4.0 / 4.5  # python, c++, ros 2 and the title's robotics at 1.0
    other = (0.3 + 0.3 * 0.5) / (0.6 + 0.5)  # nice-to-have: docker covered, kubernetes half
    # A CV skill is in the title: T = 1.
    expected = 100 * (0.65 * required + 0.35 * other)
    assert r.score == pytest.approx(expected, abs=0.1)


def test_missing_is_sorted_by_criticality(g):
    description = (
        "Nice to have\n- Kubernetes\n\nRequirements\n- Rust\n\n"
        "Day to day you will also touch Terraform with the rest of the platform team."
    )
    r = score_job(job(title="Engineer", description=description), frozenset(), filters(), g)
    assert r.missing == ["rust", "terraform", "kubernetes"]
    assert r.score == 0


def test_family_credit_is_half(g):
    description = "Requirements\n- FastAPI\n\n" + "We are a small team that ships every week. " * 3
    posting = job(title="Engineer", description=description)
    exact = score_job(posting, frozenset({"fastapi"}), filters(), g)
    family = score_job(posting, frozenset({"django"}), filters(), g)
    nothing = score_job(posting, frozenset({"postgresql"}), filters(), g)
    assert family.partial == ["fastapi"] and family.covered == [] and family.missing == []
    assert exact.covered == ["fastapi"]
    assert family.score == pytest.approx(exact.score / 2, abs=0.1)
    assert nothing.score == 0 and nothing.missing == ["fastapi"]


def test_one_skill_posting_cannot_reach_100(g):
    description = "We are looking for someone who knows Python. " + "Great team, great office. " * 4
    r = score_job(
        job(title="Python Developer", description=description),
        frozenset({"python"}),
        filters(keywords=["python"]),
        g,
    )
    assert r.covered == ["python"] and r.missing == []
    assert 0 < r.score < 70


def test_posting_without_any_known_skill_scores_zero(g, cv):
    description = "Nous cherchons une personne motivée pour rejoindre notre équipe. " * 3
    r = score_job(job(title="Collaborateur", description=description), cv, filters(), g)
    assert r.passed_filters and not r.provisional and r.score == 0


# --- provisional -------------------------------------------------------------------


def test_description_less_card_gets_provisional_title_score(g, cv):  # Review Focus 1
    r = score_job(
        job(title="Robotics Engineer", description=""), cv, filters(keywords=["robotics"]), g
    )
    assert r.provisional and r.score == 70


def test_provisional_score_tiers(g, cv):
    f = filters(keywords=["robotics"])
    only_cv_skill = score_job(job(title="Python Developer", description="  "), cv, f, g)
    assert only_cv_skill.provisional and only_cv_skill.score == 45
    assert only_cv_skill.covered == ["python"]
    neither = score_job(job(title="Office Manager", description="Apply now"), cv, f, g)
    assert neither.provisional and neither.score == 0 and neither.passed_filters


def test_provisional_card_still_goes_through_hard_filters(g, cv):
    r = score_job(
        job(title="Robotics Engineer", description="", location="Lyon"),
        cv,
        filters(keywords=["robotics"], locations=["Paris"]),
        g,
    )
    assert not r.passed_filters and r.score == 0


# --- invariants --------------------------------------------------------------------


def test_no_recency_in_score(g, cv):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=60)
    f = filters(keywords=["robotics"])
    today = score_job(job(posted_at=now, posted_date=now), cv, f, g)
    stale = score_job(job(posted_at=old, posted_date=old), cv, f, g)
    assert today == stale


def test_score_stays_in_range(g, cv):
    description = "Requirements\n- Python\n- C++\n- ROS 2\n- MoveIt\n- Docker\n- Linux\n- Git\n" * 3
    r = score_job(job(description=description), cv, filters(keywords=["robotics"]), g)
    assert 0 <= r.score <= 100


def test_scoring_is_fast(g, cv):
    f = filters(keywords=["robotics", "ros 2"], locations=["Paris"], excluded_keywords=["stage"])
    jobs = [
        job(title=f"Robotics Engineer {i}", description=REAL_DESCRIPTION * 4 + f"\nRef {i}")
        for i in range(1000)
    ]
    start = time.perf_counter()
    results = [score_job(j, cv, f, g) for j in jobs]
    assert time.perf_counter() - start < 5
    assert all(r.score > 0 for r in results)
