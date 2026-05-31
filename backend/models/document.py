from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
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


class TailoredDocument(Base):
    __tablename__ = "tailored_documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('cv', 'letter')",
            name="ck_tailored_documents_doc_type",
        ),
        # Document lookups by match + type + recency
        # (``backend/api/documents.py``) are served by this covering index.
        Index(
            "ix_tailored_documents_match_doc_created",
            "job_match_id",
            "doc_type",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("job_matches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    tex_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    diff_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    llm_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now, index=True)
