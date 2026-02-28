"""FastAPI routes for /api/jobs (T14)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"], redirect_slashes=False)




class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: Optional[str] = None
    salary_text: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: Optional[str] = None
    url: str
    apply_url: Optional[str] = None
    posted_at: Optional[datetime] = None
    scraped_at: datetime
    score: Optional[float] = None

    class Config:
        from_attributes = True


class JobListOut(BaseModel):
    jobs: list[JobOut]
    total: int


class SearchRequest(BaseModel):
    keywords: list[str]
    location: Optional[str] = None
    country: str = "gb"
    max_results: int = 20


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=JobListOut)
async def list_jobs(
    db: DBSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    min_score: Optional[float] = Query(None),
):
    """List all scraped jobs with optional score filter."""
    stmt = select(Job).order_by(Job.scraped_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    # Attach scores from job_matches if min_score is requested
    job_outs: list[JobOut] = []
    for job in jobs:
        match_stmt = (
            select(JobMatch.score)
            .where(JobMatch.job_id == job.id)
            .order_by(JobMatch.matched_at.desc())
            .limit(1)
        )
        match_result = await db.execute(match_stmt)
        score_row = match_result.scalar_one_or_none()
        if min_score is not None and (score_row is None or score_row < min_score):
            continue
        job_outs.append(
            JobOut(
                id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                salary_text=job.salary_text,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                description=job.description,
                url=job.url,
                apply_url=job.apply_url,
                posted_at=job.posted_at,
                scraped_at=job.scraped_at,
                score=score_row,
            )
        )

    # Total count
    count_stmt = select(func.count()).select_from(Job)
    total = (await db.execute(count_stmt)).scalar_one()

    return JobListOut(jobs=job_outs, total=total)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, db: DBSession):
    """Get a single job by ID."""
    stmt = select(Job).where(Job.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Latest score
    match_stmt = (
        select(JobMatch.score)
        .where(JobMatch.job_id == job_id)
        .order_by(JobMatch.matched_at.desc())
        .limit(1)
    )
    score_row = (await db.execute(match_stmt)).scalar_one_or_none()

    return JobOut(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        salary_text=job.salary_text,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        description=job.description,
        url=job.url,
        apply_url=job.apply_url,
        posted_at=job.posted_at,
        scraped_at=job.scraped_at,
        score=score_row,
    )


@router.post("/search")
async def search_jobs(body: SearchRequest, db: DBSession):
    """Trigger a manual Adzuna search and store results in the DB.

    Returns the list of newly stored jobs.
    """
    import hashlib

    from backend.matching.filters import JobFilters
    from backend.scraping.adzuna_client import AdzunaClient
    from backend.scraping.deduplicator import JobDeduplicator

    client = AdzunaClient()
    deduplicator = JobDeduplicator()
    filters = JobFilters(
        keywords=body.keywords,
        locations=[body.location] if body.location else [],
    )

    try:
        raw_jobs = await client.search(
            keywords=body.keywords,
            filters=filters,
            country=body.country,
            results_per_page=body.max_results,
        )
    except Exception as exc:
        logger.error("Adzuna search failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Adzuna search failed: {exc}")

    # Deduplicate
    unique_jobs = deduplicator.deduplicate(raw_jobs)

    stored: list[dict] = []
    for rj in unique_jobs:
        # Skip if hash already in DB
        dedup_hash = hashlib.md5(
            f"{rj.company}|{rj.title}|{rj.location}".lower().encode()
        ).hexdigest()
        existing = await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))
        if existing.scalar_one_or_none() is not None:
            continue

        job = Job(
            title=rj.title,
            company=rj.company,
            location=rj.location,
            salary_text=rj.salary_text,
            salary_min=rj.salary_min,
            salary_max=rj.salary_max,
            description=rj.description,
            url=rj.url,
            apply_url=rj.apply_url,
            apply_method=rj.apply_method,
            posted_at=rj.posted_at,
            dedup_hash=dedup_hash,
            external_id=rj.external_id,
            raw_data=rj.raw_data,
        )
        db.add(job)
        stored.append({"title": rj.title, "company": rj.company})

    await db.commit()
    logger.info("Manual search stored %d new jobs", len(stored))
    return {"stored": len(stored), "jobs": stored}


@router.get("/{job_id}/score")
async def get_job_score(job_id: int, db: DBSession):
    """Return the latest match score for a job."""
    stmt = select(Job).where(Job.id == job_id)
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    match_stmt = (
        select(JobMatch)
        .where(JobMatch.job_id == job_id)
        .order_by(JobMatch.matched_at.desc())
        .limit(1)
    )
    match = (await db.execute(match_stmt)).scalar_one_or_none()
    if match is None:
        return {"job_id": job_id, "score": None}

    return {"job_id": job_id, "score": match.score, "keyword_hits": match.keyword_hits}
