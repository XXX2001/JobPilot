# backend/matching/cv_parser.py
"""CV LaTeX parser: the gazetteer skills a CV names, tagged with where it names them."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from backend.matching.gazetteer import Gazetteer, fold

# Context weights matching ATS behavior
CONTEXT_WEIGHTS = {
    "experience_recent": 1.0,
    "skills_section": 0.6,
    "profile": 0.5,
    "experience_older": 0.4,
}

_SECTION_RE = re.compile(
    r"\\begin\{(?:rSection|section|cvsection)\}\{([^}]+)\}(.*?)\\end\{(?:rSection|section|cvsection)\}",
    re.DOTALL,
)
# On the accent-folded section name.
_PROFILE = re.compile(r"\b(?:profile?|summary|about|objecti[fv]e?)\b")
_EXPERIENCE = re.compile(r"\b(?:experiences?|employment)\b")
# Role header "\textbf{Title} \hfill {Août 2023 -- Présent}": the first one is the recent role.
_ROLE_RE = re.compile(r"\\(?:textbf|bf)\b[^\n]*?\\hfill")

# A brace group, nested up to three deep ("{@{} l @{\hspace{6ex}} l}").
_GROUP = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
_LATEX_RE = re.compile(
    r"(?P<keep>\\\\)"  # a line break, not the start of an escape
    # column spec of a table ("{L C R}"), after the width of tabularx / tabular*
    r"|(?P<spec>\\begin\{(?:tabular[x*]\}\s*" + _GROUP + r"|(?:tabular|longtable|array)\})"
    r"\s*(?:\[[^\]]*\])?\s*" + _GROUP + r")"
    r"|\\(?P<char>[&#_%$])"
    r"|\{\\[`'^\"~]\s*(?P<a>[A-Za-z])\}"  # {\'e}
    r"|\\[`'^\"~]\s*(?:\{(?P<b>[A-Za-z])\}|(?P<c>[A-Za-z]))"  # \'{e}, \'e
    r"|\\c(?:\s*\{(?P<d>[A-Za-z])\}|\s+(?P<e>[A-Za-z]))"  # \c{c}, \c c
    r"|\\,|~"
)


def latex_to_text(cv_tex: str) -> str:
    """De-escape a LaTeX CV for the gazetteer: ``R\\&D`` is "R&D", ``C\\#`` is "C#".

    Escaped characters become themselves, ``~`` and ``\\,`` a space, accent macros their
    bare letter (the gazetteer folds accents anyway) and a table's column spec nothing.
    Commands and environments stay: the parser reads sections from them.

    ponytail: the escapes a CV uses, not a LaTeX reader — ``\\textbackslash``, ``\\oe`` or
    a user macro pass through as they are. Convert with a real detex if that bites.
    """

    def plain(m: re.Match[str]) -> str:
        if m["keep"]:
            return m[0]
        return m["char"] or m["a"] or m["b"] or m["c"] or m["d"] or m["e"] or " "

    return _LATEX_RE.sub(plain, cv_tex)


@dataclass
class SkillEntry:
    text: str  # canonical gazetteer name
    context: str
    weight: float


@dataclass
class CVProfile:
    skills: list[SkillEntry]
    raw_text_hash: str


class CVParser:
    """Extracts skills from a LaTeX CV with context tagging."""

    def parse(self, cv_tex: str, gaz: Gazetteer) -> list[SkillEntry]:
        """Every gazetteer skill in the CV, once, with the strongest context naming it.

        Text outside a profile or experience section (the skills table, or a template
        this parser does not know) counts as the skills section.
        """
        cv_tex = latex_to_text(cv_tex)
        texts: dict[str, list[str]] = {context: [] for context in CONTEXT_WEIGHTS}
        texts["skills_section"].append(_SECTION_RE.sub(" ", cv_tex))
        for name, body in _SECTION_RE.findall(cv_tex):
            section = fold(name)
            if _EXPERIENCE.search(section):
                roles = [m.start() for m in _ROLE_RE.finditer(body)]
                # Roles that cannot be told apart all count as older.
                second = roles[1] if len(roles) > 1 else len(body) if roles else 0
                texts["experience_recent"].append(body[:second])
                texts["experience_older"].append(body[second:])
            elif _PROFILE.search(section):
                texts["profile"].append(body)
            else:
                texts["skills_section"].append(body)
        # Strongest context first; the whole text last, so no skill of the CV is lost.
        best = dict.fromkeys(gaz.skills(cv_tex), "skills_section")
        for context in sorted(CONTEXT_WEIGHTS, key=CONTEXT_WEIGHTS.__getitem__):
            best.update(dict.fromkeys(gaz.skills("\n".join(texts[context])), context))
        return [SkillEntry(s, c, CONTEXT_WEIGHTS[c]) for s, c in sorted(best.items())]

    def build_profile(self, cv_tex: str, gaz: Gazetteer) -> CVProfile:
        return CVProfile(
            skills=self.parse(cv_tex, gaz),
            raw_text_hash=hashlib.sha256(cv_tex.encode()).hexdigest(),
        )
