from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.api.ws_models import Status
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class QueueJobOut(BaseModel):
    id: int
    title: str
    company: str
    location: Optional[str] = None
    country: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    description: Optional[str] = None
    url: str
    apply_url: str = ""
    apply_method: str = ""
    posted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QueueMatchOut(BaseModel):
    id: int  # match ID — matches frontend's match.id
    job_id: int
    score: float
    status: str
    batch_date: Optional[date] = None
    matched_at: datetime
    job: QueueJobOut  # nested — matches frontend's match.job.title etc.


class QueueOut(BaseModel):
    matches: list[QueueMatchOut]
    total: int


class BatchStatusOut(BaseModel):
    running: bool
    last_status: Optional[Status] = None


class RefreshResponse(BaseModel):
    status: Literal["started"]
    message: str


class PreviewMatchOut(BaseModel):
    """One previewed match — mirrors the dry-run preview dict from BatchRunner."""

    title: str
    company: str
    score: float
    location: str = ""


class PreviewResponse(BaseModel):
    status: Literal["preview"]
    matches: list[PreviewMatchOut]
    total: int


class MatchStatusUpdateOut(BaseModel):
    match_id: int
    status: Literal["new", "skipped", "applying", "applied", "rejected"]


class EnrichmentResponse(BaseModel):
    status: Literal["enriched", "no_change"]
    description: str


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=QueueOut)
async def get_queue(db: DBSession):
    """Return all pending matches (status='new'), newest batch first."""
    stmt = (
        select(JobMatch, Job)
        .join(Job, Job.id == JobMatch.job_id)
        .where(JobMatch.status == "new")
        .order_by(JobMatch.batch_date.desc(), JobMatch.score.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    matches: list[QueueMatchOut] = []
    for match, job in rows:
        matches.append(
            QueueMatchOut(
                id=match.id,
                job_id=job.id,
                score=match.score,
                status=match.status,
                batch_date=match.batch_date,
                matched_at=match.matched_at,
                job=QueueJobOut(
                    id=job.id,
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    country=job.country,
                    salary_min=job.salary_min,
                    salary_max=job.salary_max,
                    description=job.description,
                    url=job.url,
                    apply_url=job.apply_url or job.url or "",
                    apply_method=job.apply_method or "",
                    posted_at=job.posted_at,
                ),
            )
        )

    return QueueOut(matches=matches, total=len(matches))


@router.get("/status", response_model=BatchStatusOut)
async def get_batch_status(request: Request) -> BatchStatusOut:
    """Return the current batch running state and last progress message."""
    runner = getattr(request.app.state, "batch_runner", None)
    if runner is None:
        return BatchStatusOut(running=False, last_status=None)
    return BatchStatusOut(
        running=runner.running,
        last_status=runner.last_status,
    )


@router.get("/source-health")
async def get_source_health(request: Request):
    """Return per-source scrape health (in-memory, lives with the orchestrator).

    Each entry is a dict with ``source``, ``status`` (one of ``healthy``,
    ``degraded``, ``down``, ``unknown``), ``last_outcome``, ``last_attempt_at``,
    ``last_success_at``, ``consecutive_failures``, ``last_error``,
    ``last_job_count``, ``total_attempts``, ``total_jobs``, and ``history``
    (rolling window of the last 5 outcomes).

    Empty list when no scrape has run since process start.
    """
    orchestrator = getattr(request.app.state, "scraping_orchestrator", None)
    if orchestrator is None:
        return {"sources": []}
    tracker = getattr(orchestrator, "source_health", None)
    if tracker is None:
        return {"sources": []}
    return {"sources": tracker.snapshot()}


@router.post("/refresh", response_model=RefreshResponse | PreviewResponse)
async def refresh_queue(
    request: Request,
    db: DBSession,  # noqa: ARG001
    dry_run: bool = Query(False),
) -> RefreshResponse | PreviewResponse:
    """Trigger a new batch run immediately (manual re-run).

    Default: runs the batch in a background task so the endpoint returns
    promptly with ``{"status": "started"}``.

    ``dry_run=true``: runs the scrape + match/rank steps INLINE (no background
    task) and returns ``{"status": "preview", "matches": [...], "total": N}``.
    A dry-run writes NOTHING to the DB and makes no Gemini calls.
    """
    import asyncio

    runner = getattr(request.app.state, "batch_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Batch runner not available")

    if runner.running:
        raise HTTPException(status_code=409, detail="A search is already in progress")

    if dry_run:
        preview = await runner.run_batch(dry_run=True)
        matches = [PreviewMatchOut(**m) for m in (preview or [])]
        return PreviewResponse(status="preview", matches=matches, total=len(matches))

    async def _run():
        try:
            await runner.run_batch()
        except Exception as exc:
            logger.error("Batch run error: %s", exc)

    asyncio.create_task(_run())
    return RefreshResponse(status="started", message="Job search triggered in background")


@router.get("/{match_id}", response_model=QueueMatchOut)
async def get_match(match_id: int, db: DBSession):
    """Return a single match with its nested job."""
    stmt = select(JobMatch, Job).join(Job, Job.id == JobMatch.job_id).where(JobMatch.id == match_id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    match, job = row
    return QueueMatchOut(
        id=match.id,
        job_id=job.id,
        score=match.score,
        status=match.status,
        batch_date=match.batch_date,
        matched_at=match.matched_at,
        job=QueueJobOut(
            id=job.id,
            title=job.title,
            company=job.company,
            location=job.location,
            country=job.country,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            description=job.description,
            url=job.url,
            apply_url=job.apply_url or job.url or "",
            apply_method=job.apply_method or "",
            posted_at=job.posted_at,
        ),
    )


@router.patch("/{match_id}/skip", response_model=MatchStatusUpdateOut)
async def skip_match(match_id: int, db: DBSession) -> MatchStatusUpdateOut:
    """Mark a queue match as skipped."""
    stmt = select(JobMatch).where(JobMatch.id == match_id)
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    match.status = "skipped"
    await db.commit()
    return MatchStatusUpdateOut(match_id=match_id, status="skipped")


class StatusUpdate(BaseModel):
    status: Literal["new", "skipped", "applying", "applied", "rejected"]


@router.patch("/{match_id}/status", response_model=MatchStatusUpdateOut)
async def update_match_status(
    match_id: int, body: StatusUpdate, db: DBSession
) -> MatchStatusUpdateOut:
    """Update the status of a queue match (new, skipped, applying, applied).

    Pydantic enforces the allowed-status vocabulary at request-validation
    time, so any out-of-range value returns 422 before reaching the handler.
    """
    stmt = select(JobMatch).where(JobMatch.id == match_id)
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    match.status = body.status
    await db.commit()
    return MatchStatusUpdateOut(match_id=match_id, status=body.status)


@router.post("/{match_id}/enrich-description", response_model=EnrichmentResponse)
async def enrich_job_description(
    match_id: int, db: DBSession, request: Request
) -> EnrichmentResponse:
    """Fetch the full job description from the job URL using Gemini.

    Used on-demand when a job's stored description is short or missing.
    """
    stmt = select(JobMatch, Job).join(Job, Job.id == JobMatch.job_id).where(JobMatch.id == match_id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    _match, job = row

    if not job.url:
        raise HTTPException(status_code=422, detail="Job has no URL to fetch description from")

    # Get the ScraplingFetcher from app state or create one
    gemini_client = getattr(request.app.state, "gemini", None)
    if gemini_client is None:
        raise HTTPException(status_code=503, detail="Gemini client not available")

    try:
        from backend.scraping.scrapling_fetcher import ScraplingFetcher

        fetcher = ScraplingFetcher(gemini_client)
        html = await fetcher.fetch_page(job.url)
        if not html:
            raise HTTPException(status_code=502, detail="Could not fetch job page")

        # Clean and extract description using Gemini
        cleaned = fetcher._clean_html(html)
        prompt = (
            "Extract the FULL job description from the page content below. "
            "Include all sections: responsibilities, requirements, qualifications, benefits, etc. "
            "Return ONLY the job description text, no JSON, no markdown formatting.\n\n"
            f"Page content:\n{cleaned[:20000]}"
        )
        description = await gemini_client.generate_text(prompt)

        if description and len(description) > 50:
            job.description = description.strip()
            await db.commit()
            logger.info(
                "Enriched description for job_id=%d (%d chars)", job.id, len(job.description)
            )
            return EnrichmentResponse(status="enriched", description=job.description)
        else:
            return EnrichmentResponse(status="no_change", description=job.description or "")

    except HTTPException:
        raise
    except Exception:
        logger.warning("Failed to enrich description for match_id=%d", match_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Enrichment failed")
