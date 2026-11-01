from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import exists, func, select

from backend.api.deps import DBSession
from backend.applier import (
    LEGACY_APPLIED_ALIASES,
    STATUS_APPLIED,
    STATUS_AWAITING_SUBMISSION,
    STATUS_NEEDS_VERIFICATION,
)
from backend.applier.daily_limit import COUNTABLE_STATUSES
from backend.applier.manual_apply import ApplicationResult
from backend.config import DATA_DIR
from backend.utils.time import utc_now
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
    # W06: "destination" | "user" | "legacy_unknown" for applied rows.
    confirmation_source: Optional[str] = None
    confirmation_evidence: Optional[str] = None
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


# Module-level alias so create/update share the same lifecycle vocabulary.
# Changing this list updates both endpoints in lockstep — preventing the
# previous drift where PATCH accepted arbitrary strings and create rejected
# them (deep-dive HIGH-2 in
# docs/reports/2026-05-23-codebase-deep-dive/01-app-shell-and-api.md).
ApplicationStatus = Literal[
    "pending", "applied", "cancelled", "failed", "awaiting_submission",
    "needs_verification", "interview", "offer", "rejected",
]


class CreateApplicationRequest(BaseModel):
    job_match_id: Optional[int] = None
    method: Literal["auto", "assisted", "manual"] = "manual"
    status: ApplicationStatus = "pending"
    notes: Optional[str] = None


class UpdateApplicationRequest(BaseModel):
    status: Optional[ApplicationStatus] = None
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
        "follow_up_due",
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
    needs_follow_up: Optional[bool] = Query(None),
):
    """List applications with optional status filter and pagination.

    When ``needs_follow_up=true`` is supplied the response is restricted to
    applications that have an *open* ``follow_up_due`` event — i.e. the event
    exists AND no subsequent ``follow_up`` event (with a later ``event_date``)
    has been logged by the user.  A ``follow_up`` event that pre-dates the
    ``follow_up_due`` does NOT resolve it.
    """
    # Query applications with joined job data
    stmt = (
        select(Application, Job)
        .outerjoin(JobMatch, Application.job_match_id == JobMatch.id)
        .outerjoin(Job, JobMatch.job_id == Job.id)
    )
    status_filter = _expand_status_filter(status)
    if status_filter is not None:
        stmt = stmt.where(Application.status.in_(status_filter))

    # Build the follow-up filter once so it can be reused for both the data
    # query and the count query without Pyright losing track of the binding.
    needs_follow_up_filter = None
    if needs_follow_up:
        # Alias for the "outer" follow_up_due event row we're anchoring on.
        fud = ApplicationEvent.__table__.alias("fud")
        # Include application only when:
        #   1. A follow_up_due event exists for it; AND
        #   2. No follow_up event exists with event_date AFTER the follow_up_due event.
        needs_follow_up_filter = exists().where(
            fud.c.application_id == Application.id,
            fud.c.event_type == "follow_up_due",
            ~exists().where(
                ApplicationEvent.application_id == Application.id,
                ApplicationEvent.event_type == "follow_up",
                ApplicationEvent.event_date > fud.c.event_date,
            ),
        )

    if needs_follow_up_filter is not None:
        stmt = stmt.where(needs_follow_up_filter)

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
    if needs_follow_up_filter is not None:
        count_stmt = count_stmt.where(needs_follow_up_filter)
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
        if body.status == STATUS_APPLIED and app.status != STATUS_APPLIED:
            # Marking a row applied by hand is a user attestation (W06).
            app.confirmation_source = "user"
            app.applied_at = app.applied_at or utc_now()
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


@router.post("/{application_id}/confirm-submitted", response_model=ApplicationOut)
async def confirm_submitted(application_id: int, db: DBSession):
    """The user attests they submitted a prepared or unverified application (W06).

    Only ``awaiting_submission`` / ``needs_verification`` rows can be
    confirmed; the result is ``applied`` with ``confirmation_source='user'``,
    distinct from destination-verified submissions.
    """
    app = await db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")
    if app.status not in (STATUS_AWAITING_SUBMISSION, STATUS_NEEDS_VERIFICATION):
        raise HTTPException(
            status_code=409, detail=f"Application {application_id} is {app.status!r}; nothing to confirm."
        )
    app.status = STATUS_APPLIED
    app.applied_at = utc_now()
    app.confirmation_source = "user"
    db.add(ApplicationEvent(application_id=app.id, event_type="applied", details="Confirmed submitted by the user"))
    if app.job_match_id is not None:
        match = await db.get(JobMatch, app.job_match_id)
        if match is not None:
            match.status = "applied"
    await db.commit()
    return await get_application(application_id, db)


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


async def _select_documents(
    *, match_id: int, body: "ApplyRequest", profile, db, request: Request
) -> tuple[Path | None, Path | None]:
    """Resolve the CV/letter to upload. W04: what the user selected is what is uploaded.

    Explicit selection (``cv_document_id`` or ``use_base_cv``): exactly those
    documents, validated against the match, type, file and content hash; the
    letter is attached only if ``letter_document_id`` is given. Without one
    (legacy callers): the newest *valid* tailored CV/letter for the match, else
    the base CV compiled to PDF. A ``.tex`` source is never returned.
    Raises :class:`ArtifactError` with a user-facing reason.
    """
    from backend.latex.artifacts import (
        ArtifactError,
        ensure_base_cv_pdf,
        resolve_base_source,
        validate_document,
    )

    data_dir = DATA_DIR

    async def base_cv() -> Path:
        source = resolve_base_source(profile.base_cv_path if profile else None, data_dir, "*.tex")
        if source is None:
            raise ArtifactError("No base CV is configured; upload one in Settings → Profile.")
        pipeline = getattr(request.app.state, "cv_pipeline", None)
        if pipeline is None:
            raise ArtifactError("The document pipeline is not available.")
        return await ensure_base_cv_pdf(source, data_dir, pipeline.compile_base_cv)

    async def pick(doc_id: int, doc_type: str) -> Path:
        doc = await db.get(TailoredDocument, doc_id)
        return validate_document(doc, match_id=match_id, doc_type=doc_type, data_dir=data_dir)

    explicit = body.use_base_cv or body.cv_document_id is not None
    if explicit:
        if body.use_base_cv and body.cv_document_id is not None:
            raise ArtifactError("Choose either the base CV or a tailored CV, not both.")
        cv = await base_cv() if body.use_base_cv else await pick(body.cv_document_id, "cv")  # type: ignore[arg-type]
        letter = await pick(body.letter_document_id, "letter") if body.letter_document_id else None
        return cv, letter

    async def newest_valid(doc_type: str) -> Path | None:
        rows = (
            await db.execute(
                select(TailoredDocument)
                .where(TailoredDocument.job_match_id == match_id, TailoredDocument.doc_type == doc_type)
                .order_by(TailoredDocument.created_at.desc(), TailoredDocument.id.desc())
            )
        ).scalars()
        for doc in rows:
            try:
                return validate_document(doc, match_id=match_id, doc_type=doc_type, data_dir=data_dir)
            except ArtifactError as exc:
                logger.warning("Skipping document id=%d for match_id=%d: %s", doc.id, match_id, exc)
        return None

    cv = await newest_valid("cv")
    if cv is None and resolve_base_source(profile.base_cv_path if profile else None, data_dir, "*.tex"):
        cv = await base_cv()
    # No CV at all stays None (legacy manual apply just opens the job link).
    return cv, await newest_valid("letter")


class ApplyRequest(BaseModel):
    method: Literal["auto", "assisted", "manual"] = "manual"
    # W04 explicit document selection (from the review UI). Set either
    # cv_document_id (a reviewed tailored CV) or use_base_cv; the letter is
    # attached only when letter_document_id is given. Omit all three for the
    # legacy "newest valid document" behaviour.
    cv_document_id: int | None = None
    use_base_cv: bool = False
    letter_document_id: int | None = None
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


@router.get("/{job_id}/review-state")
async def get_review_state(job_id: int, request: Request) -> dict:
    """Return the cached pending-review snapshot for *job_id*.

    Lets a client that lost its WS connection re-fetch the apply_review
    payload over HTTP instead of silently losing the in-flight review.
    """
    engine = getattr(request.app.state, "apply_engine", None)
    if engine is None:
        raise HTTPException(status_code=404, detail="No apply engine")
    payload = engine.get_pending_review(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No pending review")
    return payload


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

    engine: ApplicationEngine | None = getattr(request.app.state, "apply_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="ApplicationEngine not initialised")

    try:
        mode = ApplyMode(body.method)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid apply method: {body.method!r}")

    from backend.config import settings as app_settings

    if mode == ApplyMode.AUTO and not app_settings.jobpilot_auto_submit_enabled:
        raise HTTPException(
            status_code=409,
            detail=(
                "Automatic submission is disabled: JobPilot cannot yet verify that a "
                "site accepted an application. Use the assisted method (prefill, then "
                "you submit) or set JOBPILOT_AUTO_SUBMIT_ENABLED=true to accept the risk."
            ),
        )

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

    from backend.latex.artifacts import ArtifactError

    try:
        cv_pdf, letter_pdf = await _select_documents(
            match_id=match_id, body=body, profile=profile, db=db, request=request
        )
    except ArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()  # persists hashes pinned on first use of legacy rows

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
