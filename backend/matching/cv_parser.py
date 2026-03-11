# backend/matching/cv_parser.py
"""CV LaTeX parser — extracts skills with context tagging."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from backend.matching.skill_patterns import TECH_PATTERN

logger = logging.getLogger(__name__)

# Context weights matching ATS behavior
CONTEXT_WEIGHTS = {
    "experience_recent": 1.0,
    "skills_section": 0.6,
    "profile": 0.5,
    "experience_older": 0.4,
}

# Common LaTeX section patterns
_SECTION_RE = re.compile(
    r"\\begin\{(?:rSection|section|cvsection)\}\{([^}]+)\}(.*?)\\end\{(?:rSection|section|cvsection)\}",
    re.DOTALL,
)

# Skills row patterns: "Category & skill1, skill2, skill3 \\"
_SKILLS_ROW_RE = re.compile(
    r"(?:&|:)\s*([A-Za-z0-9\s,/\-\.+#]+?)\\\\",
)

# \cvskill{category}{skills} pattern
_CVSKILL_RE = re.compile(r"\\cvskill\{[^}]*\}\{([^}]+)\}")

# Experience role header — detect most recent vs older
_ROLE_RE = re.compile(
    r"\\textbf\{([^}]+)\}.*?\\(?:hfill|\\)\s*(\d{4})\s*[-–—]\s*(Present|\d{4})",
    re.DOTALL,
)

# Bullet items
_ITEM_RE = re.compile(r"\\item\s+(.+?)(?=\\item|\\end|$)", re.DOTALL)

# Common non-skill words to filter out in fallback
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "been", "will", "are", "was", "were", "can", "also", "our", "your",
    "about", "some", "text", "good", "measure", "also", "mentions",
    "begin", "end", "item", "textbf", "emph", "hfill",
}

# Known multi-word skills
_MULTI_WORD_SKILLS = {
    "machine learning", "deep learning", "data engineering", "data science",
    "natural language processing", "computer vision", "cloud computing",
    "project management", "agile methodology", "software development",
    "web development", "mobile development", "devops", "ci/cd",
    "unit testing", "integration testing", "rest api", "graphql",
    "apache airflow", "apache kafka", "apache spark",
}


@dataclass
class SkillEntry:
    text: str
    context: str
    weight: float
    embedding: list[float] = field(default_factory=list)


@dataclass
class CVProfile:
    skills: list[SkillEntry]
    raw_text_hash: str


class CVParser:
    """Extracts skills from LaTeX CV with context tagging."""

    def parse(self, cv_tex: str) -> list[SkillEntry]:
        """Extract skills with context from CV LaTeX source."""
        skills: list[SkillEntry] = []

        sections = dict(_SECTION_RE.findall(cv_tex))

        # Profile section
        for name in ("Profile", "Summary", "About", "Objective", "Profil"):
            if name in sections:
                skills.extend(self._extract_profile_skills(sections[name]))
                break

        # Skills section
        for name in ("Skills", "Technical Skills", "Compétences", "Technologies"):
            if name in sections:
                skills.extend(self._extract_skills_section(sections[name]))
                break

        # Experience section
        for name in ("Experience", "Work Experience", "Professional Experience",
                      "Expérience", "Employment"):
            if name in sections:
                skills.extend(self._extract_experience_skills(sections[name]))
                break

        # Fallback if fewer than 3 skills extracted
        if len(skills) < 3:
            logger.warning(
                "CV parser extracted only %d skills — falling back to full-text scan",
                len(skills),
            )
            skills = self._fallback_extract(cv_tex)

        return skills

    def build_profile(self, cv_tex: str) -> CVProfile:
        """Parse CV and return a CVProfile (embeddings empty, to be filled later)."""
        skills = self.parse(cv_tex)
        text_hash = hashlib.sha256(cv_tex.encode()).hexdigest()
        return CVProfile(skills=skills, raw_text_hash=text_hash)

    def _extract_profile_skills(self, text: str) -> list[SkillEntry]:
        """Extract skill-like phrases from profile/summary."""
        skills = []
        # Check for multi-word skills first
        text_lower = text.lower()
        for mw in _MULTI_WORD_SKILLS:
            if mw in text_lower:
                skills.append(SkillEntry(text=mw, context="profile", weight=CONTEXT_WEIGHTS["profile"]))

        # Then tech patterns
        for match in TECH_PATTERN.finditer(text):
            term = match.group(1).strip()
            if term.lower() not in _STOP_WORDS and len(term) >= 2:
                if not any(s.text.lower() == term.lower() for s in skills):
                    skills.append(SkillEntry(text=term, context="profile", weight=CONTEXT_WEIGHTS["profile"]))

        return skills

    def _extract_skills_section(self, text: str) -> list[SkillEntry]:
        """Extract skills from a structured skills section."""
        skills = []

        # Try \cvskill{}{} pattern
        for match in _CVSKILL_RE.finditer(text):
            for item in match.group(1).split(","):
                item = item.strip()
                if item and len(item) >= 2:
                    skills.append(SkillEntry(
                        text=item, context="skills_section",
                        weight=CONTEXT_WEIGHTS["skills_section"],
                    ))

        # Try table row pattern: "& skill1, skill2 \\"
        for match in _SKILLS_ROW_RE.finditer(text):
            for item in match.group(1).split(","):
                item = item.strip()
                if item and len(item) >= 2 and item.lower() not in _STOP_WORDS:
                    if not any(s.text.lower() == item.lower() for s in skills):
                        skills.append(SkillEntry(
                            text=item, context="skills_section",
                            weight=CONTEXT_WEIGHTS["skills_section"],
                        ))

        return skills

    def _extract_experience_skills(self, text: str) -> list[SkillEntry]:
        """Extract skills from experience bullets, distinguishing recent vs older roles."""
        skills = []
        roles = list(_ROLE_RE.finditer(text))

        if not roles:
            # Can't distinguish roles — treat all as older
            for match in _ITEM_RE.finditer(text):
                skills.extend(self._skills_from_bullet(
                    match.group(1), "experience_older"
                ))
            return skills

        # First role is most recent (or any with "Present")
        for i, role in enumerate(roles):
            is_recent = i == 0 or role.group(3).strip().lower() == "present"
            context = "experience_recent" if is_recent else "experience_older"

            # Get text between this role and next role (or end)
            start = role.end()
            end = roles[i + 1].start() if i + 1 < len(roles) else len(text)
            role_text = text[start:end]

            for match in _ITEM_RE.finditer(role_text):
                skills.extend(self._skills_from_bullet(match.group(1), context))

        return skills

    def _skills_from_bullet(self, bullet_text: str, context: str) -> list[SkillEntry]:
        """Extract tech mentions from a single experience bullet."""
        skills = []
        for match in TECH_PATTERN.finditer(bullet_text):
            term = match.group(1).strip()
            if term.lower() not in _STOP_WORDS and len(term) >= 2:
                skills.append(SkillEntry(
                    text=term, context=context,
                    weight=CONTEXT_WEIGHTS[context],
                ))
        return skills

    def _fallback_extract(self, cv_tex: str) -> list[SkillEntry]:
        """Last-resort full-text scan for skill-like phrases."""
        skills = []
        text_lower = cv_tex.lower()

        # Multi-word skills
        for mw in _MULTI_WORD_SKILLS:
            if mw in text_lower:
                skills.append(SkillEntry(
                    text=mw, context="skills_section",
                    weight=CONTEXT_WEIGHTS["skills_section"],
                ))

        # Tech patterns
        for match in TECH_PATTERN.finditer(cv_tex):
            term = match.group(1).strip()
            if (term.lower() not in _STOP_WORDS
                    and len(term) >= 2
                    and not any(s.text.lower() == term.lower() for s in skills)):
                skills.append(SkillEntry(
                    text=term, context="skills_section",
                    weight=CONTEXT_WEIGHTS["skills_section"],
                ))

        return skills
