from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


def _now():
    return datetime.utcnow()


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    driver_license: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mobility: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    base_cv_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    base_letter_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    additional_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SearchSettings(Base):
    __tablename__ = "search_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    keywords: Mapped[dict] = mapped_column(JSON, nullable=False)
    excluded_keywords: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    locations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    experience_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    experience_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    remote_only: Mapped[bool] = mapped_column(nullable=False, default=False)
    job_types: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    languages: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    excluded_companies: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    batch_time: Mapped[str] = mapped_column(String, default="08:00")
    min_match_score: Mapped[float] = mapped_column(nullable=False, default=30.0)
    countries: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    cv_modification_sensitivity: Mapped[str] = mapped_column(
        String, default="balanced", nullable=False
    )


class SiteCredential(Base):
    __tablename__ = "site_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    encrypted_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    encrypted_password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
