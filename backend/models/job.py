from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base
from backend.utils.time import naive_utc_now


class JobSource(Base):
    __tablename__ = "job_sources"
    # ``name`` is a natural key (the source identifier shown in the UI and used
    # by the batch runner). The explicit ``UniqueConstraint`` keeps the name
    # deterministic so the model and the ``t2b2_unique_keys`` migration produce
    # the identical schema object (``compare_metadata`` is the judge). It also
    # supersedes the old non-unique ``ix_job_sources_name`` index — a unique
    # constraint provides its own index for lookups.
    __table_args__ = (
        UniqueConstraint("name", name="uq_job_sources_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    prompt_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("job_sources.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    salary_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requirements: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    benefits: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    apply_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    apply_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now, index=True)
    dedup_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (
        Index("ix_job_matches_job_id_matched_at", "job_id", "matched_at"),
        # One match per (job, batch_date): the morning batch must not store the
        # same job twice in a single day. Replaces the old non-unique
        # ``ix_job_matches_job_id_batch_date`` index (a unique constraint
        # provides its own index). ``batch_date`` is nullable and SQLite treats
        # multiple NULLs as DISTINCT, so the constraint only bites for non-NULL
        # batch dates — intended (ad-hoc, dateless matches stay unconstrained).
        UniqueConstraint(
            "job_id", "batch_date", name="uq_job_matches_job_id_batch_date"
        ),
        CheckConstraint(
            "status IN ('new', 'skipped', 'applying', 'applied', "
            "'rejected', 'selected')",
            name="ck_job_matches_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    keyword_hits: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="new", nullable=False, index=True)
    batch_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    matched_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)
    gap_severity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ats_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_assessment_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
