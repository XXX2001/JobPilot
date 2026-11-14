"""Tests for the coverage scorer: hard filters, coverage, title factor, provisional score."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.defaults import MIN_MATCH_SCORE
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
    # Same posting under a neutral title: the non-technical title is what sinks it, with
    # no floor (0.1 against 0.4 + 0.6 * 0.6), well under the default threshold.
    neutral = score_job(stuffed.model_copy(update={"title": "Team member"}), cv, f, g)
    assert neutral.score >= MIN_MATCH_SCORE
    assert sales.score == pytest.approx(neutral.score * 0.1 / 0.76, abs=0.1)
    assert sales.score < MIN_MATCH_SCORE / 2


def test_ambiguous_non_technical_words_only_count_at_the_head_of_the_title(g, cv):
    description = "We work with Python, Docker and Linux on a daily basis, in a team of ten people."
    neutral = score_job(job(title="Platform Engineer", description=description), cv, filters(), g)
    for title in ("Software Engineer, Talent Platform", "Ingénieur logiciel - Commercial Aircraft"):
        r = score_job(job(title=title, description=description), cv, filters(), g)
        assert r.score == neutral.score, title
    for title in ("Ingénieur Commercial (H/F)", "Legal Counsel - Software", "Engineer, Sales"):
        r = score_job(job(title=title, description=description), cv, filters(), g)
        assert r.score < MIN_MATCH_SCORE / 2, title


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
        # A non-technical title gets no floor: the bare factor.
        multiplier = factor if title == "Marketing Manager" else 0.4 + 0.6 * factor
        assert r.score == pytest.approx(100 * expected * multiplier, abs=0.1), title


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


@pytest.mark.parametrize(
    ("wanted", "location", "country", "passes"),
    [
        # a country entry: reject only a job that names another country
        (["France", "Paris"], "Lyon, ARA, FR", "", True),
        (["France", "Paris"], "FR", "", True),
        (["France"], "Marseille", "", True),
        (["France"], "Remote - France", "", True),
        (["France"], "Lyon", "fr", True),
        (["France"], "Berlin, Germany", "", False),
        (["France"], "London, ENG, GB", "", False),
        (["France"], "Remote UK", "", False),
        (["France"], "Lyon", "gb", False),
        (["France"], "", "de", False),
        (["FR"], "Nantes, France", "", True),
        # "City, Country": the city decides
        (["Paris, France"], "Paris", "", True),
        (["Paris, France"], "Paris, 75, FR", "", True),
        (["Paris, France"], "Paris, Île-de-France, France", "", True),
        (["Paris, France"], "Lyon, France", "", False),
        # hyphen and space are the same, accents fold
        (["Ile de France"], "Île-de-France", "", True),
        (["Saint-Denis"], "Saint Denis", "", True),
        # blank entries are no filter
        ([""], "Lyon", "", True),
        (["", " , "], "Lyon", "", True),
        (["", "Paris"], "Lyon", "", False),
        # a remote job is held to the country, not the city
        (["Paris"], "Remote - France", "", True),
        (["Paris"], "Lyon (Hybrid remote)", "", False),
    ],
)
def test_location_filter_matrix(g, cv, wanted, location, country, passes):
    r = score_job(job(location=location, country=country), cv, filters(locations=wanted), g)
    assert r.passed_filters is passes
    assert r.reject_reason == (None if passes else "location")


def test_unprovable_location_is_flagged_not_rejected(g, cv):
    f = filters(locations=["France", "Paris"])
    for town in ("Massy", "Saint-Maurice", "La Rochelle", "LA ROCHELLE", "Porto-Vecchio", "Remote"):
        r = score_job(job(location=town), cv, f, g)
        assert r.passed_filters and r.location_unverified, town
    assert not score_job(job(location="Lyon, ARA, FR"), cv, f, g).location_unverified
    assert not score_job(job(location="Marseille"), cv, f, g).location_unverified  # city table
    assert not score_job(job(location="Paris"), cv, f, g).location_unverified
    assert not score_job(job(location=""), cv, f, g).location_unverified


@pytest.mark.parametrize(
    "location",
    [
        # a country the short table did not have
        "Dubai, United Arab Emirates",
        "Thailand - Bangkok",
        "Riyadh, Saudi Arabia",
        "Slovakia - Bratislava",
        "Abidjan, Côte d'Ivoire",
        "Séoul, Corée du Sud",
        # an ISO code as the first or last token
        "US - New York",
        "Remote US",
        "Munich (DE)",
        # a major city, no country named
        "London",
        "Hong Kong",
        "Sydney",
        "New York",
        "Wrocław",
        "Remote - Bangalore",
        # a US state or Canadian province code is not France either
        "Denver, CO",
        "Toronto, ON",
    ],
)
def test_country_filter_rejects_a_location_proven_abroad(g, cv, location):
    r = score_job(job(location=location), cv, filters(locations=["France", "Paris"]), g)
    assert not r.passed_filters and r.reject_reason == "location", location


@pytest.mark.parametrize(
    ("wanted", "location"),
    [
        (["United States"], "Denver, CO"),  # CO is Colorado before it is Colombia
        (["United States"], "Boston, MA"),
        (["Canada"], "Regina, SK"),
        (["Canada"], "London, Ontario, Canada"),  # the named country wins over the city
        (["United Kingdom"], "London"),
        (["Germany"], "Remote DE"),
        (["France"], "Saint-Denis, La Réunion"),  # an overseas department is France
        (["Côte d'Ivoire"], "Abidjan"),
        (["Japan"], "Tokyo"),
    ],
)
def test_country_filter_accepts_a_location_proven_inside(g, cv, wanted, location):
    r = score_job(job(location=location), cv, filters(locations=wanted), g)
    assert r.passed_filters and not r.location_unverified, (wanted, location)


def test_country_and_city_tables_are_consistent():
    from backend.matching import scorer

    assert len(scorer._COUNTRY_CODES) >= 240  # every ISO 3166-1 country
    assert set(scorer._CITIES.values()) <= scorer._COUNTRY_CODES
    assert set(scorer._SUBDIVISIONS.values()) <= scorer._COUNTRY_CODES
    # A city name must not be a country name, or hide inside a town of another country.
    assert not set(scorer._CITIES) & set(scorer._COUNTRY_NAMES)
    for name in ("york", "orleans", "porto", "vienne", "maurice", "georgia", "jersey"):
        assert name not in scorer._CITIES and name not in scorer._COUNTRY_NAMES, name


@pytest.mark.parametrize(
    ("location", "extra", "passes"),
    [
        ("Paris", "No remote work possible.", False),
        ("Lyon", "Experience with remote control of robots.", False),
        ("Lyon", "You will process remote sensing data.", False),
        ("Lyon", "2 jours de télétravail par semaine.", False),
        ("Paris (Hybrid)", "Remote-first culture.", False),
        ("Lyon", "This is a fully remote position.", True),
        ("Anywhere in France", "", True),
        ("Home-based", "", True),
        ("Full remote", "", True),
        ("", "", False),
    ],
)
def test_remote_only_reads_the_marker_in_context(g, cv, location, extra, passes):
    posting = job(location=location, description=REAL_DESCRIPTION + "\n" + extra)
    assert score_job(posting, cv, filters(remote_only=True), g).passed_filters is passes


def test_remote_only_still_checks_the_country(g, cv):
    f = filters(remote_only=True, locations=["France"])
    assert score_job(job(location="Remote, France"), cv, f, g).passed_filters
    assert score_job(job(location="Remote"), cv, f, g).passed_filters
    for abroad in ("Remote UK", "Remote Germany", "Remote, Canada"):
        r = score_job(job(location=abroad), cv, f, g)
        assert not r.passed_filters and r.reject_reason == "location", abroad


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


def test_one_skill_posting_is_scored_on_its_title(g):
    """One recognised skill is an incidental mention, not a description of the job."""
    description = "We are looking for someone who knows Python. " + "Great team, great office. " * 4
    cv = frozenset({"python"})
    wanted = score_job(
        job(title="Python Developer", description=description), cv, filters(keywords=["python"]), g
    )
    assert wanted.provisional and wanted.score == 70
    assert wanted.covered == ["python"] and wanted.missing == []
    unrelated = score_job(
        job(title="Chief of Staff to the CMO", description=description), cv, filters(), g
    )
    assert unrelated.provisional and unrelated.score == 0
    two = score_job(
        job(title="Chief of Staff to the CMO", description=description + " And Docker."),
        cv,
        filters(),
        g,
    )
    assert not two.provisional and 0 < two.score < 100


def test_posting_without_any_known_skill_falls_back_to_the_title(g, cv):
    description = "Nous cherchons une personne motivée pour rejoindre notre équipe. " * 3
    r = score_job(job(title="Collaborateur", description=description), cv, filters(), g)
    assert r.passed_filters and r.provisional and r.score == 0
    # Same score as the description-less card: a body that names no skill says nothing.
    f = filters(keywords=["robotics"])
    wanted = score_job(job(title="Robotics Engineer", description=description), cv, f, g)
    assert wanted.provisional and wanted.score == 70
    by_skill = score_job(job(title="Python Developer", description=description), cv, f, g)
    assert by_skill.provisional and by_skill.score == 45 and by_skill.covered == ["python"]
    # A body that names two skills is a real, scored posting.
    body = score_job(
        job(title="Python Developer", description=description + " Python, Docker."), cv, f, g
    )
    assert not body.provisional


def test_all_required_posting_is_not_capped_by_the_empty_other_tier(g, cv):
    structured = (
        "Requirements\n- Python\n- C++\n- ROS 2\n- Docker\n- Linux\n\n"
        "Benefits\n- A friendly team in a bright office close to the station.\n"
    )
    passing = "Day to day the team works with Python, C++, ROS 2, Docker and Linux in production."
    required = score_job(job(title="Engineer", description=structured), cv, filters(), g)
    neutral = score_job(job(title="Engineer", description=passing), cv, filters(), g)
    assert required.score == pytest.approx(100 * (5 / 5.5) * 0.76, abs=0.1)
    assert neutral.score == pytest.approx(100 * (3 / 3.5) * 0.76, abs=0.1)
    assert required.score > neutral.score


def test_owner_keyword_does_not_lower_the_job_it_targets(cv):
    description = "Requirements: Robotics, Python\n" + "We are a small team in Paris. " * 3
    posting = job(title="Robotics Engineer", description=description)
    plain = Gazetteer.load()
    keyed = Gazetteer.load(extra_terms=["Ingénieur Robotique", "Python Developer"])
    base = score_job(posting, cv_skills(CV_TEXT, plain), filters(), plain)
    with_keywords = score_job(posting, cv_skills(CV_TEXT, keyed), filters(), keyed)
    assert with_keywords == base and base.covered == ["python", "robotics"]
    titled = score_job(
        posting.model_copy(update={"title": "Python Developer"}),
        cv_skills(CV_TEXT, keyed),
        filters(),
        keyed,
    )
    assert titled.missing == []


def test_keyword_that_became_a_skill_is_covered_not_missing(cv):
    """The owner's own term always counts; a curated skill must still be on the CV."""
    keyed = Gazetteer.load(extra_terms=["software engineer", "terraform"])
    assert keyed.extra_skills == frozenset({"software engineer"})
    description = "Requirements\n- Software Engineer with Python\n- Terraform\n" + "Team. " * 20
    r = score_job(
        job(title="Software Engineer, Backend", description=description),
        cv_skills(CV_TEXT, keyed),
        filters(keywords=["software engineer", "terraform"]),
        keyed,
    )
    assert "software engineer" in r.covered and r.missing == ["terraform"]


def test_claimed_only_scores_the_claimed_skills(g):
    """Without a CV nobody claimed the other skills: they are not missing."""
    description = "Requirements\n- Python\n- FastAPI\n- PostgreSQL\n- Docker\n" + "Team. " * 20
    posting = job(title="Backend Engineer", description=description)
    f = filters(keywords=["python"])
    penalised = score_job(posting, frozenset({"python"}), f, g)
    claimed = score_job(posting, frozenset({"python"}), f, g, claimed_only=True)
    assert penalised.score < MIN_MATCH_SCORE <= claimed.score
    assert claimed.covered == ["python"] and claimed.missing == []
    assert claimed.score == pytest.approx(100 * (1 / 1.5) * 0.76, abs=0.1)
    # A posting that never names the claimed skill scores nothing.
    other = job(title="Backend Engineer", description=description.replace("Python", "Java"))
    assert score_job(other, frozenset({"python"}), f, g, claimed_only=True).score == 0


def test_jargon_only_posting_is_not_covered_by_c_and_go(g):
    cv = cv_skills("Python, C, Go, Docker", g)
    description = (
        "You partner with C-level leaders on our Go-to-Market strategy after our Series C. "
        "Go deep on people topics and own the Go/No-Go decisions for Go Live."
    )
    r = score_job(job(title="Sr HR Business Partner", description=description), cv, filters(), g)
    assert r.covered == [] and r.score == 0
    card = score_job(job(title="Head of Go-to-Market", description=""), cv, filters(), g)
    assert card.covered == [] and card.score == 0


# --- provisional -------------------------------------------------------------------


def test_description_less_card_gets_provisional_title_score(g, cv):  # Review Focus 1
    r = score_job(
        job(title="Robotics Engineer", description=""), cv, filters(keywords=["robotics"]), g
    )
    assert r.provisional and r.score == 70


def test_keyword_named_in_the_title_by_an_alias_counts(g, cv):  # Review Focus 1
    f = filters(keywords=["robotics"])
    card = score_job(job(title="Ingénieur Robotique", description=""), cv, f, g)
    assert card.provisional and card.score == 70
    # The described posting gets the full title factor too.
    described = score_job(job(title="Ingénieur Robotique"), cv, f, g)
    literal = score_job(job(title="Robotics Engineer"), cv, f, g)
    assert described.score == literal.score


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
