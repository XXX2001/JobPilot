from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class BulletEdit(BaseModel):
    index: int
    original: str
    edited: str
    reason: str


class CVSummaryEdit(BaseModel):
    edited_summary: Optional[str] = None
    changes_made: list[str] = []


class CVExperienceEdit(BaseModel):
    edits: list[BulletEdit] = []


class LetterEdit(BaseModel):
    edited_paragraph: str
    company_name: str


class CVReplacement(BaseModel):
    section: str                    # "Profile" | "Experience" | "Skills" | "Additional Information"
    original_text: str              # verbatim substring that must exist in the CV
    replacement_text: str           # the new text to substitute in
    reason: str                     # human-readable explanation
    job_requirement_matched: str    # which job requirement this addresses
    confidence: float               # 0.0–1.0; only apply if >= 0.7

    def is_applicable(self) -> bool:
        return self.confidence >= 0.7


class CVModifierOutput(BaseModel):
    replacements: list[CVReplacement] = []

    def top_three(self) -> list[CVReplacement]:
        """Return at most 3 replacements, sorted by confidence descending."""
        return sorted(self.replacements, key=lambda r: r.confidence, reverse=True)[:3]
