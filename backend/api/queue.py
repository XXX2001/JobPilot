from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class JobOut(BaseModel):
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
    job: JobOut  # nested — matches frontend's match.job.title etc.


class QueueOut(BaseModel):
    matches: list[QueueMatchOut]
    total: int


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
                job=JobOut(
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


@router.get("/status")
async def get_batch_status(request: Request):
    """Return the current batch running state and last progress message."""
    runner = getattr(request.app.state, "batch_runner", None)
    if runner is None:
        return {"running": False, "last_status": None}
    return {
        "running": runner.running,
        "last_status": runner.last_status,
    }


@router.post("/refresh")
async def refresh_queue(request: Request, db: DBSession):  # noqa: ARG001
    """Trigger a new morning batch run immediately (manual re-run).

    Runs the batch in a background task so the endpoint returns promptly.
    """
    import asyncio

    runner = getattr(request.app.state, "batch_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Batch runner not available")

    if runner.running:
        raise HTTPException(status_code=409, detail="A search is already in progress")

    async def _run():
        try:
            await runner.run_batch()
        except Exception as exc:
            logger.error("Batch run error: %s", exc)

    asyncio.create_task(_run())
    return {"status": "started", "message": "Job search triggered in background"}


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
        job=JobOut(
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


@router.patch("/{match_id}/skip")
async def skip_match(match_id: int, db: DBSession):
    """Mark a queue match as skipped."""
    stmt = select(JobMatch).where(JobMatch.id == match_id)
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    match.status = "skipped"
    await db.commit()
    return {"match_id": match_id, "status": "skipped"}


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{match_id}/status")
async def update_match_status(match_id: int, body: StatusUpdate, db: DBSession):
    """Update the status of a queue match (new, skipped, applying, applied)."""
    allowed = {"new", "skipped", "applying", "applied", "rejected"}
    if body.status not in allowed:
        raise HTTPException(
            status_code=422, detail=f"Invalid status '{body.status}'. Allowed: {allowed}"
        )
    stmt = select(JobMatch).where(JobMatch.id == match_id)
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    match.status = body.status
    await db.commit()
    return {"match_id": match_id, "status": body.status}


@router.post("/{match_id}/enrich-description")
async def enrich_job_description(match_id: int, db: DBSession, request: Request):
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
            return {"status": "enriched", "description": job.description}
        else:
            return {"status": "no_change", "description": job.description or ""}

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to enrich description for match_id=%d: %s", match_id, exc)
        raise HTTPException(status_code=502, detail=f"Enrichment failed: {exc}")
