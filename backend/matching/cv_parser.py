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
