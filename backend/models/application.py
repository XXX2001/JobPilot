from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base
from backend.utils.time import naive_utc_now


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'applied', 'cancelled', 'failed', "
            "'interview', 'offer', 'rejected')",
            name="ck_applications_status",
        ),
        CheckConstraint(
            "method IN ('auto', 'assisted', 'manual')",
            name="ck_applications_method",
        ),
        # A non-manual application is always created from a concrete match
        # (the auto/assisted apply pipelines). Only the manual-apply path
        # legitimately records an application without a ``job_match_id``.
        CheckConstraint(
            "method = 'manual' OR job_match_id IS NOT NULL",
            name="ck_applications_job_match_required",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_match_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("job_matches.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    method: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now, index=True)
    last_correspondence_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        Index(
            "ix_application_events_application_id_event_date",
            "application_id",
            "event_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)
