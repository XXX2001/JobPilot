# backend/matching/job_skills.py
"""Skills a posting asks for, each with a criticality weight.

A skill's weight is that of its strongest non-negated mention: required (1.0),
neutral (0.6), company blurb (0.4) or nice-to-have (0.3). The weight comes from the
section header the mention sits under, overridden by a marker in its own sentence.
"""

from __future__ import annotations

import re
from bisect import bisect_right

from backend.matching.gazetteer import Gazetteer, fold

REQUIRED = 1.0
NEUTRAL = 0.6
BLURB = 0.4
NICE = 0.3

_NEGATION_WINDOW = 6  # tokens before the mention, within its clause
_NEGATION_CHARS = 120  # how far back those tokens are looked for

# All patterns run on accent-folded, lowercased text. Order matters: first match wins.
_HEADERS: tuple[tuple[re.Pattern[str], float], ...] = (
    # "Profil souhaité", "Compétences souhaitées": the wanted profile is the requirements
    # section, although "souhaité" next to a skill marks a nice-to-have.
    (
        re.compile(
            r"\bprofil\b|\b(?:competences?|experiences?|qualifications?)"
            r" (?:souhaitee?s?|recherchee?s?|attendue?s?)\b"
        ),
        REQUIRED,
    ),
    (
        re.compile(
            r"\b(?:nice[- ]to[- ]haves?|bonus(?:es)?|bonus points|preferred|good to have|pluses"
            r"|ideally|idealement|competences appreciees|appreciee?s?|un plus"
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
    r"|exigee?s?|maitris\w*)\b"
)
# "Python is not required", "n'est pas obligatoire", "(no French required)": optional.
_NOT_REQUIRED = re.compile(
    r"\b(?:not|no|pas|non|isn.?t|aren.?t)\s+(?:\w+\s+)?(?:required|mandatory|essential"
    r"|necessary|a must|indispensables?|obligatoires?|imperatif|requise?s?|exigee?s?"
    r"|necessaires?)\b"
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
_NOT_A_NEGATION = re.compile(r"\b(?:not (?:only|just)|pas (?:seulement|uniquement)|sans oublier)\b")
# Between two mentions of one list: a negation on the first reaches the second.
_LIST_LINK = re.compile(r"[\s,/]*(?:or|and|nor|ou|et|ni)?[\s,/]*")
_CLAUSE = re.compile(r"(?<=[.;!?])\s+|\s+[-–]\s+|,?\s+(?:but|mais)\s+", re.IGNORECASE)
_SEGMENT = re.compile(r",\s+")
_WORD = re.compile(r"\w+")
_BULLET = " \t-*•▪◦·–#>"
_LIST_ITEM = re.compile(r"(?:[-*•▪◦·–]|\d+[.)])\s")


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


def _marker(folded: str) -> float | None:
    """The weight a marker in this text gives, or None when it holds no marker."""
    if _NOT_REQUIRED.search(folded):
        return NICE
    if _REQUIRED_INLINE.search(folded):
        return REQUIRED
    if _NICE_INLINE.search(folded):
        return NICE
    return None


def _segment_markers(clause: str) -> tuple[list[int], list[float | None]]:
    """Start offset and marker of each comma-separated part of a clause.

    A part without a marker takes the next marked part's ("Python, Docker required,
    Kubernetes a plus"), or the previous one's after the last marker.
    """
    starts = [0] + [m.end() for m in _SEGMENT.finditer(clause)]
    markers = [_marker(fold(clause[a:b])) for a, b in zip(starts, [*starts[1:], len(clause)])]
    for i in range(len(markers) - 2, -1, -1):
        if markers[i] is None:
            markers[i] = markers[i + 1]
    for i in range(1, len(markers)):
        if markers[i] is None:
            markers[i] = markers[i - 1]
    return starts, markers


def _negated(prefix: str) -> bool:
    window = " ".join(_WORD.findall(fold(prefix))[-_NEGATION_WINDOW:])
    return bool(_NEGATION.search(_NOT_A_NEGATION.sub(" ", window)))


def _clause_skills(clause: str, section: float, gaz: Gazetteer) -> list[tuple[str, float]]:
    """(skill, weight) for each non-negated mention in one clause."""
    mentions = gaz.extract(clause)
    if not mentions:
        return []
    folded = fold(clause)
    marker = _marker(folded)
    default = BLURB if section == NEUTRAL and _STACK_INLINE.search(folded) else section
    starts: list[int] = []
    markers: list[float | None] = []
    # Two kinds of marker in one clause ("Python required, Docker a plus"): each
    # comma-separated part goes by its own. Otherwise the marker covers the clause.
    if (
        marker is not None
        and _REQUIRED_INLINE.search(folded)
        and (_NICE_INLINE.search(folded) or _NOT_REQUIRED.search(folded))
    ):
        starts, markers = _segment_markers(clause)
    out: list[tuple[str, float]] = []
    negated_until: int | None = None  # end of the previous mention, when it was negated
    for mention in mentions:
        if negated_until is not None and _LIST_LINK.fullmatch(
            fold(clause[negated_until : mention.start])
        ):
            negated = True
        else:
            negated = _negated(clause[max(0, mention.start - _NEGATION_CHARS) : mention.start])
        negated_until = mention.end if negated else None
        if negated:
            continue
        if starts:
            marker = markers[bisect_right(starts, mention.start) - 1]
        out.append((mention.skill, default if marker is None else marker))
    return out


def extract_job_skills(title: str, description: str, gaz: Gazetteer) -> dict[str, float]:
    """Canonical skill -> criticality, the strongest non-negated mention winning."""
    out: dict[str, float] = {}
    section = NEUTRAL
    for line in description.splitlines():
        raw = line.strip()
        # Only leading markers go: a trailing "#" belongs to "C#".
        content = raw.lstrip(_BULLET).rstrip(" \t*")
        head, colon, rest = content.partition(":")
        # A list item is content, unless it is nothing but a header phrase and a colon:
        # "- Ideally 3+ years of experience" must not turn the bullets after it optional.
        if _LIST_ITEM.match(raw) and (not colon or rest.strip()):
            weight = None
        else:
            weight = _header_weight(head, gaz)
        if weight is not None:
            section = weight
            content = rest if colon else ""
        for clause in _CLAUSE.split(content):
            for skill, weight in _clause_skills(clause, section, gaz):
                out[skill] = max(out.get(skill, 0.0), weight)
    for skill in gaz.skills(title):
        out[skill] = REQUIRED  # named in the title: required, whatever the body says
    return out
