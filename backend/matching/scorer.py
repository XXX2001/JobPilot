# backend/matching/scorer.py
"""Score a posting against the CV's skills: hard filters, coverage, title factor.

    score = 100 * coverage * (0.4 + 0.6 * T)

Coverage is the criticality-weighted share of the posting's skills the CV covers, the
required tier and the rest scored separately. T is the title factor; a non-technical
title gets no 0.4 floor (score = 100 * coverage * T). A posting that fails a hard
filter scores 0 and is flagged, never down-weighted. A posting whose description names
fewer than two skills is scored on its title alone, like a description-less card.
No recency, no embeddings, no LLM, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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
# Fewer skills in the body is an incidental mention, not a description of the job: one
# "Python" in a chief-of-staff posting would otherwise be 67% coverage.
PROVISIONAL_MIN_SKILLS = 2
# Bump when a scoring rule or the location data changes: stored scores go stale.
SCORER_VERSION = 2
PROVISIONAL_KEYWORD_SCORE = 50.0
PROVISIONAL_CV_SKILL_SCORE = 40.0

# Both run on accent-folded, lowercased text.
_NON_TECHNICAL = re.compile(
    r"\b(?:sales|account (?:executive|manager)|business developer|marketing"
    r"|comptab\w*|accountant|juriste|counsel|aml|recruit\w*"
    r"|customer (?:care|success|support)|biostatisti\w*|chef de projet)\b"
)
# Words that also name a product or a team inside an engineering title ("Software
# Engineer, Talent Platform", "Ingénieur logiciel - Commercial Aircraft"): they only
# count in the head of the title, before its first separator.
_NON_TECHNICAL_HEAD = re.compile(r"\b(?:commercial|legal|compliance|talent|analyst)\b")
_TITLE_HEAD_END = re.compile(r"[,(|/:\u2013\u2014]|\s-\s")
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
_DATA = Path(__file__).resolve().parent / "data"


def _place(text: str) -> str:
    """Folded, with hyphen and space equivalent ("Île-de-France" == "ile de france")."""
    return " ".join(fold(text).replace("-", " ").split())


def _table(name: str) -> dict[str, str]:
    """Folded place name -> ISO country code, from a ``code | name | name`` data file."""
    table: dict[str, str] = {}
    for line in (_DATA / name).read_text(encoding="utf-8").splitlines():
        code, *names = (part.strip() for part in line.split("|"))
        if code and not code.startswith("#"):
            table.update((_place(n), code) for n in names if n)
    return table


def _names(table: dict[str, str]) -> re.Pattern[str]:
    """Any name of the table as whole words, longest first; never after "new" (New Mexico)."""
    names = "|".join(map(re.escape, sorted(table, key=len, reverse=True)))
    return re.compile(r"(?<![^\W_])(?<!new )(?:" + names + r")(?![^\W_])")


# For the location filter. A place neither table knows is an unknown place, which a
# country-level filter lets through, unverified.
_COUNTRY_NAMES = _table("countries.txt")  # every ISO 3166-1 country, English and French
_COUNTRY_CODES = frozenset(_COUNTRY_NAMES.values())
_COUNTRY_NAME = _names(_COUNTRY_NAMES)
_CITIES = _table("cities.txt")  # capitals and large metros, to place "London"
_CITY = _names(_CITIES)
# A bare two-letter code is a US state or a Canadian province at least as often as the
# ISO country that shares it ("Denver, CO", "Regina, SK"): it stands for both.
# ponytail: US and Canada only. Add Indian and Australian states if "Pune, MH" shows up.
_SUBDIVISIONS = {
    **dict.fromkeys(
        "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh"
        " nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split(),
        "us",
    ),
    **dict.fromkeys("ab bc mb nb nl ns nt nu on pe qc sk yt".split(), "ca"),
}
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class ScoreResult:
    score: float  # 0-100
    passed_filters: bool
    reject_reason: str | None  # "location" | "excluded_keyword" | "excluded_company" | None
    covered: list[str]
    partial: list[str]  # same-family credit
    missing: list[str]  # sorted by criticality desc
    provisional: bool  # no description, or one naming under two skills: title-only score
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


@lru_cache(maxsize=64)
def keyword_skills(keywords: tuple[str, ...], gaz: Gazetteer) -> frozenset[str]:
    """The gazetteer skills the owner's search keywords name."""
    return frozenset(s for keyword in keywords for s in gaz.skills(keyword))


def _non_technical(folded_title: str) -> bool:
    head = _TITLE_HEAD_END.split(folded_title, maxsplit=1)[0]
    return bool(_NON_TECHNICAL.search(folded_title) or _NON_TECHNICAL_HEAD.search(head))


def _has_any(phrases: list[str], folded_text: str) -> bool:
    return any((p := _phrase(phrase)) is not None and p.search(folded_text) for phrase in phrases)


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
    """Countries the job's location (as posted) or ``JobDetails.country`` names.

    By name anywhere; by ISO code as the last comma part (Indeed's "Lyon, ARA, FR") or,
    in capitals, as the first or last word ("US - New York", "Remote US"); and when
    none of those names a country, by a major city ("London").
    """
    place = _place(location)
    found = {_COUNTRY_NAMES[name] for name in _COUNTRY_NAME.findall(place)}
    if code := _country_code(_place(country)):
        found.add(code)
    codes = [place.rsplit(",", 1)[-1].strip()]
    if not location.isupper():  # in "LA ROCHELLE" capitals say nothing
        words = _WORD.findall(location)
        codes += [w.lower() for w in (*words[:1], *words[-1:]) if w.isupper()]
    for code in codes:
        found |= {code} & _COUNTRY_CODES
        if code in _SUBDIVISIONS:
            found.add(_SUBDIVISIONS[code])
    return found or {_CITIES[city] for city in _CITY.findall(place)}


def _location(job: JobDetails, locations: list[str], remote: bool) -> tuple[bool, bool]:
    """(passes, unverified) for the location filter.

    A place entry must be in the location as whole words, unless the job is remote. A
    country entry rejects only a job proven to be in another country: a town the city
    table does not know ("Massy") cannot be proven outside France, so it passes,
    unverified.
    """
    places, countries = _wanted(tuple(locations))
    location = _place(job.location)
    here = _job_countries(job.location, job.country)
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
    """Per posting skill: 1.0 on the CV, FAMILY_CREDIT for a same-family CV skill, else 0.0.

    A search keyword the gazetteer took as a skill of its own (``gaz.extra_skills``) is
    the owner's term: it counts whether or not the CV spells it out.
    """
    return {
        skill: 1.0
        if skill in cv or skill in gaz.extra_skills
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
    job: JobDetails,
    cv: frozenset[str],
    filters: JobFilters,
    gaz: Gazetteer,
    *,
    claimed_only: bool = False,
) -> ScoreResult:
    """Score one posting against the CV's skills (see :func:`cv_skills`).

    ``claimed_only``: ``cv`` is not a CV but the skills the owner's keywords name. The
    score is then how strongly the posting asks for those; a skill nobody claimed is
    not a missing one.
    """
    reason, unverified = _reject_reason(job, filters)
    if reason is not None:
        return ScoreResult(0.0, False, reason, [], [], [], False)

    title_skills = gaz.skills(job.title)
    weights: dict[str, float] = {}
    if len(job.description.strip()) >= PROVISIONAL_MIN_CHARS:
        weights = extract_job_skills("", job.description, gaz)
    # Too little to cover in the body: the title is all there is.
    provisional = len(weights) < PROVISIONAL_MIN_SKILLS
    weights.update(dict.fromkeys(title_skills, REQUIRED))
    if claimed_only:
        weights = {s: w for s, w in weights.items() if s in cv or s in gaz.extra_skills}
    credit = skill_credit(weights, cv, gaz)
    covered = sorted(s for s, c in credit.items() if c == 1.0)
    partial = sorted(s for s, c in credit.items() if c == FAMILY_CREDIT)
    missing = sorted((s for s, c in credit.items() if c == 0.0), key=lambda s: (-weights[s], s))

    title = fold(job.title)
    # Literally, or by any alias of a skill it names ("Ingénieur Robotique" for robotics).
    keyword_in_title = _has_any(filters.keywords, title) or not title_skills.isdisjoint(
        keyword_skills(tuple(filters.keywords), gaz)
    )
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
        elif _non_technical(title):
            factor = TITLE_NON_TECHNICAL
        elif cv_skill_in_title:
            factor = TITLE_MATCH
        else:
            factor = TITLE_NEUTRAL
        multiplier = (1 - TITLE_WEIGHT) + TITLE_WEIGHT * factor
        if factor == TITLE_NON_TECHNICAL:
            # No floor: a sales posting that lists every skill of the CV stays near zero.
            multiplier = factor
        score = 100 * coverage(weights, credit) * multiplier
    return ScoreResult(
        round(score, 2), True, None, covered, partial, missing, provisional, unverified
    )
