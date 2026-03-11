# tests/test_skill_patterns.py
from backend.matching.skill_patterns import (
    CRITICAL_SECTION_PATTERNS,
    PREFERRED_SECTION_PATTERNS,
    SKILL_PHRASE_PATTERNS,
    LINGUISTIC_BOOST_PATTERNS,
    LINGUISTIC_DROP_PATTERNS,
    classify_section,
    extract_linguistic_modifier,
)


def test_classify_required_section():
    assert classify_section("Requirements") == "required"
    assert classify_section("What you must have") == "required"
    assert classify_section("Essential qualifications") == "required"
    assert classify_section("You bring") == "required"


def test_classify_preferred_section():
    assert classify_section("Nice to have") == "preferred"
    assert classify_section("Bonus skills") == "preferred"
    assert classify_section("What's advantageous") == "preferred"


def test_classify_neutral_section():
    assert classify_section("About the company") == "neutral"
    assert classify_section("Benefits") == "neutral"


def test_linguistic_boost():
    assert extract_linguistic_modifier("Must have experience with Docker") == 1.0
    assert extract_linguistic_modifier("Essential: Python programming") == 1.0
    assert extract_linguistic_modifier("Required knowledge of SQL") == 1.0


def test_linguistic_drop():
    assert extract_linguistic_modifier("Bonus: experience with Kubernetes") == 0.3
    assert extract_linguistic_modifier("Exposure to cloud platforms is a plus") == 0.3
    assert extract_linguistic_modifier("Familiarity with Terraform preferred") == 0.3


def test_linguistic_neutral():
    assert extract_linguistic_modifier("Python programming") is None
