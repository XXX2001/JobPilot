# backend/matching/job_skills.py
"""Skills a posting asks for, each with a criticality weight.

A skill's weight is that of its strongest non-negated mention: required (1.0),
neutral (0.6), company blurb (0.4) or nice-to-have (0.3). The weight comes from the
section header the mention sits under, overridden by a marker in its own sentence.
"""

from __future__ import annotations

import re

from backend.matching.gazetteer import Gazetteer, fold

REQUIRED = 1.0
NEUTRAL = 0.6
BLURB = 0.4
NICE = 0.3

_NEGATION_WINDOW = 6  # tokens before the mention, within its sentence

# All patterns run on accent-folded, lowercased text. Order matters: first match wins.
_HEADERS: tuple[tuple[re.Pattern[str], float], ...] = (
    (
        re.compile(
            r"\b(?:nice[- ]to[- ]haves?|bonus(?:es)?|bonus points|preferred|good to have|pluses"
            r"|ideally|idealement|competences appreciees|appreciee?s?|souhaitee?s?|un plus"
            r"|atouts?|would be (?:fantastic|great|amazing|a plus))\b"
        ),
        NICE,
    ),
    (
        re.compile(
            r"\b(?:requirements?|required|must[- ]haves?|qualifications?|what you.ll need"
            r"|you have|who you are|about you|what we.re looking for|profil|exigences"
            r"|prerequis|competences (?:requises|cles)|vous avez|vous etes|vous maitrisez"
            r"|hard skills|key skills|expected skills)\b"
        ),
        REQUIRED,
    ),
    (
        re.compile(
            r"\b(?:our (?:tech )?stack|notre stack|our tech environment|benefits|avantages|perks"
            r"|about us|a propos|who we are|qui sommes|hiring process|processus|diversity"
            r"|what we offer|nous offrons|salary|remuneration)\b"
        ),
        BLURB,
    ),
    (
        re.compile(
            r"\b(?:responsibilities|missions?|what you.ll do|(?:the|your) role"
            r"|about the (?:job|role)|job description|le poste|description du poste|votre role)\b"
        ),
        NEUTRAL,
    ),
)
_REQUIRED_INLINE = re.compile(
    r"\b(?:must|required|mandatory|essential|indispensable|obligatoire|imperatif|requise?s?"
    r"|exige|maitris\w*)\b"
)
# ponytail: a bare "plus" is not a marker ("plus de 5 ans", "Python plus Docker");
# only "a plus" / "un plus" count.
_NICE_INLINE = re.compile(
    r"\b(?:nice to have|a plus(?! de\b)|un plus|bonus|preferred|ideally|idealement|appreciee?s?"
    r"|souhaitee?s?|atout|familiarity|exposure)\b"
)
_STACK_INLINE = re.compile(
    r"\b(?:our (?:tech )?stack|notre stack|stack is composed|our tech environment|we use"
    r"|we build with|nous utilisons)\b"
)
_NEGATION = re.compile(
    r"\b(?:not|no|never|without|won.?t|don.?t|doesn.?t|migrated away|moved away"
    r"|pas|aucune?|sans|jamais|ni)\b"
)
_CLAUSE = re.compile(r"(?<=[.;!?])\s+|\s+[-–]\s+")
_WORD = re.compile(r"\w+")
_BULLET = " \t-*•▪◦·–#>"


def _header_weight(candidate: str, gaz: Gazetteer) -> float | None:
    """The section weight a header line opens, or None when the line is not a header.

    A header is short, is not a sentence and names no skill itself, so a bullet such as
    "Python required" stays content.
    """
    text = fold(candidate).strip()
    if not text or len(text) > 60 or len(text.split()) > 8 or text.endswith("."):
        return None
    for pattern, weight in _HEADERS:
        if pattern.search(text):
            return None if gaz.extract(candidate) else weight
    return None


def _negated(prefix: str) -> bool:
    window = _WORD.findall(fold(prefix))[-_NEGATION_WINDOW:]
    return bool(_NEGATION.search(" ".join(window)))


def extract_job_skills(title: str, description: str, gaz: Gazetteer) -> dict[str, float]:
    """Canonical skill -> criticality, the strongest non-negated mention winning."""
    out: dict[str, float] = {}
    section = NEUTRAL
    for line in description.splitlines():
        content = line.strip(_BULLET)
        head, colon, rest = content.partition(":")
        weight = _header_weight(head, gaz)
        if weight is not None:
            section = weight
            content = rest if colon else ""
        for clause in _CLAUSE.split(content):
            mentions = gaz.extract(clause)
            if not mentions:
                continue
            folded = fold(clause)
            if _REQUIRED_INLINE.search(folded):
                weight = REQUIRED
            elif _NICE_INLINE.search(folded):
                weight = NICE
            elif section == NEUTRAL and _STACK_INLINE.search(folded):
                weight = BLURB
            else:
                weight = section
            for mention in mentions:
                if not _negated(clause[: mention.start]):
                    out[mention.skill] = max(out.get(mention.skill, 0.0), weight)
    for skill in gaz.skills(title):
        out[skill] = REQUIRED  # named in the title: required, whatever the body says
    return out
