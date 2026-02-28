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
