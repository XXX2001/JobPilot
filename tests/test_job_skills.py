"""Tests for posting skill extraction with criticality."""

from __future__ import annotations

import time

import pytest

from backend.matching.gazetteer import Gazetteer
from backend.matching.job_skills import extract_job_skills


@pytest.fixture(scope="module")
def g() -> Gazetteer:
    return Gazetteer.load()


def test_header_without_colon_keeps_first_bullet(g):
    d = "Requirements\n- 5+ years of experience with Python\n- PostgreSQL"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "postgresql": 1.0}


def test_header_with_skills_on_the_same_line(g):
    d = "Requirements: Python, Docker\nNice to have: Rust"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "docker": 1.0, "rust": 0.3}


def test_all_lowercase_posting_still_extracts(g):
    d = "requirements\n- python\n- postgresql\nnice to have\n- docker"
    assert extract_job_skills("dev", d, g) == {"python": 1.0, "postgresql": 1.0, "docker": 0.3}


def test_negated_skill_is_dropped(g):  # Review Focus 2
    skills = extract_job_skills("Java Architect", "You will NOT use Python here. Java required.", g)
    assert "python" not in skills
    assert skills["java"] == 1.0


@pytest.mark.parametrize(
    "sentence",
    [
        "No Python in this role.",
        "We never touch Python.",
        "A backend without Python.",
        "Pas de Python sur ce poste.",
        "Un poste sans Python.",
        "Aucun développement Python.",
        "We migrated away from Python last year.",
    ],
)
def test_negation_markers(g, sentence):
    assert extract_job_skills("Dev", sentence, g) == {}


def test_negation_only_reaches_six_tokens_and_its_own_sentence(g):
    far = "This is not a role where one would sit and write Python."
    assert extract_job_skills("Dev", far, g) == {"python": 0.6}
    assert extract_job_skills("Dev", "No meetings. We write Python.", g) == {"python": 0.6}


def test_strongest_non_negated_mention_wins(g):
    d = "No Python in the legacy part.\nPython is a plus.\nWe write Python daily."
    assert extract_job_skills("Dev", d, g) == {"python": 0.6}


def test_nice_to_have_is_lower_than_required(g):
    d = "Must have\n- Python\n\nNice to have\n- Rust\n- Scala"
    skills = extract_job_skills("Dev", d, g)
    assert skills == {"python": 1.0, "rust": 0.3, "scala": 0.3}
    assert skills["rust"] < skills["python"]


def test_inline_markers_override_the_section(g):
    d = "About the job\nPython is required. Docker is a bonus. We also run PostgreSQL."
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "docker": 0.3, "postgresql": 0.6}


def test_short_bullet_with_a_marker_is_not_a_header(g):
    d = "- Python required\n- Docker is a plus\n- PostgreSQL"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "docker": 0.3, "postgresql": 0.6}


def test_section_ends_at_the_next_header(g):
    d = "Requirements\n- Python\n\nResponsibilities\n- Deploy with Docker"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "docker": 0.6}


def test_company_stack_blurb(g):
    assert extract_job_skills("Dev", "Our stack: Python, Docker.", g) == {
        "python": 0.4,
        "docker": 0.4,
    }
    assert extract_job_skills("Dev", "Notre stack\n- Python", g) == {"python": 0.4}
    assert extract_job_skills("Dev", "We use Docker every day.", g) == {"docker": 0.4}
    # under a requirements header the header wins
    d = "Requirements\n- Fluent in the tools we use: Python"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0}


def test_title_skill_counts_as_required(g):
    assert extract_job_skills("Python Developer", "", g) == {"python": 1.0}
    d = "Nice to have: Python. No Python on Fridays."
    assert extract_job_skills("Python Developer", d, g) == {"python": 1.0}


def test_french_markers(g):
    assert extract_job_skills("Ingénieur", "Kubernetes serait un plus", g) == {"kubernetes": 0.3}
    assert extract_job_skills("Ingénieur", "maîtrise de ROS 2 indispensable", g) == {"ros 2": 1.0}
    d = (
        "Profil recherché\n- Python\n\nCompétences requises :\n- Docker\n\n"
        "Vous maîtrisez :\n- PostgreSQL\n\nAtouts\n- Rust\n\n"
        "Idéalement, vous connaissez Scala. Django est apprécié. Java impératif."
    )
    assert extract_job_skills("Ingénieur", d, g) == {
        "python": 1.0,
        "docker": 1.0,
        "postgresql": 1.0,
        "rust": 0.3,
        "scala": 0.3,
        "django": 0.3,
        "java": 1.0,
    }


def test_french_plus_meaning_more_is_not_nice_to_have(g):
    d = "Vous avez plus de 5 ans d'expérience grâce à plus de dix projets Python."
    assert extract_job_skills("Ingénieur", d, g) == {"python": 0.6}


def test_empty_posting(g):
    assert extract_job_skills("", "", g) == {}


# --- review fixes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Requirements\n- Ideally 3+ years of experience\n- Python\n- ROS 2",
            {"python": 1.0, "ros 2": 1.0},
        ),
        ("Requirements\n- Bonus: a PhD\n- Python\n- ROS 2", {"python": 1.0, "ros 2": 1.0}),
        ("Requirements\n- Competitive mindset, benefits of teamwork\n- Python", {"python": 1.0}),
        (
            "Nice to have\n- Rust\n- You have a driving licence\n- Scala",
            {"rust": 0.3, "scala": 0.3},
        ),
        ("Requirements\n1) About you and the team\n2) Python", {"python": 1.0}),
    ],
)
def test_a_bullet_is_not_a_section_header(g, description, expected):
    assert extract_job_skills("Dev", description, g) == expected


def test_a_bullet_that_is_only_a_header_and_a_colon_opens_a_section(g):
    d = "- Requirements:\n- Python\n- Nice to have:\n- Rust\n**Must have**\n* Docker"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "rust": 0.3, "docker": 1.0}


def test_french_wanted_profile_headers_are_requirements(g):
    assert extract_job_skills("Dev", "Profil souhaité\n- Python\n- ROS 2", g) == {
        "python": 1.0,
        "ros 2": 1.0,
    }
    assert extract_job_skills("Dev", "Compétences souhaitées :\n- Python", g) == {"python": 1.0}
    assert extract_job_skills("Dev", "Expérience souhaitée\n- Docker", g) == {"docker": 1.0}
    # inline, "souhaité" stays a nice-to-have marker
    assert extract_job_skills("Dev", "Docker souhaité", g) == {"docker": 0.3}


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Python is not required", {"python": 0.3}),
        ("Python n'est pas obligatoire", {"python": 0.3}),
        ("Docker is not mandatory", {"docker": 0.3}),
        ("Requirements\n- Python is not strictly required", {"python": 0.3}),
        ("Expérience ROS 2 exigée, Docker souhaité", {"ros 2": 1.0, "docker": 0.3}),
        ("Python required, Kubernetes is a plus", {"python": 1.0, "kubernetes": 0.3}),
        ("Python (required), Docker (bonus)", {"python": 1.0, "docker": 0.3}),
        (
            "Python, Docker required, Kubernetes a plus",
            {"python": 1.0, "docker": 1.0, "kubernetes": 0.3},
        ),
        (
            "Python, Docker and Kubernetes are required",
            {"python": 1.0, "docker": 1.0, "kubernetes": 1.0},
        ),
        ("We do not require Java, but Python is a must.", {"python": 1.0}),
        ("Not only Python but also Go.", {"python": 0.6, "go": 0.6}),
        ("Pas seulement Python mais aussi Go.", {"python": 0.6, "go": 0.6}),
        ("Sans oublier Python.", {"python": 0.6}),
        ("We don't care whether you know Python or Go", {}),
        ("No Python, Java or Go.", {}),
    ],
)
def test_inline_markers_and_negation_scope(g, description, expected):
    assert extract_job_skills("Dev", description, g) == expected


def test_trailing_hash_is_part_of_the_skill(g):
    d = "Requirements\n- Python\n- C#\n- Experience with C#"
    assert extract_job_skills("Dev", d, g) == {"python": 1.0, "c#": 1.0}
    assert extract_job_skills("Dev", "## Requirements\n- C#", g) == {"c#": 1.0}


@pytest.mark.parametrize("unit", ["Python, Docker, Kubernetes, ROS 2, ", "C++ "])
def test_one_keyword_stuffed_line_is_linear(g, unit):
    description = (unit * 20000)[:20000]  # the sources' MAX_DESCRIPTION, on one line
    start = time.perf_counter()
    skills = extract_job_skills("Dev", description, g)
    assert time.perf_counter() - start < 0.5  # was 1.5 s and 3.6 s
    assert skills
