"""Job description NLP extraction — skills, criticality, knockout filters."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from backend.matching.skill_patterns import (
    KNOCKOUT_PATTERN,
    TECH_PATTERN,
    SKILL_PHRASE_PATTERNS,
    classify_section,
    extract_linguistic_modifier,
)

logger = logging.getLogger(__name__)

# Section split: detect headers like "Requirements:", "Nice to have:", etc.
_SECTION_SPLIT_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,3}\s*)?([A-Za-z][A-Za-z\s/&'-]{2,40})(?:\s*[:：\-—]|\s*\n)",
    re.MULTILINE,
)

# Bullet detection
_BULLET_RE = re.compile(r"(?:^|\n)\s*[-•*▪◦]\s*(.+?)(?=\n\s*[-•*▪◦]|\n\s*\n|\Z)", re.DOTALL)

# Non-skill stopwords for filtering extracted terms
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has",
    "been", "will", "are", "was", "our", "your", "about", "work",
    "team", "ability", "strong", "good", "excellent", "company",
    "experience", "years", "role", "position", "job", "salary",
    "remote", "benefits", "competitive", "options",
    "requirements", "nice", "have", "about", "what",
}


@dataclass
class JobSkill:
    text: str
    criticality: float
    section: str  # "required", "preferred", "neutral"
    embedding: list[float] = field(default_factory=list)


@dataclass
class JobProfile:
    skills: list[JobSkill]
    knockout_filters: list[str] = field(default_factory=list)


class JobSkillExtractor:
    """Extracts skills from job descriptions with criticality scoring."""

    def extract(self, description: str) -> JobProfile:
        """Extract skills and knockout filters from a job description."""
        if not description or not description.strip():
            return JobProfile(skills=[], knockout_filters=[])

        # Step 1: Detect knockout filters
        knockouts = [m.group(0).strip() for m in KNOCKOUT_PATTERN.finditer(description)]

        # Step 2: Split into sections
        section_blocks = self._split_sections(description)

        # Step 3: Extract skills per section with criticality
        skills: list[JobSkill] = []
        seen: set[str] = set()

        for section_type, text in section_blocks:
            section_skills = self._extract_from_block(text, section_type, seen)
            skills.extend(section_skills)

        return JobProfile(skills=skills, knockout_filters=knockouts)

    def _split_sections(self, description: str) -> list[tuple[str, str]]:
        """Split description into (section_type, text) blocks."""
        headers = list(_SECTION_SPLIT_RE.finditer(description))

        if not headers:
            # No clear sections — treat entire text as neutral
            return [("neutral", description)]

        blocks: list[tuple[str, str]] = []

        # Text before first header
        if headers[0].start() > 0:
            blocks.append(("neutral", description[: headers[0].start()]))

        for i, header in enumerate(headers):
            header_text = header.group(1).strip()
            section_type = classify_section(header_text)
            start = header.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(description)
            blocks.append((section_type, description[start:end]))

        return blocks

    def _extract_from_block(
        self, text: str, section_type: str, seen: set[str]
    ) -> list[JobSkill]:
        """Extract skills from a text block."""
        skills: list[JobSkill] = []

        # Section base criticality
        section_crit = {"required": 1.0, "preferred": 0.5, "neutral": 0.3}[section_type]

        # Extract from bullets
        bullets = _BULLET_RE.findall(text)
        sources = bullets if bullets else [text]

        for source in sources:
            source_clean = source.strip()
            if not source_clean:
                continue

            # Linguistic modifier for this specific line
            ling_mod = extract_linguistic_modifier(source_clean)
            criticality = max(section_crit, ling_mod) if ling_mod is not None else section_crit

            # Extract skill phrases ("experience with X", "knowledge of Y")
            for match in SKILL_PHRASE_PATTERNS.finditer(source_clean):
                term = match.group(1).strip()
                if self._is_valid_skill(term, seen):
                    seen.add(term.lower())
                    skills.append(JobSkill(
                        text=term, criticality=criticality, section=section_type,
                    ))

            # Extract tech patterns
            for match in TECH_PATTERN.finditer(source_clean):
                term = match.group(1).strip()
                if self._is_valid_skill(term, seen):
                    seen.add(term.lower())
                    skills.append(JobSkill(
                        text=term, criticality=criticality, section=section_type,
                    ))

        return skills

    @staticmethod
    def _is_valid_skill(term: str, seen: set[str]) -> bool:
        """Check if a term looks like a valid skill and hasn't been seen."""
        if not term or len(term) < 2:
            return False
        if term.lower() in _STOP_WORDS:
            return False
        if term.lower() in seen:
            return False
        # Filter out purely numeric terms
        if term.replace("+", "").replace("-", "").isdigit():
            return False
        return True
