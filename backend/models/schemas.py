from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RawJob(BaseModel):
    """Raw job data from any source before DB storage."""

    external_id: str = ""
    title: str = Field(max_length=300)
    company: str = Field(max_length=200)
    location: str = Field(default="", max_length=200)
    salary_text: str = Field(default="", max_length=100)
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: str = Field(default="", max_length=5000)
    requirements: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    url: str = Field(default="", max_length=2048)
    apply_url: str = Field(default="", max_length=2048)
    apply_method: str = ""
    posted_at: Optional[datetime] = None
    source_name: str = ""
    country: str = ""
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
    country: str = ""
