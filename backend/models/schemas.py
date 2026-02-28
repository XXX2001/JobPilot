from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class RawJob(BaseModel):
    """Raw job data from any source before DB storage."""

    external_id: str = ""
    title: str
    company: str
    location: str = ""
    salary_text: str = ""
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: str = ""
    requirements: list[str] = []
    benefits: list[str] = []
    url: str
    apply_url: str = ""
    apply_method: str = ""
    posted_at: Optional[datetime] = None
    source_name: str = ""
    raw_data: Optional[dict] = None


class JobDetails(BaseModel):
    """Enriched job for matching and pipeline."""

    id: Optional[int] = None
    title: str
    company: str
    location: str = ""
    description: str = ""
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    posted_at: Optional[datetime] = None
    posted_date: Optional[datetime] = None  # alias for recency scoring
    url: str = ""
    score: Optional[float] = None
    apply_url: str = ""
    apply_method: str = ""
