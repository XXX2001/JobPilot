"""FastAPI routes for /api/analytics (T15 - usage analytics)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, func, and_
from pydantic import BaseModel

from backend.api.deps import DBSession
from backend.models.application import Application

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"], redirect_slashes=False)


# ─── Response schemas ─────────────────────────────────────────────────────────


class AnalyticsSummary(BaseModel):
    total_apps: int
    apps_this_week: int
    response_rate: float
    avg_match_score: Optional[float]


class DailyTrend(BaseModel):
    date: str
    count: int


class AnalyticsTrends(BaseModel):
    trends: list[DailyTrend]
    days: int


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(db: DBSession):
    """Return high-level application statistics."""
    # Total applications
    total_stmt = select(func.count()).select_from(Application)
    total_apps = (await db.execute(total_stmt)).scalar_one()

    # Applications this week (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_stmt = (
        select(func.count()).select_from(Application).where(Application.created_at >= week_ago)
    )
    apps_this_week = (await db.execute(week_stmt)).scalar_one()

    # Response rate: apps with status 'interview', 'offer', or 'rejected' / total
    responded_statuses = ("interview", "offer", "rejected")
    responded_stmt = (
        select(func.count())
        .select_from(Application)
        .where(Application.status.in_(responded_statuses))
    )
    responded = (await db.execute(responded_stmt)).scalar_one()
    response_rate = round(responded / total_apps * 100, 1) if total_apps > 0 else 0.0

    # Average match score from job_matches (deferred — placeholder until Wave 3 wires JobMatch)
    avg_match_score: Optional[float] = None
    try:
        from backend.models.job import JobMatch

        avg_stmt = select(func.avg(JobMatch.score))
        avg_result = (await db.execute(avg_stmt)).scalar_one_or_none()
        if avg_result is not None:
            avg_match_score = round(float(avg_result), 1)
    except Exception:
        pass

    return AnalyticsSummary(
        total_apps=total_apps,
        apps_this_week=apps_this_week,
        response_rate=response_rate,
        avg_match_score=avg_match_score,
    )


@router.get("/trends", response_model=AnalyticsTrends)
async def get_analytics_trends(
    db: DBSession,
    days: int = Query(30, ge=1, le=365),
):
    """Return applications per day for the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Fetch all applications within range
    stmt = select(Application.created_at).where(Application.created_at >= cutoff)
    result = await db.execute(stmt)
    created_dates = result.scalars().all()

    # Aggregate by day
    day_counts: dict[str, int] = {}
    for created_at in created_dates:
        day_str = created_at.strftime("%Y-%m-%d")
        day_counts[day_str] = day_counts.get(day_str, 0) + 1

    # Fill in zero-count days for continuity
    trends: list[DailyTrend] = []
    today = datetime.utcnow().date()
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        trends.append(DailyTrend(date=day_str, count=day_counts.get(day_str, 0)))

    return AnalyticsTrends(trends=trends, days=days)
