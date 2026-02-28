from __future__ import annotations

from typing import Optional
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


def _now():
    return datetime.utcnow()


class TailoredDocument(Base):
    __tablename__ = "tailored_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_match_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    tex_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    diff_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    llm_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
