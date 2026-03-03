"""FastAPI routes for /api/queue (T14)."""

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


class QueueMatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    match_id: int
    job_id: int
    title: str
    company: str
    location: Optional[str] = None
    score: float
    status: str
    batch_date: Optional[date] = None
    matched_at: datetime
    url: str


class QueueOut(BaseModel):
    matches: list[QueueMatchOut]
    total: int


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=QueueOut)
async def get_queue(db: DBSession, batch_date: Optional[date] = None):
    """Return today's morning queue — matches with status='new', sorted by score desc."""
    target_date = batch_date or date.today()

    stmt = (
        select(JobMatch, Job)
        .join(Job, Job.id == JobMatch.job_id)
        .where(JobMatch.status == "new")
        .where(JobMatch.batch_date == target_date)
        .order_by(JobMatch.score.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    matches: list[QueueMatchOut] = []
    for match, job in rows:
        matches.append(
            QueueMatchOut(
                match_id=match.id,
                job_id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                score=match.score,
                status=match.status,
                batch_date=match.batch_date,
                matched_at=match.matched_at,
                url=job.url,
            )
        )

    return QueueOut(matches=matches, total=len(matches))


@router.post("/refresh")
async def refresh_queue(request: Request, db: DBSession):
    """Trigger a new morning batch run immediately (manual re-run).

    Runs the batch in a background task so the endpoint returns promptly.
    """
    import asyncio

    scheduler = getattr(request.app.state, "morning_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Morning scheduler not available")

    async def _run():
        try:
            await scheduler.run_batch()
        except Exception as exc:
            logger.error("Morning batch error: %s", exc)

    asyncio.create_task(_run())
    return {"status": "started", "message": "Morning batch triggered in background"}


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
