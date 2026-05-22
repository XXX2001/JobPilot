from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


def _now():
    return datetime.utcnow()


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_match_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    method: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


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
    application_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
