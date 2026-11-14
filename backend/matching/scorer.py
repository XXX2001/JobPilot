# backend/matching/scorer.py
"""Score a posting against the CV's skills: hard filters, coverage, title factor.

    score = 100 * coverage * (0.4 + 0.6 * T)

Coverage is the criticality-weighted share of the posting's skills the CV covers, the
required tier and the rest scored separately. T is the title factor. A posting that
fails a hard filter scores 0 and is flagged, never down-weighted. A posting whose
description names no skill is scored on its title alone, like a description-less card.
No recency, no embeddings, no LLM, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from backend.matching.filters import JobFilters
from backend.matching.gazetteer import Gazetteer, fold
from backend.matching.job_skills import REQUIRED, extract_job_skills
from backend.models.schemas import JobDetails

REQUIRED_SHARE = 0.65  # coverage = 0.65 * cov(required) + 0.35 * cov(other)
SHRINK = 0.5  # pseudo-weight of unknown skills: a one-skill posting cannot reach 100
FAMILY_CREDIT = 0.5  # the CV has a skill of the same family (Django for FastAPI)
TITLE_WEIGHT = 0.6
TITLE_MATCH = 1.0
TITLE_NEUTRAL = 0.6
TITLE_NON_TECHNICAL = 0.1
PROVISIONAL_MIN_CHARS = 80  # a shorter description is a card, not a posting
PROVISIONAL_KEYWORD_SCORE = 70.0
PROVISIONAL_CV_SKILL_SCORE = 45.0

# Both run on accent-folded, lowercased text.
_NON_TECHNICAL = re.compile(
    r"\b(?:sales|account (?:executive|manager)|commercial|business developer|marketing"
    r"|comptab\w*|accountant|legal|juriste|counsel|compliance|aml|recruit\w*|talent"
    r"|customer (?:care|success|support)|biostatisti\w*|analyst|chef de projet)\b"
)
_REMOTE = re.compile(
    r"\b(?:remote|teletravail|home[- ]?office|home[- ]?based|work(?:ing)? from home"
    r"|a distance|distanciel|anywhere)\b"
)
_NOT_REMOTE = re.compile(r"\b(?:hybrid\w*|on[- ]?site|sur site|presentiel)\b")
# Blanked from the description before looking for a remote marker: other senses of
# "remote", negated and part-time forms.
# ponytail: a phrase list. Parse the workplace type per source if it leaks in practice.
_REMOTE_NOISE = re.compile(
    r"\bremote(?:ly)?[- ](?:control\w*|sens\w+|monitor\w+|access|desktop|operat\w+|sites?"
    r"|diagnos\w+|support|devices?|management)\b"
    r"|\b(?:no|not|non|pas de|sans|aucun) (?:\w+ )?(?:remote|teletravail|home[- ]?office)\b"
    r"|\b(?:remote|teletravail)(?: work)?(?: is| n.est)? (?:not|pas) \w+"
    r"|\b\d+ ?(?:jours?|j|days?)(?: de| of| par| per)? (?:remote|teletravail|home[- ]?office)\b"
    r"|\b(?:remote|teletravail) (?:partiel|occasionnel|partial|occasional)\b"
    r"|\bhybrid\w*[ /-]+remote\b|\bremote[ /-]+hybrid\w*"
)
# Folded country name -> ISO code, for the location filter. Not exhaustive: an unknown
# country is an unknown place, which a country-level filter lets through.
_COUNTRY_NAMES = {
    name: code
    for code, names in {
        "fr": "france",
        "de": "germany|allemagne|deutschland",
        "gb": "united kingdom|uk|royaume uni|england|angleterre|scotland|great britain",
        "us": "united states|usa|etats unis",
        "es": "spain|espagne",
        "it": "italy|italie",
        "be": "belgium|belgique",
        "ch": "switzerland|suisse",
        "lu": "luxembourg",
        "nl": "netherlands|pays bas",
        "pt": "portugal",
        "ie": "ireland|irlande",
        "ca": "canada",
        "pl": "poland|pologne",
        "se": "sweden|suede",
        "dk": "denmark|danemark",
        "no": "norway|norvege",
        "fi": "finland|finlande",
        "at": "austria|autriche",
        "ro": "romania|roumanie",
        "cz": "czech republic|czechia",
        "gr": "greece|grece",
        "hu": "hungary|hongrie",
        "ua": "ukraine",
        "tr": "turkey|turquie",
        "il": "israel",
        "ma": "morocco|maroc",
        "tn": "tunisia|tunisie",
        "dz": "algeria|algerie",
        "in": "india|inde",
        "cn": "china|chine",
        "jp": "japan|japon",
        "sg": "singapore|singapour",
        "au": "australia|australie",
        "br": "brazil|bresil",
        "mx": "mexico|mexique",
    }.items()
    for name in names.split("|")
}
_COUNTRY_CODES = frozenset(_COUNTRY_NAMES.values())
_COUNTRY_NAME = re.compile(
    r"(?<![^\W_])(?:" + "|".join(sorted(_COUNTRY_NAMES, key=len, reverse=True)) + r")(?![^\W_])"
)


@dataclass(frozen=True)
class ScoreResult:
    score: float  # 0-100
    passed_filters: bool
    reject_reason: str | None  # "location" | "excluded_keyword" | "excluded_company" | None
    covered: list[str]
    partial: list[str]  # same-family credit
    missing: list[str]  # sorted by criticality desc
    provisional: bool  # no description, or one that names no skill: title-only score
    # The location filter lists a country (or the job is remote) and the job's location
    # does not name its country: let through, not proven to be inside.
    location_unverified: bool = False


@lru_cache(maxsize=16)
def cv_skills(cv_text: str, gaz: Gazetteer) -> frozenset[str]:
    """Canonical skills named in the CV, cached per (CV text, gazetteer)."""
    return frozenset(gaz.skills(cv_text))


@lru_cache(maxsize=1024)
def _phrase(phrase: str) -> re.Pattern[str] | None:
    """Whole-word pattern for a phrase, to run on folded text. None for a blank phrase."""
    words = fold(phrase).split()
    if not words:
        return None
    # Not \b: a phrase may start or end with a non-word character ("c++", ".net").
    return re.compile(r"(?<![^\W_])" + r"\s+".join(map(re.escape, words)) + r"(?![^\W_])")


def _has_any(phrases: list[str], folded_text: str) -> bool:
    return any((p := _phrase(phrase)) is not None and p.search(folded_text) for phrase in phrases)


def _place(text: str) -> str:
    """Folded, with hyphen and space equivalent ("Île-de-France" == "ile de france")."""
    return " ".join(fold(text).replace("-", " ").split())


def _country_code(text: str) -> str | None:
    """ISO code for a country name or code ("france", "FR", "uk"), else None."""
    code = _COUNTRY_NAMES.get(text, text)
    return code if code in _COUNTRY_CODES else None


@lru_cache(maxsize=64)
def _wanted(locations: tuple[str, ...]) -> tuple[tuple[str, ...], frozenset[str]]:
    """The filter as (places, country codes). Blank entries are dropped.

    "Paris, France" is the place Paris, its country only a qualifier; an entry that is
    nothing but a country ("France", "FR") is a country-level filter.
    """
    places: list[str] = []
    countries: set[str] = set()
    for entry in locations:
        parts = [p for p in (_place(part) for part in entry.split(",")) if p]
        named = [p for p in parts if _country_code(p) is None]
        if named:
            places.append(named[0])
        else:
            countries.update(c for p in parts if (c := _country_code(p)))
    return tuple(places), frozenset(countries)


def _job_countries(location: str, country: str) -> set[str]:
    """Countries the job names: by name anywhere, by ISO code as the last comma part
    (Indeed's "Lyon, ARA, FR") or in ``JobDetails.country``."""
    found = {_COUNTRY_NAMES[name] for name in _COUNTRY_NAME.findall(location)}
    for candidate in (location.rsplit(",", 1)[-1].strip(), _place(country)):
        if code := _country_code(candidate):
            found.add(code)
    return found


def _location(job: JobDetails, locations: list[str], remote: bool) -> tuple[bool, bool]:
    """(passes, unverified) for the location filter.

    A place entry must be in the location as whole words, unless the job is remote. A
    country entry rejects only a job that names another country: without a city table
    "Marseille" cannot be proven to be outside France, so it passes, unverified.
    """
    places, countries = _wanted(tuple(locations))
    location = _place(job.location)
    here = _job_countries(location, job.country)
    if not (places or countries) or not (location or here):
        return True, False
    if _has_any(list(places), location):
        return True, False
    if countries and here:
        return not here.isdisjoint(countries), False
    return bool(countries) or remote, True


def _is_remote(location: str, description: str) -> bool:
    """The location says remote, or (when it does not say on-site) the description does."""
    if _NOT_REMOTE.search(location):
        return False
    return bool(_REMOTE.search(location) or _REMOTE.search(_REMOTE_NOISE.sub(" ", description)))


def _reject_reason(job: JobDetails, filters: JobFilters) -> tuple[str | None, bool]:
    """(reason a hard filter rejects the job or None, location unverified)."""
    if _has_any(filters.excluded_keywords, fold(job.title)):
        return "excluded_keyword", False
    company = fold(job.company).strip()
    if any(company == fold(c).strip() for c in filters.excluded_companies):
        return "excluded_company", False
    # The description is only read for a remote search; a remote job is then held to
    # the configured countries, not the cities ("Remote, France" for a Paris search).
    remote = _is_remote(fold(job.location), fold(job.description) if filters.remote_only else "")
    if filters.remote_only and not remote:
        return "location", False
    passes, unverified = _location(job, filters.locations, remote)
    return (None, unverified) if passes else ("location", False)


def skill_credit(weights: dict[str, float], cv: frozenset[str], gaz: Gazetteer) -> dict[str, float]:
    """Per posting skill: 1.0 on the CV, FAMILY_CREDIT for a same-family CV skill, else 0.0."""
    return {
        skill: 1.0
        if skill in cv
        else FAMILY_CREDIT
        if any(gaz.related(skill, have) for have in cv)
        else 0.0
        for skill in weights
    }


def coverage(weights: dict[str, float], credit: dict[str, float]) -> float:
    """Criticality-weighted share (0-1) of the posting's skills the CV covers."""
    def cov(skills: list[str]) -> float:
        total = sum(weights[s] for s in skills)
        return sum(weights[s] * credit[s] for s in skills) / (total + SHRINK)

    required = [s for s, w in weights.items() if w >= REQUIRED]
    other = [s for s, w in weights.items() if w < REQUIRED]
    # An empty tier carries no weight: a posting that only lists requirements is not
    # capped at REQUIRED_SHARE.
    if not required or not other:
        return cov(required or other)
    return REQUIRED_SHARE * cov(required) + (1 - REQUIRED_SHARE) * cov(other)


def score_job(
    job: JobDetails, cv: frozenset[str], filters: JobFilters, gaz: Gazetteer
) -> ScoreResult:
    """Score one posting against the CV's skills (see :func:`cv_skills`)."""
    reason, unverified = _reject_reason(job, filters)
    if reason is not None:
        return ScoreResult(0.0, False, reason, [], [], [], False)

    title_skills = gaz.skills(job.title)
    weights: dict[str, float] = {}
    if len(job.description.strip()) >= PROVISIONAL_MIN_CHARS:
        weights = extract_job_skills("", job.description, gaz)
    # Nothing to cover in the body: coverage is undefined, the title is all there is.
    provisional = not weights
    weights.update(dict.fromkeys(title_skills, REQUIRED))
    credit = skill_credit(weights, cv, gaz)
    covered = sorted(s for s, c in credit.items() if c == 1.0)
    partial = sorted(s for s, c in credit.items() if c == FAMILY_CREDIT)
    missing = sorted((s for s, c in credit.items() if c == 0.0), key=lambda s: (-weights[s], s))

    title = fold(job.title)
    keyword_in_title = _has_any(filters.keywords, title)
    cv_skill_in_title = not cv.isdisjoint(title_skills)
    if provisional:
        if keyword_in_title:
            score = PROVISIONAL_KEYWORD_SCORE
        elif cv_skill_in_title:
            score = PROVISIONAL_CV_SKILL_SCORE
        else:
            score = 0.0
    else:
        # A non-technical title beats a CV skill named in it ("Sales Engineer, Python
        # products") but not one of the owner's own search keywords.
        if keyword_in_title:
            factor = TITLE_MATCH
        elif _NON_TECHNICAL.search(title):
            factor = TITLE_NON_TECHNICAL
        elif cv_skill_in_title:
            factor = TITLE_MATCH
        else:
            factor = TITLE_NEUTRAL
        score = 100 * coverage(weights, credit) * ((1 - TITLE_WEIGHT) + TITLE_WEIGHT * factor)
    return ScoreResult(
        round(score, 2), True, None, covered, partial, missing, provisional, unverified
    )
