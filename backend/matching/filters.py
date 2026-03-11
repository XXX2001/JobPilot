from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend.defaults import MIN_MATCH_SCORE


@dataclass
class JobFilters:
    keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    salary_min: Optional[int] = None
    experience_range: Optional[tuple[int, int]] = None
    remote_only: bool = False
    job_types: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    excluded_companies: list[str] = field(default_factory=list)
    min_score: float = MIN_MATCH_SCORE
