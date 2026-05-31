from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base
from backend.utils.time import naive_utc_now


class GmailCredential(Base):
    """OAuth refresh token + sync cursor for one linked inbox.

    Phase 1 ships single-account; the unique key is `email_address` so a
    future multi-account release needs no migration. Refresh tokens are
    Fernet-encrypted at rest with CREDENTIAL_KEY (mirrors SiteCredential).
    Access tokens are NEVER persisted — held in-memory by GmailTokenManager.
    """

    __tablename__ = "gmail_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_address: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String, nullable=False)
    history_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)


class GmailMessage(Base):
    """Cached metadata for one observed Gmail message. Body NOT persisted."""

    __tablename__ = "gmail_messages"
    __table_args__ = (
        Index("ix_gmail_messages_account_received",
              "account_email", "received_at"),
        CheckConstraint(
            "category IS NULL OR category IN ('noise', 'rejection', 'offer', "
            "'interview_invite', 'ats_ack', 'unknown')",
            name="ck_gmail_messages_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    account_email: Mapped[str] = mapped_column(String, nullable=False)
    from_address: Mapped[str] = mapped_column(String, nullable=False)
    from_domain: Mapped[str] = mapped_column(String, index=True, nullable=False)
    to_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)

    # Phase 1 classification (heuristic-only)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    category_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classified_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ats_vendor: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Phase 2 enrichment fields — declared now so we don't migrate twice;
    # all remain NULL in Phase 1.
    extracted_company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_interview_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_salary_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_questions_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)


class ApplicationCorrespondence(Base):
    """Association object linking an Application to a GmailMessage with link-quality metadata."""

    __tablename__ = "application_correspondence"
    __table_args__ = (
        Index("ix_application_correspondence_app_created",
              "application_id", "created_at"),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_application_correspondence_direction",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gmail_messages.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "inbound" | "outbound"
    link_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    link_method: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=naive_utc_now)
