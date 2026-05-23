from __future__ import annotations

import logging
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Type, TypeVar

import re

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import DBSession
from backend.config import DATA_DIR, PROJECT_ROOT, settings
from backend.models.job import JobSource
from backend.models.user import SearchSettings, SiteCredential, UserProfile
from backend.scraping.site_prompts import SITE_CONFIGS

_T = TypeVar("_T")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


logger = logging.getLogger(__name__)


async def _upsert_singleton(
    db: AsyncSession,
    model_cls: Type[_T],
    row_id: int,
    body: BaseModel,
    defaults: dict[str, Any],
) -> _T:
    """Fetch-or-create a singleton DB row, then apply only the fields present in *body*.

    Uses ``body.model_dump(exclude_unset=True)`` — the standard Pydantic v2
    PATCH-like pattern — so fields absent from the JSON request body are never
    written and cannot clobber existing DB values (F-Q4 bug class).  Fields
    explicitly sent as ``null`` *are* included because they are set; this
    correctly supports clearing nullable columns (e.g. ``max_job_age_days``).

    ``defaults`` provides the create-path column values for fields whose
    first-run value differs from None/falsy (e.g. ``daily_limit=10``).
    On update, only the fields present in *body* are applied — defaults
    are ignored.
    """
    stmt = select(model_cls).where(model_cls.id == row_id)  # type: ignore[attr-defined]
    result = await db.execute(stmt)
    row: _T | None = result.scalar_one_or_none()

    updates = body.model_dump(exclude_unset=True)

    if row is None:
        # Merge defaults with whatever was sent in the body (body wins).
        merged = {**defaults, **updates, "id": row_id}
        row = model_cls(**merged)  # type: ignore[call-arg]
        db.add(row)
    else:
        for field, value in updates.items():
            setattr(row, field, value)
        row.updated_at = _utc_now()  # type: ignore[attr-defined]

    await db.commit()
    await db.refresh(row)  # type: ignore[arg-type]
    return row

router = APIRouter(prefix="/api/settings", tags=["settings"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    email: str
    phone: Optional[str]
    location: Optional[str]
    linkedin_url: Optional[str]
    driver_license: Optional[str]
    mobility: Optional[str]
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
    linkedin_url: Optional[str] = None
    driver_license: Optional[str] = None
    mobility: Optional[str] = None
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
    min_match_score: float
    countries: Optional[dict] = None
    cv_modification_sensitivity: str = "balanced"
    cv_tailoring_enabled: bool = True
    max_results_per_source: int = 20
    max_job_age_days: Optional[int] = None


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
    min_match_score: Optional[float] = None
    countries: Optional[dict] = None
    cv_modification_sensitivity: Optional[Literal["conservative", "balanced", "aggressive"]] = None
    cv_tailoring_enabled: Optional[bool] = None
    max_results_per_source: Optional[int] = None
    max_job_age_days: Optional[int] = None


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


class CvUploadResponse(BaseModel):
    path: str
    filename: str
    size_bytes: int

class SourceProviderStatus(BaseModel):
    configured: bool
    app_id_hint: Optional[str] = None


class GeminiProviderStatus(BaseModel):
    configured: bool


class SourcesOut(BaseModel):
    adzuna: SourceProviderStatus
    gemini: GeminiProviderStatus


class SourcesUpdateResponse(BaseModel):
    message: str
    env_file: str


class SiteToggleResponse(BaseModel):
    name: str
    enabled: bool


class CredentialSaveResponse(BaseModel):
    site_name: str
    saved: bool


class SessionClearResponse(BaseModel):
    cleared: bool


class CustomSiteDeleteResponse(BaseModel):
    deleted: int


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
            linkedin_url=None,
            driver_license=None,
            mobility=None,
            base_cv_path=None,
            base_letter_path=None,
            additional_info=None,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
    return ProfileOut.model_validate(profile)


@router.put("/profile", response_model=ProfileOut)
async def update_profile(body: ProfileUpdate, db: DBSession):
    """Create or update the user profile (upsert, id=1)."""
    profile = await _upsert_singleton(
        db,
        UserProfile,
        row_id=1,
        body=body,
        defaults={"full_name": body.full_name or "", "email": body.email or ""},
    )
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
    # daily_limit default matches DailyLimitGuard.__init__ default (10).
    ss = await _upsert_singleton(
        db,
        SearchSettings,
        row_id=1,
        body=body,
        defaults={
            "keywords": {"include": []},
            "remote_only": False,
            "daily_limit": 10,
            "min_match_score": 30.0,
            "cv_modification_sensitivity": "balanced",
            "cv_tailoring_enabled": True,
            "max_results_per_source": 20,
        },
    )
    logger.info("Search settings updated")
    return SearchSettingsOut.model_validate(ss)


@router.get("/sources", response_model=SourcesOut)
async def get_sources() -> SourcesOut:
    """Return which API sources are configured (keys masked)."""
    adzuna_app_id = settings.ADZUNA_APP_ID
    adzuna_id_set = settings.is_configured("ADZUNA_APP_ID")
    adzuna_key_set = settings.is_configured("ADZUNA_APP_KEY")
    gemini_key_set = settings.is_configured("GOOGLE_API_KEY")
    return SourcesOut(
        adzuna=SourceProviderStatus(
            configured=bool(adzuna_id_set and adzuna_key_set),
            app_id_hint=(
                (adzuna_app_id[:4] + "****") if adzuna_id_set else None
            ),
        ),
        gemini=GeminiProviderStatus(
            configured=gemini_key_set,
        ),
    )


@router.put("/sources", response_model=SourcesUpdateResponse)
async def update_sources(body: SourcesUpdate) -> SourcesUpdateResponse:
    """Placeholder: sources are configured via .env file — this route returns guidance."""
    del body  # validated but not persisted; sources live in .env
    return SourcesUpdateResponse(
        message=(
            "API keys must be set in the .env file at the project root. "
            "Edit ADZUNA_APP_ID, ADZUNA_APP_KEY, and GOOGLE_API_KEY then restart the server."
        ),
        env_file=".env",
    )


@router.get("/status", response_model=SetupStatus)
async def get_setup_status(db: DBSession):
    """Return setup completeness flags."""
    gemini_key_set = settings.is_configured("GOOGLE_API_KEY")
    adzuna_key_set = settings.is_configured("ADZUNA_APP_ID") and settings.is_configured(
        "ADZUNA_APP_KEY"
    )
    tectonic_name = "tectonic.exe" if platform.system() == "Windows" else "tectonic"
    tectonic_found = (PROJECT_ROOT / "bin" / tectonic_name).exists() or shutil.which(
        "tectonic"
    ) is not None

    # Check if user has uploaded a base CV
    stmt = select(UserProfile).where(UserProfile.id == 1)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    base_cv_uploaded = False
    if profile and profile.base_cv_path:
        cv_p = Path(profile.base_cv_path)
        resolved = cv_p if cv_p.is_absolute() else DATA_DIR / cv_p
        base_cv_uploaded = resolved.exists()

    if not base_cv_uploaded:
        templates_dir = DATA_DIR / "templates"
        base_cv_uploaded = any(templates_dir.glob("*.tex"))

    setup_complete = gemini_key_set and adzuna_key_set and base_cv_uploaded

    return SetupStatus(
        gemini_key_set=gemini_key_set,
        adzuna_key_set=adzuna_key_set,
        tectonic_found=bool(tectonic_found),
        base_cv_uploaded=base_cv_uploaded,
        setup_complete=setup_complete,
    )


_ALLOWED_CV_EXTENSIONS = {".tex", ".cls"}
_MAX_CV_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB

def _sanitize_cv_filename(raw_filename: str) -> str:
    """Slug a user-supplied filename, preserving extension.

    Raises ValueError if the name contains path-traversal sequences before
    or after slugging, or if the slugged stem is empty.
    """
    # Defence layer 1: reject any input containing '..' or a directory separator
    if ".." in raw_filename or "/" in raw_filename or "\\" in raw_filename:
        raise ValueError("Path traversal detected in filename")

    p = Path(raw_filename)
    stem = p.stem
    suffix = p.suffix.lower()  # normalise extension case

    # Slug the stem: replace anything that is not alphanumeric / . / - / _
    slug = re.sub(r"[^a-zA-Z0-9._\-]", "_", stem)
    if not slug.replace("_", ""):
        # stem is all underscores (was all special chars) — reject
        raise ValueError("Filename is empty after sanitisation")

    return slug + suffix


@router.post("/profile/cv-upload", response_model=CvUploadResponse)
async def upload_cv(db: DBSession, file: UploadFile = File(...)) -> CvUploadResponse:
    """Accept a multipart .tex/.cls upload, persist it, and update UserProfile.base_cv_path."""
    raw_filename = file.filename or ""

    # --- Path-traversal guard (pre-slug) ---
    if ".." in raw_filename or "/" in raw_filename or "\\" in raw_filename:
        raise HTTPException(status_code=400, detail="Filename contains path-traversal sequences.")

    # --- Extension check ---
    ext = Path(raw_filename).suffix.lower()
    if ext not in _ALLOWED_CV_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_CV_EXTENSIONS))}",
        )

    # --- Read bytes (check size AFTER reading — file.size can be None) ---
    data = await file.read()
    if len(data) > _MAX_CV_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(data)} bytes). Maximum allowed size is {_MAX_CV_SIZE_BYTES} bytes (1 MB).",
        )

    # --- Sanitise filename ---
    try:
        safe_name = _sanitize_cv_filename(raw_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- Write to data/templates/ ---
    templates_dir = DATA_DIR / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    dest = templates_dir / safe_name

    # Defence layer 2: verify resolved path is a descendant of templates_dir
    try:
        dest.resolve().relative_to(templates_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Resolved path escapes templates directory.")

    # --- Atomic write: write to a .tmp file first; rename only after DB commit ---
    dest_tmp = dest.with_suffix(dest.suffix + ".tmp")
    dest_tmp.write_bytes(data)

    # --- Update UserProfile.base_cv_path (relative to data_dir) ---
    relative_path = str(dest.relative_to(DATA_DIR))
    stmt = select(UserProfile).where(UserProfile.id == 1)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(
            id=1,
            full_name="",
            email="",
            base_cv_path=relative_path,
        )
        db.add(profile)
    else:
        profile.base_cv_path = relative_path
        profile.updated_at = _utc_now()

    try:
        await db.commit()
    except Exception:
        dest_tmp.unlink(missing_ok=True)
        raise
    else:
        dest_tmp.rename(dest)

    logger.info("CV uploaded: path=%s size=%d", dest, len(data))

    return CvUploadResponse(
        path=relative_path,
        filename=safe_name,
        size_bytes=len(data),
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _mask_email(email: str) -> str:
    parts = email.split("@")
    if len(parts) == 2 and len(parts[0]) >= 2:
        return parts[0][:2] + "***@" + parts[1]
    return "***"


def _has_session(site: str) -> bool:
    base = DATA_DIR
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


@router.put("/sites/{site_name}", response_model=SiteToggleResponse)
async def toggle_site(
    site_name: str, body: SiteToggle, db: DBSession
) -> SiteToggleResponse:
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
    return SiteToggleResponse(name=site_name, enabled=body.enabled)


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

                cred_key = settings.CREDENTIAL_KEY.get_secret_value()
                if cred_key:
                    f = Fernet(cred_key.encode())
                    decrypted = f.decrypt(cred_row.encrypted_email.encode()).decode()
                    masked = _mask_email(decrypted)
                else:
                    masked = "***@***"
            except Exception:
                logger.warning("Credential decryption failed for site=%s", key)
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


@router.put("/credentials/{site_name}", response_model=CredentialSaveResponse)
async def save_credential(
    site_name: str, body: CredentialUpdate, db: DBSession
) -> CredentialSaveResponse:
    """Encrypt and store email/password for a site that requires login."""
    if site_name not in SITE_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site_name}")
    if not SITE_CONFIGS[site_name].get("requires_login", False):
        raise HTTPException(status_code=400, detail=f"Site {site_name} does not require login.")
    cred_key = settings.CREDENTIAL_KEY.get_secret_value()
    if not cred_key:
        raise HTTPException(
            status_code=400,
            detail="CREDENTIAL_KEY is not set. Add a Fernet key to your .env file.",
        )

    from cryptography.fernet import Fernet

    f = Fernet(cred_key.encode())
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
        row.updated_at = _utc_now()

    await db.commit()
    return CredentialSaveResponse(site_name=site_name, saved=True)


@router.delete("/credentials/{site_name}/session", response_model=SessionClearResponse)
async def clear_session(site_name: str) -> SessionClearResponse:
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

    return SessionClearResponse(cleared=cleared)


# ─── Custom sites routes ───────────────────────────────────────────────────────


@router.get("/custom-sites", response_model=list[CustomSiteOut])
async def get_custom_sites(db: DBSession):
    """Return custom website job source entries from the DB."""
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
    """Add a custom website job source."""
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


@router.delete("/custom-sites/{site_id}", response_model=CustomSiteDeleteResponse)
async def delete_custom_site(site_id: int, db: DBSession) -> CustomSiteDeleteResponse:
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
    return CustomSiteDeleteResponse(deleted=site_id)
