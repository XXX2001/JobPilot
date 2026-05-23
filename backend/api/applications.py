from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.applier import LEGACY_APPLIED_ALIASES, STATUS_APPLIED
from backend.applier.daily_limit import COUNTABLE_STATUSES
from backend.applier.manual_apply import ApplicationResult
from backend.defaults import DAILY_LIMIT
from backend.models.application import Application, ApplicationEvent
from backend.models.document import TailoredDocument
from backend.models.job import Job, JobMatch
from backend.models.user import SearchSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applications", tags=["applications"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class ApplicationEventOut(BaseModel):
    id: int
    application_id: int
    event_type: str
    details: Optional[str]
    event_date: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationOut(BaseModel):
    id: int
    job_match_id: Optional[int]
    method: str
    status: str
    applied_at: Optional[datetime]
    notes: Optional[str]
    error_log: Optional[str]
    created_at: datetime
    events: list[ApplicationEventOut] = []
    # Denormalized job fields for display
    job_title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ApplicationListOut(BaseModel):
    applications: list[ApplicationOut]
    total: int


class CreateApplicationRequest(BaseModel):
    job_match_id: Optional[int] = None
    method: Literal["auto", "assisted", "manual"] = "manual"
    status: Literal[
        "pending", "applied", "cancelled", "failed", "interview", "offer", "rejected"
    ] = "pending"
    notes: Optional[str] = None


class UpdateApplicationRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    error_log: Optional[str] = None


class CreateEventRequest(BaseModel):
    event_type: Literal[
        "pending",
        "applied",
        "cancelled",
        "failed",
        "interview",
        "offer",
        "rejected",
        "viewed",
        "follow_up",
    ]
    details: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


class LimitStatusOut(BaseModel):
    used: int
    limit: int
    resets_at: str  # ISO 8601 UTC datetime of next midnight


def _expand_status_filter(status: Optional[str]) -> Optional[list[str]]:
    """Translate a user-supplied status filter into the set of DB values
    that should match.

    Filtering by :data:`STATUS_APPLIED` also matches rows persisted under
    the legacy aliases ``"manual"`` / ``"assisted"`` (see
    ``backend.applier`` module docstring for the backward-compatibility
    policy). All other values are matched verbatim. Returns ``None`` if
    no filter was supplied.
    """
    if not status:
        return None
    if status == STATUS_APPLIED:
        return [STATUS_APPLIED, *sorted(LEGACY_APPLIED_ALIASES)]
    return [status]


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(body: CreateApplicationRequest, db: DBSession):
    """Create a new application record."""
    app = Application(
        job_match_id=body.job_match_id,
        method=body.method,
        status=body.status,
        notes=body.notes,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    logger.info("Created application id=%d method=%s", app.id, app.method)
    return ApplicationOut.model_validate(app)


@router.get("", response_model=ApplicationListOut)
async def list_applications(
    db: DBSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
):
    """List applications with optional status filter and pagination."""
    # Query applications with joined job data
    stmt = (
        select(Application, Job)
        .outerjoin(JobMatch, Application.job_match_id == JobMatch.id)
        .outerjoin(Job, JobMatch.job_id == Job.id)
    )
    status_filter = _expand_status_filter(status)
    if status_filter is not None:
        stmt = stmt.where(Application.status.in_(status_filter))
    stmt = stmt.order_by(Application.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    # Batch-fetch events (avoids N+1)
    app_ids = [app.id for app, _ in rows]
    if app_ids:
        events_stmt = (
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id.in_(app_ids))
            .order_by(ApplicationEvent.application_id, ApplicationEvent.event_date.asc())
        )
        events_result = await db.execute(events_stmt)
        all_events = events_result.scalars().all()

        from collections import defaultdict

        events_by_app: dict[int, list] = defaultdict(list)
        for event in all_events:
            events_by_app[event.application_id].append(event)
    else:
        events_by_app = {}

    app_outs: list[ApplicationOut] = []
    for app, job in rows:
        out = ApplicationOut.model_validate(app)
        out.events = [ApplicationEventOut.model_validate(e) for e in events_by_app.get(app.id, [])]
        if job is not None:
            out.job_title = job.title
            out.company = job.company
            out.location = job.location
            out.url = job.url
        app_outs.append(out)

    # Total count (with same filter)
    count_stmt = select(func.count()).select_from(Application)
    if status_filter is not None:
        count_stmt = count_stmt.where(Application.status.in_(status_filter))
    total = (await db.execute(count_stmt)).scalar_one()

    return ApplicationListOut(applications=app_outs, total=total)


@router.get("/limit-status", response_model=LimitStatusOut)
async def get_limit_status(db: DBSession) -> LimitStatusOut:
    """Return today's application usage against the configured daily limit.

    Reads the same counter as :class:`~backend.applier.daily_limit.DailyLimitGuard`:
    Application rows whose ``applied_at`` is today (UTC date) and whose
    ``status`` is in ``{"applied", "pending"}`` (plus legacy aliases).

    The ``daily_limit`` is read from :class:`~backend.models.user.SearchSettings`
    row id=1; falls back to :data:`~backend.defaults.DAILY_LIMIT` (10) if
    no settings row exists.

    ``resets_at`` is the next UTC midnight in ISO 8601 format.
    """
    # ── Resolve the configured limit ──────────────────────────────────────────
    ss_result = await db.execute(select(SearchSettings).where(SearchSettings.id == 1))
    ss = ss_result.scalar_one_or_none()
    limit = (ss.daily_limit if ss is not None else None) or DAILY_LIMIT

    # ── Count today's applications (same SQL as DailyLimitGuard.remaining_today) ──
    today = datetime.now(timezone.utc).date()
    stmt = select(func.count(Application.id)).where(
        Application.applied_at >= today,  # type: ignore[operator]
        Application.status.in_(COUNTABLE_STATUSES),
    )
    used: int = (await db.execute(stmt)).scalar_one_or_none() or 0

    # ── Compute next UTC midnight ──────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    resets_at = datetime(
        now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc
    ) + timedelta(days=1)

    return LimitStatusOut(used=used, limit=limit, resets_at=resets_at.isoformat())


@router.get("/{application_id}", response_model=ApplicationOut)
async def get_application(application_id: int, db: DBSession):
    """Get a single application with its lifecycle events."""
    stmt = (
        select(Application, Job)
        .outerjoin(JobMatch, Application.job_match_id == JobMatch.id)
        .outerjoin(Job, JobMatch.job_id == Job.id)
        .where(Application.id == application_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")

    app, job = row

    events_stmt = (
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.event_date.asc())
    )
    events_result = await db.execute(events_stmt)
    events = events_result.scalars().all()

    out = ApplicationOut.model_validate(app)
    out.events = [ApplicationEventOut.model_validate(e) for e in events]
    if job is not None:
        out.job_title = job.title
        out.company = job.company
        out.location = job.location
        out.url = job.url
    return out


@router.patch("/{application_id}", response_model=ApplicationOut)
async def update_application(application_id: int, body: UpdateApplicationRequest, db: DBSession):
    """Update application status, notes, or error log."""
    stmt = select(Application).where(Application.id == application_id)
    result = await db.execute(stmt)
    app = result.scalar_one_or_none()

    if app is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")

    if body.status is not None:
        app.status = body.status
    if body.notes is not None:
        app.notes = body.notes
    if body.applied_at is not None:
        app.applied_at = body.applied_at
    if body.error_log is not None:
        app.error_log = body.error_log

    await db.commit()
    await db.refresh(app)
    logger.info("Updated application id=%d status=%s", app.id, app.status)

    # Fetch job data for response
    job = None
    if app.job_match_id is not None:
        job_stmt = (
            select(Job)
            .join(JobMatch, JobMatch.job_id == Job.id)
            .where(JobMatch.id == app.job_match_id)
        )
        job_result = await db.execute(job_stmt)
        job = job_result.scalar_one_or_none()

    # Return with events
    events_stmt = (
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.event_date.asc())
    )
    events_result = await db.execute(events_stmt)
    events = events_result.scalars().all()

    out = ApplicationOut.model_validate(app)
    out.events = [ApplicationEventOut.model_validate(e) for e in events]
    if job is not None:
        out.job_title = job.title
        out.company = job.company
        out.location = job.location
        out.url = job.url
    return out


@router.post("/{application_id}/events", response_model=ApplicationEventOut, status_code=201)
async def add_application_event(application_id: int, body: CreateEventRequest, db: DBSession):
    """Add a lifecycle event to an application."""
    # Verify application exists
    stmt = select(Application).where(Application.id == application_id)
    result = await db.execute(stmt)
    app = result.scalar_one_or_none()

    if app is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")

    event = ApplicationEvent(
        application_id=application_id,
        event_type=body.event_type,
        details=body.details,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    logger.info("Added event type=%s to application id=%d", body.event_type, application_id)
    return ApplicationEventOut.model_validate(event)


# ─── Apply endpoint ───────────────────────────────────────────────────────────


async def _resolve_documents(match_id: int, db) -> tuple[Path | None, Path | None]:
    """Return (cv_pdf, letter_pdf) Paths for the latest tailored docs for match_id.

    Returns (None, None) if no documents have been generated yet.
    Note: engine.apply() already accepts cv_pdf/letter_pdf — no changes to engine.py needed.
    """
    cv_path: Path | None = None
    letter_path: Path | None = None

    cv_stmt = (
        select(TailoredDocument)
        .where(
            TailoredDocument.job_match_id == match_id,
            TailoredDocument.doc_type == "cv",
        )
        .order_by(TailoredDocument.created_at.desc())
        .limit(1)
    )
    cv_row = (await db.execute(cv_stmt)).scalar_one_or_none()
    if cv_row and cv_row.pdf_path:
        cv_path = Path(cv_row.pdf_path)

    letter_stmt = (
        select(TailoredDocument)
        .where(
            TailoredDocument.job_match_id == match_id,
            TailoredDocument.doc_type == "letter",
        )
        .order_by(TailoredDocument.created_at.desc())
        .limit(1)
    )
    letter_row = (await db.execute(letter_stmt)).scalar_one_or_none()
    if letter_row and letter_row.pdf_path:
        letter_path = Path(letter_row.pdf_path)

    return cv_path, letter_path


class ApplyRequest(BaseModel):
    method: Literal["auto", "assisted", "manual"] = "manual"
    apply_url: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    additional_answers_json: str = ""

    @field_validator("apply_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("apply_url must be http or https")
        if len(v) > 2048:
            raise ValueError("apply_url too long")
        return v

    @field_validator("additional_answers_json")
    @classmethod
    def validate_json(cls, v: str) -> str:
        if v:
            import json

            try:
                json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("additional_answers_json must be valid JSON")
        return v[:5000]


@router.post("/{match_id}/apply", status_code=200, response_model=ApplicationResult)
async def apply_to_job(
    match_id: int, body: ApplyRequest, db: DBSession, request: Request
) -> ApplicationResult:
    """Trigger an application for a job match via auto / assisted / manual strategy."""
    from backend.models.user import UserProfile

    try:
        from backend.applier.engine import (  # noqa: PLC0415
            ApplicantInfo,
            ApplicationEngine,
            ApplyMode,
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="ApplicationEngine not available")

    engine: ApplicationEngine = getattr(request.app.state, "apply_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="ApplicationEngine not initialised")

    try:
        mode = ApplyMode(body.method)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid apply method: {body.method!r}")

    # ── Auto-populate applicant info from UserProfile when not provided ──
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.id == 1))
    ).scalar_one_or_none()

    # `profile.<col>` can be None when the DB row was partially filled,
    # so coerce each chain to "" rather than letting None reach
    # ApplicantInfo (whose fields are typed `str`).
    full_name = body.full_name or (profile.full_name if profile else "") or ""
    email = body.email or (profile.email if profile else "") or ""
    phone = body.phone or (profile.phone if profile else "") or ""
    location = body.location or (profile.location if profile else "") or ""
    additional_answers = body.additional_answers_json
    if not additional_answers and profile and profile.additional_info:
        import json as _json

        try:
            additional_answers = _json.dumps(profile.additional_info)
        except (TypeError, ValueError) as exc:
            # EH-03: surface bad profile data instead of silently dropping it.
            # additional_info is a JSON column but may contain non-serializable
            # objects (e.g. datetime) that slipped past upstream validation.
            logger.warning(
                "Could not serialize UserProfile.additional_info for profile_id=%s "
                "match_id=%d: %s — falling back to empty string",
                getattr(profile, "id", "?"),
                match_id,
                exc,
                exc_info=True,
            )
            additional_answers = ""

    # Inject profile fields into additional_answers so the agent can use them
    if profile:
        import json as _json2

        try:
            answers_dict = _json2.loads(additional_answers) if additional_answers else {}
        except (TypeError, ValueError, _json2.JSONDecodeError) as exc:
            # EH-03: surface malformed JSON so we can debug bad profile/credential
            # data instead of silently coercing to {} and losing custom answers.
            logger.warning(
                "Could not parse additional_answers JSON for profile_id=%s "
                "match_id=%d: %s — falling back to empty dict",
                getattr(profile, "id", "?"),
                match_id,
                exc,
                exc_info=True,
            )
            answers_dict = {}
        changed = False
        if profile.linkedin_url and "linkedin_url" not in answers_dict:
            answers_dict["linkedin_url"] = profile.linkedin_url
            changed = True
        if profile.driver_license and "driver_license" not in answers_dict:
            answers_dict["driver_license"] = profile.driver_license
            changed = True
        if profile.mobility and "mobility" not in answers_dict:
            answers_dict["mobility"] = profile.mobility
            changed = True
        if changed:
            additional_answers = _json2.dumps(answers_dict)

    # Resolve apply_url from the job if not provided in the request body
    apply_url = body.apply_url
    if not apply_url:
        job_stmt = (
            select(Job).join(JobMatch, JobMatch.job_id == Job.id).where(JobMatch.id == match_id)
        )
        job_row = (await db.execute(job_stmt)).scalar_one_or_none()
        if job_row:
            apply_url = job_row.apply_url or job_row.url or ""
        if not apply_url:
            logger.warning("No apply_url found for match_id=%d", match_id)

    applicant = ApplicantInfo(
        full_name=full_name,
        email=email,
        phone=phone,
        location=location,
        additional_answers_json=additional_answers,
    )

    # Resolve tailored CV and cover letter for this job match
    cv_pdf, letter_pdf = await _resolve_documents(match_id=match_id, db=db)

    # Fall back to base CV/letter from user profile if no tailored docs exist
    if cv_pdf is None and profile and profile.base_cv_path:
        base_cv = Path(profile.base_cv_path)
        if base_cv.exists():
            cv_pdf = base_cv
            logger.info("Using base CV as fallback: %s", cv_pdf)
    if letter_pdf is None and profile and profile.base_letter_path:
        base_letter = Path(profile.base_letter_path)
        if base_letter.exists():
            letter_pdf = base_letter
            logger.info("Using base cover letter as fallback: %s", letter_pdf)

    # Last resort: scan templates directory for a .tex-compiled PDF
    if cv_pdf is None:
        from backend.config import settings as app_settings

        templates_dir = Path(app_settings.jobpilot_data_dir) / "templates"
        if templates_dir.is_dir():
            pdf_candidates = sorted(templates_dir.glob("*.pdf"))
            if pdf_candidates:
                cv_pdf = pdf_candidates[0]
                logger.info("Using auto-detected base CV PDF: %s", cv_pdf)

    if cv_pdf:
        logger.info("Resolved cv_pdf=%s for match_id=%d", cv_pdf, match_id)
    if letter_pdf:
        logger.info("Resolved letter_pdf=%s for match_id=%d", letter_pdf, match_id)

    result = await engine.apply(
        job_match_id=match_id,
        mode=mode,
        db=db,
        apply_url=apply_url,
        applicant=applicant,
        cv_pdf=cv_pdf,
        letter_pdf=letter_pdf,
    )
    return result
