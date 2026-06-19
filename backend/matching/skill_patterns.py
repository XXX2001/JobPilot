# backend/matching/skill_patterns.py
"""Shared regex patterns and linguistic classifiers for skill extraction."""
from __future__ import annotations

import re

# Section header classification patterns
CRITICAL_SECTION_PATTERNS = re.compile(
    r"(?i)\b("
    r"require[ds]?|requirements?|must\s+have|essential|"
    r"you\s+bring|qualifications?|what\s+we\s+need|"
    r"what\s+you.ll\s+need|key\s+skills?"
    r")\b"
)

PREFERRED_SECTION_PATTERNS = re.compile(
    r"(?i)\b("
    r"nice\s+to\s+have|bonus|preferred|ideally|"
    r"plus|advantageous|desirable|good\s+to\s+have"
    r")\b"
)

# Skill phrase extraction patterns
SKILL_PHRASE_PATTERNS = re.compile(
    r"(?i)(?:"
    r"experience\s+(?:with|in)\s+|"
    r"knowledge\s+of\s+|"
    r"proficiency\s+in\s+|"
    r"familiarity\s+with\s+|"
    r"understanding\s+of\s+|"
    r"expertise\s+in\s+"
    r")([A-Za-z0-9\s/\-\.+#]+?)(?:[,;.]|\s+and\s+|\s+or\s+|$)"
)

# Tech-like pattern: capitalized words, compound with / or -
TECH_PATTERN = re.compile(
    r"\b("
    r"[A-Z][a-zA-Z0-9]*(?:\.[a-zA-Z]+)*|"  # Capitalized: Python, Node.js
    r"[a-zA-Z0-9]+(?:/[a-zA-Z0-9]+)+|"     # Slash compound: CI/CD
    r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+"      # Hyphen compound: front-end
    r")\b"
)

# Linguistic modifier patterns
LINGUISTIC_BOOST_PATTERNS = re.compile(
    r"(?i)\b(must|essential|required|mandatory|critical|necessary)\b"
)

LINGUISTIC_DROP_PATTERNS = re.compile(
    r"(?i)\b(bonus|plus|exposure\s+to|familiarity|familiar\s+with|"
    r"nice\s+to\s+have|preferred|desirable|advantageous|ideally)\b"
)

# Knockout filter patterns (years, degrees)
KNOCKOUT_PATTERN = re.compile(
    r"(?i)(\d+\+?\s*years?\s+(?:of\s+)?experience|"
    r"(?:MSc|PhD|Master|Bachelor|BSc|MBA)\s+(?:required|in))"
)


def classify_section(header: str) -> str:
    """Classify a section header as 'required', 'preferred', or 'neutral'."""
    if CRITICAL_SECTION_PATTERNS.search(header):
        return "required"
    if PREFERRED_SECTION_PATTERNS.search(header):
        return "preferred"
    return "neutral"


def extract_linguistic_modifier(text: str) -> float | None:
    """Return 1.0 for boost, 0.3 for drop, None for neutral."""
    if LINGUISTIC_BOOST_PATTERNS.search(text):
        return 1.0
    if LINGUISTIC_DROP_PATTERNS.search(text):
        return 0.3
    return None
