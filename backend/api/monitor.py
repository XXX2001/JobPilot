"""Discovery monitor API: per-source poll status, pause/resume, add a source.

The mutating routes need no guard of their own: ``AccessBoundary`` requires the
local session on every POST under ``/api``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.config import settings
from backend.models.job import Job, JobSource
from backend.monitor.poller import DEFAULT_INTERVAL_S, FALLBACK_INTERVAL_S
from backend.scraping.sources import ADAPTERS
from backend.utils.time import naive_utc_now

router = APIRouter(prefix="/api/monitor", tags=["monitor"], redirect_slashes=False)

LAG_WINDOW = timedelta(days=7)


class SourceIn(BaseModel):
    adapter: str
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    interval_s: Optional[int] = Field(default=None, gt=0)


def _adapter(source: JobSource) -> str:
    """The adapter name, or "" for a source of the old batch path."""
    cfg = source.config
    return str(cfg.get("adapter") or "") if isinstance(cfg, dict) else ""


def _iso(dt: datetime | None) -> str | None:
    """Stored datetimes are naive UTC; say so on the wire."""
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


def _row(source: JobSource, jobs_seen: int = 0, median_lag_s: float | None = None) -> dict:
    adapter = _adapter(source)
    return {
        "id": source.id,
        "name": source.name,
        "adapter": adapter,
        "enabled": bool(source.enabled),
        "interval_s": source.poll_interval_s
        or DEFAULT_INTERVAL_S.get(adapter, FALLBACK_INTERVAL_S),
        "last_scraped_at": _iso(source.last_scraped_at),
        "next_poll_at": _iso(source.next_poll_at),
        "fail_count": source.fail_count or 0,
        "last_error": source.last_error,
        "jobs_seen": jobs_seen,
        "median_lag_s": median_lag_s,
    }


async def _job_stats(db: DBSession, source_id: int | None = None) -> dict[int, tuple[int, Any]]:
    """Per source: (jobs seen, median seconds from posted to first seen, or None).

    The median covers the last 7 days and leaves the seeding poll out: its jobs
    were already old when the monitor first looked, so they say nothing about lag.
    """
    # ponytail: the seeding poll is recognised as the source's earliest
    # first_seen_at (one poll stamps all its jobs with the same instant). A source
    # whose first poll found nothing loses its first real batch from the median;
    # store first_polled_at on job_sources if that ever matters.
    one = (Job.source_id == source_id,) if source_id is not None else ()
    totals = (
        await db.execute(
            select(Job.source_id, func.count(), func.min(Job.first_seen_at))
            .where(Job.source_id.is_not(None), *one)
            .group_by(Job.source_id)
        )
    ).all()
    first_poll = {sid: first for sid, _, first in totals}
    lags: dict[int, list[float]] = {}
    recent = await db.execute(
        select(Job.source_id, Job.first_seen_at, Job.posted_at).where(
            Job.source_id.is_not(None),
            Job.posted_at.is_not(None),
            Job.first_seen_at >= naive_utc_now() - LAG_WINDOW,
            *one,
        )
    )
    for sid, first_seen, posted in recent:
        if first_seen > first_poll[sid]:
            lags.setdefault(sid, []).append((first_seen - posted).total_seconds())
    return {sid: (n, median(lags[sid]) if sid in lags else None) for sid, n, _ in totals}


async def _monitor_source(db: DBSession, source_id: int) -> JobSource:
    source = await db.get(JobSource, source_id)
    if source is None or not _adapter(source):
        raise HTTPException(status_code=404, detail="Monitor source not found")
    return source


@router.get("/status")
async def status(db: DBSession) -> dict:
    sources = (await db.execute(select(JobSource).order_by(JobSource.name))).scalars().all()
    stats = await _job_stats(db)
    return {
        "enabled": bool(settings.MONITOR_ENABLED),
        "sources": [_row(s, *stats.get(s.id, (0, None))) for s in sources if _adapter(s)],
    }


async def _set_enabled(db: DBSession, source_id: int, enabled: bool) -> dict:
    source = await _monitor_source(db, source_id)
    source.enabled = enabled
    if enabled:
        # Resuming forgives the backoff: the owner asked, so poll at the next tick.
        source.fail_count = 0
        source.next_poll_at = None
    await db.commit()
    return _row(source, *(await _job_stats(db, source.id)).get(source.id, (0, None)))


@router.post("/sources/{source_id}/pause")
async def pause(source_id: int, db: DBSession) -> dict:
    return await _set_enabled(db, source_id, False)


@router.post("/sources/{source_id}/resume")
async def resume(source_id: int, db: DBSession) -> dict:
    return await _set_enabled(db, source_id, True)


@router.post("/sources", status_code=201)
async def create_source(body: SourceIn, db: DBSession) -> dict:
    if body.adapter not in ADAPTERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown adapter {body.adapter!r}; known: {', '.join(sorted(ADAPTERS))}",
        )
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    taken = await db.execute(select(JobSource.id).where(JobSource.name == name))
    if taken.first() is not None:
        raise HTTPException(status_code=409, detail=f"A source named {name!r} already exists")
    source = JobSource(
        name=name,
        type="monitor",
        config={**body.config, "adapter": body.adapter},
        poll_interval_s=body.interval_s,
    )
    db.add(source)
    await db.commit()
    return _row(source)
