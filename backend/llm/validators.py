from typing import Literal, Optional

from pydantic import BaseModel, field_validator


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
    section: Literal["Profile", "Experience", "Skills", "Additional Information"]
    original_text: str              # verbatim substring that must exist in the CV
    replacement_text: str           # the new text to substitute in
    reason: str                     # human-readable explanation
    job_requirement_matched: str    # which job requirement this addresses
    confidence: float               # 0.0–1.0; only apply if >= 0.7

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return v

    def is_applicable(self) -> bool:
        return self.confidence >= 0.7


class CVModifierOutput(BaseModel):
    replacements: list[CVReplacement] = []

    def top_three(self) -> list[CVReplacement]:
        """Return at most 3 applicable replacements (confidence ≥ 0.7), sorted by confidence descending."""
        applicable = [r for r in self.replacements if r.is_applicable()]
        return sorted(applicable, key=lambda r: r.confidence, reverse=True)[:3]
