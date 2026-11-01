"""Durable background operations (W05 minimal slice; W15 adds leases/retries)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base
from backend.utils.time import naive_utc_now

ACTIVE_STATES = ("queued", "running")


class DocumentOperation(Base):
    """One accepted document-generation request.

    Persisted *before* the HTTP response so accepted work survives a restart.
    At most one active (queued/running) operation exists per (kind, match):
    the partial unique index makes duplicate requests converge on one row.
    """

    __tablename__ = "document_operations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_document_operations_state",
        ),
        CheckConstraint("kind IN ('regenerate_cv')", name="ck_document_operations_kind"),
        Index(
            "uq_document_operations_active",
            "kind",
            "job_match_id",
            unique=True,
            sqlite_where=text("state IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    job_match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("job_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    # Hash of the inputs (base CV source + job content). An unforced request
    # with an unchanged version reuses the last succeeded operation.
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tailored_documents.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=naive_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=naive_utc_now, onupdate=naive_utc_now
    )
