"""FastAPI routes for /api/settings (T15 - user profile and search settings)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.config import settings
from backend.models.user import SearchSettings, UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class ProfileOut(BaseModel):
    id: int
    full_name: str
    email: str
    phone: Optional[str]
    location: Optional[str]
    base_cv_path: Optional[str]
    base_letter_path: Optional[str]
    additional_info: Optional[dict]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    base_cv_path: Optional[str] = None
    base_letter_path: Optional[str] = None
    additional_info: Optional[dict] = None


class SearchSettingsOut(BaseModel):
    id: int
    keywords: dict
    excluded_keywords: Optional[dict]
    locations: Optional[dict]
    salary_min: Optional[int]
    experience_min: Optional[int]
    experience_max: Optional[int]
    remote_only: bool
    job_types: Optional[dict]
    languages: Optional[dict]
    excluded_companies: Optional[dict]
    daily_limit: int
    batch_time: str
    min_match_score: float

    class Config:
        from_attributes = True


class SearchSettingsUpdate(BaseModel):
    keywords: Optional[dict] = None
    excluded_keywords: Optional[dict] = None
    locations: Optional[dict] = None
    salary_min: Optional[int] = None
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    remote_only: Optional[bool] = None
    job_types: Optional[dict] = None
    languages: Optional[dict] = None
    excluded_companies: Optional[dict] = None
    daily_limit: Optional[int] = None
    batch_time: Optional[str] = None
    min_match_score: Optional[float] = None


class SourcesUpdate(BaseModel):
    adzuna_app_id: Optional[str] = None
    adzuna_app_key: Optional[str] = None
    google_api_key: Optional[str] = None


class SetupStatus(BaseModel):
    gemini_key_set: bool
    adzuna_key_set: bool
    tectonic_found: bool
    base_cv_uploaded: bool
    setup_complete: bool


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/profile", response_model=ProfileOut)
async def get_profile(db: DBSession):
    """Get the user profile (singleton, id=1)."""
    stmt = select(UserProfile).where(UserProfile.id == 1)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="User profile not found. Please complete setup first.",
        )
    return ProfileOut.model_validate(profile)


@router.put("/profile", response_model=ProfileOut)
async def update_profile(body: ProfileUpdate, db: DBSession):
    """Create or update the user profile (upsert, id=1)."""
    stmt = select(UserProfile).where(UserProfile.id == 1)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        # Create with required fields
        full_name = body.full_name or ""
        email = body.email or ""
        profile = UserProfile(
            id=1,
            full_name=full_name,
            email=email,
        )
        db.add(profile)
    else:
        if body.full_name is not None:
            profile.full_name = body.full_name
        if body.email is not None:
            profile.email = body.email
        if body.phone is not None:
            profile.phone = body.phone
        if body.location is not None:
            profile.location = body.location
        if body.base_cv_path is not None:
            profile.base_cv_path = body.base_cv_path
        if body.base_letter_path is not None:
            profile.base_letter_path = body.base_letter_path
        if body.additional_info is not None:
            profile.additional_info = body.additional_info
        profile.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(profile)
    logger.info("Profile updated for user id=1")
    return ProfileOut.model_validate(profile)


@router.get("/search", response_model=SearchSettingsOut)
async def get_search_settings(db: DBSession):
    """Get current search settings (singleton, id=1)."""
    stmt = select(SearchSettings).where(SearchSettings.id == 1)
    result = await db.execute(stmt)
    ss = result.scalar_one_or_none()

    if ss is None:
        raise HTTPException(
            status_code=404,
            detail="Search settings not found. Please complete setup first.",
        )
    return SearchSettingsOut.model_validate(ss)


@router.put("/search", response_model=SearchSettingsOut)
async def update_search_settings(body: SearchSettingsUpdate, db: DBSession):
    """Create or update search settings (upsert, id=1)."""
    stmt = select(SearchSettings).where(SearchSettings.id == 1)
    result = await db.execute(stmt)
    ss = result.scalar_one_or_none()

    if ss is None:
        ss = SearchSettings(
            id=1,
            keywords=body.keywords or {"include": []},
            excluded_keywords=body.excluded_keywords,
            locations=body.locations,
            salary_min=body.salary_min,
            experience_min=body.experience_min,
            experience_max=body.experience_max,
            remote_only=body.remote_only if body.remote_only is not None else False,
            job_types=body.job_types,
            languages=body.languages,
            excluded_companies=body.excluded_companies,
            daily_limit=body.daily_limit if body.daily_limit is not None else 10,
            batch_time=body.batch_time or "08:00",
            min_match_score=body.min_match_score if body.min_match_score is not None else 30.0,
        )
        db.add(ss)
    else:
        if body.keywords is not None:
            ss.keywords = body.keywords
        if body.excluded_keywords is not None:
            ss.excluded_keywords = body.excluded_keywords
        if body.locations is not None:
            ss.locations = body.locations
        if body.salary_min is not None:
            ss.salary_min = body.salary_min
        if body.experience_min is not None:
            ss.experience_min = body.experience_min
        if body.experience_max is not None:
            ss.experience_max = body.experience_max
        if body.remote_only is not None:
            ss.remote_only = body.remote_only
        if body.job_types is not None:
            ss.job_types = body.job_types
        if body.languages is not None:
            ss.languages = body.languages
        if body.excluded_companies is not None:
            ss.excluded_companies = body.excluded_companies
        if body.daily_limit is not None:
            ss.daily_limit = body.daily_limit
        if body.batch_time is not None:
            ss.batch_time = body.batch_time
        if body.min_match_score is not None:
            ss.min_match_score = body.min_match_score

    await db.commit()
    await db.refresh(ss)
    logger.info("Search settings updated")
    return SearchSettingsOut.model_validate(ss)


@router.get("/sources")
async def get_sources():
    """Return which API sources are configured (keys masked)."""
    return {
        "adzuna": {
            "configured": bool(
                getattr(settings, "ADZUNA_APP_ID", "") not in (None, "", "placeholder")
                and getattr(settings, "ADZUNA_APP_KEY", "") not in (None, "", "placeholder")
            ),
            "app_id_hint": (
                (settings.ADZUNA_APP_ID[:4] + "****")
                if getattr(settings, "ADZUNA_APP_ID", "") not in (None, "", "placeholder")
                else None
            ),
        },
        "gemini": {
            "configured": bool(
                getattr(settings, "GOOGLE_API_KEY", "") not in (None, "", "placeholder")
            ),
        },
    }


@router.put("/sources")
async def update_sources(body: SourcesUpdate):
    """Placeholder: sources are configured via .env file — this route returns guidance."""
    # API keys are NOT stored in the DB per spec. Guide user to .env.
    return {
        "message": (
            "API keys must be set in the .env file at the project root. "
            "Edit ADZUNA_APP_ID, ADZUNA_APP_KEY, and GOOGLE_API_KEY then restart the server."
        ),
        "env_file": ".env",
    }


@router.get("/status", response_model=SetupStatus)
async def get_setup_status(db: DBSession):
    """Return setup completeness flags."""
    gemini_key_set = bool(getattr(settings, "GOOGLE_API_KEY", "") not in (None, "", "placeholder"))
    adzuna_key_set = bool(
        getattr(settings, "ADZUNA_APP_ID", "") not in (None, "", "placeholder")
        and getattr(settings, "ADZUNA_APP_KEY", "") not in (None, "", "placeholder")
    )
    tectonic_found = Path("bin/tectonic").exists() or shutil.which("tectonic") is not None

    # Check if user has uploaded a base CV
    stmt = select(UserProfile).where(UserProfile.id == 1)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    base_cv_uploaded = False
    if profile and profile.base_cv_path:
        base_cv_uploaded = Path(profile.base_cv_path).exists()

    setup_complete = gemini_key_set and adzuna_key_set and base_cv_uploaded

    return SetupStatus(
        gemini_key_set=gemini_key_set,
        adzuna_key_set=adzuna_key_set,
        tectonic_found=bool(tectonic_found),
        base_cv_uploaded=base_cv_uploaded,
        setup_complete=setup_complete,
    )
