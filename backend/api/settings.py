"""FastAPI routes for /api/settings (T15 - user profile and search settings)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.config import settings
from backend.models.job import JobSource
from backend.models.user import SearchSettings, SiteCredential, UserProfile
from backend.scraping.site_prompts import SITE_CONFIGS

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    base_cv_path: Optional[str] = None
    base_letter_path: Optional[str] = None
    additional_info: Optional[dict] = None


class SearchSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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
        return ProfileOut(
            id=0,
            full_name="",
            email="",
            phone=None,
            location=None,
            base_cv_path=None,
            base_letter_path=None,
            additional_info=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _mask_email(email: str) -> str:
    parts = email.split("@")
    if len(parts) == 2 and len(parts[0]) >= 2:
        return parts[0][:2] + "***@" + parts[1]
    return "***"


def _has_session(site: str) -> bool:
    base = Path(settings.jobpilot_data_dir)
    new_path = base / "browser_profiles" / site / "state.json"
    old_path = base / "browser_sessions" / f"{site}_state.json"
    return new_path.exists() or old_path.exists()


# ─── Pydantic schemas for new routes ──────────────────────────────────────────


class SiteOut(BaseModel):
    name: str
    display_name: str
    type: str
    requires_login: bool
    base_url: str
    enabled: bool
    has_session: bool


class SiteToggle(BaseModel):
    enabled: bool


class CredentialOut(BaseModel):
    site_name: str
    display_name: str
    masked_email: Optional[str]
    has_session: bool


class CredentialUpdate(BaseModel):
    email: str
    password: str


class CustomSiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    display_name: Optional[str]
    url: Optional[str]
    enabled: bool


class CustomSiteCreate(BaseModel):
    name: str
    url: str
    display_name: Optional[str] = None


# ─── Sites routes ─────────────────────────────────────────────────────────────


@router.get("/sites", response_model=list[SiteOut])
async def get_sites(db: DBSession):
    """Return all known job source sites with enabled state and session presence."""
    # Build a dict of DB-stored enabled states keyed by name
    stmt = select(JobSource)
    result = await db.execute(stmt)
    db_sources: dict[str, JobSource] = {row.name: row for row in result.scalars().all()}

    out = []
    for key, cfg in SITE_CONFIGS.items():
        db_row = db_sources.get(key)
        enabled = db_row.enabled if db_row is not None else True
        out.append(
            SiteOut(
                name=cfg["name"],
                display_name=cfg["display_name"],
                type=cfg["type"],
                requires_login=cfg.get("requires_login", False),
                base_url=cfg.get("base_url", ""),
                enabled=enabled,
                has_session=_has_session(key),
            )
        )
    return out


@router.put("/sites/{site_name}")
async def toggle_site(site_name: str, body: SiteToggle, db: DBSession):
    """Enable or disable a job source site."""
    if site_name not in SITE_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site_name}")

    stmt = select(JobSource).where(JobSource.name == site_name)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    cfg = SITE_CONFIGS[site_name]
    if row is None:
        row = JobSource(
            name=site_name,
            type=cfg["type"],
            url=cfg.get("base_url", ""),
            enabled=body.enabled,
        )
        db.add(row)
    else:
        row.enabled = body.enabled

    await db.commit()
    return {"name": site_name, "enabled": body.enabled}


# ─── Credentials routes ────────────────────────────────────────────────────────


@router.get("/credentials", response_model=list[CredentialOut])
async def get_credentials(db: DBSession):
    """Return sites that require login, with masked email and session status."""
    login_sites = {k: v for k, v in SITE_CONFIGS.items() if v.get("requires_login", False)}

    stmt = select(SiteCredential)
    result = await db.execute(stmt)
    creds: dict[str, SiteCredential] = {row.site_name: row for row in result.scalars().all()}

    out = []
    for key, cfg in login_sites.items():
        cred_row = creds.get(key)
        masked = None
        if cred_row and cred_row.encrypted_email:
            try:
                from cryptography.fernet import Fernet

                if settings.CREDENTIAL_KEY:
                    f = Fernet(settings.CREDENTIAL_KEY.encode())
                    decrypted = f.decrypt(cred_row.encrypted_email.encode()).decode()
                    masked = _mask_email(decrypted)
                else:
                    masked = "***@***"
            except Exception:
                masked = "***@***"
        out.append(
            CredentialOut(
                site_name=key,
                display_name=cfg["display_name"],
                masked_email=masked,
                has_session=_has_session(key),
            )
        )
    return out


@router.put("/credentials/{site_name}")
async def save_credential(site_name: str, body: CredentialUpdate, db: DBSession):
    """Encrypt and store email/password for a site that requires login."""
    if site_name not in SITE_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site_name}")
    if not SITE_CONFIGS[site_name].get("requires_login", False):
        raise HTTPException(status_code=400, detail=f"Site {site_name} does not require login.")
    if not settings.CREDENTIAL_KEY:
        raise HTTPException(
            status_code=400,
            detail="CREDENTIAL_KEY is not set. Add a Fernet key to your .env file.",
        )

    from cryptography.fernet import Fernet

    f = Fernet(settings.CREDENTIAL_KEY.encode())
    enc_email = f.encrypt(body.email.encode()).decode()
    enc_pass = f.encrypt(body.password.encode()).decode()

    stmt = select(SiteCredential).where(SiteCredential.site_name == site_name)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = SiteCredential(
            site_name=site_name,
            encrypted_email=enc_email,
            encrypted_password=enc_pass,
        )
        db.add(row)
    else:
        row.encrypted_email = enc_email
        row.encrypted_password = enc_pass
        row.updated_at = datetime.utcnow()

    await db.commit()
    return {"site_name": site_name, "saved": True}


@router.delete("/credentials/{site_name}/session")
async def clear_session(site_name: str):
    """Delete the browser session files for a site."""
    if site_name not in SITE_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site_name}")

    base = Path(settings.jobpilot_data_dir)
    profile_dir = base / "browser_profiles" / site_name
    old_path = base / "browser_sessions" / f"{site_name}_state.json"

    cleared = False
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
        cleared = True
    if old_path.exists():
        old_path.unlink()
        cleared = True

    return {"cleared": cleared}


# ─── Custom sites routes ───────────────────────────────────────────────────────


@router.get("/custom-sites", response_model=list[CustomSiteOut])
async def get_custom_sites(db: DBSession):
    """Return custom/lab_url job source entries from the DB."""
    stmt = select(JobSource).where(JobSource.type.in_(["lab_url", "custom"]))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    out: list[CustomSiteOut] = []
    for row in rows:
        cfg = row.config if isinstance(row.config, dict) else {}
        display_name = cfg.get("display_name") or row.name
        out.append(
            CustomSiteOut(
                id=row.id,
                name=row.name,
                display_name=display_name,
                url=row.url,
                enabled=row.enabled,
            )
        )
    return out


@router.post("/custom-sites", response_model=CustomSiteOut)
async def add_custom_site(body: CustomSiteCreate, db: DBSession):
    """Add a custom lab/URL job source."""
    row = JobSource(
        name=body.name,
        type="lab_url",
        url=body.url,
        config={"display_name": body.display_name or body.name},
        enabled=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CustomSiteOut(
        id=row.id,
        name=row.name,
        display_name=body.display_name or body.name,
        url=row.url,
        enabled=row.enabled,
    )


@router.delete("/custom-sites/{site_id}")
async def delete_custom_site(site_id: int, db: DBSession):
    """Delete a custom site by ID."""
    stmt = select(JobSource).where(
        JobSource.id == site_id, JobSource.type.in_(["lab_url", "custom"])
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Custom site not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": site_id}
