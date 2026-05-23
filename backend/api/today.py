"""GET /api/today — Today dashboard (nx-2).

Answers the three questions a job seeker has when they open the app:
  1. What's new since I last looked?   → new_matches
  2. What needs my attention right now? → blocked_actions
  3. How am I doing this week?          → week_stats

"Since last visit" semantics
-----------------------------
``UserProfile.last_dashboard_seen_at`` tracks the timestamp of the
*previous* visit. On every request we:
  1. Read ``last_dashboard_seen_at`` (snapshot it inside the transaction).
  2. Count new JobMatch rows created since that timestamp.
  3. UPDATE the column to NOW (so the *next* call sees the updated baseline).

All three steps happen inside the same DB transaction so two rapid
back-to-back GET calls cannot both see the same "new" counts.

NULL → first visit → fallback to "last 24 h".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.applier.daily_limit import COUNTABLE_STATUSES
from backend.models.application import Application
from backend.models.job import Job, JobMatch
from backend.models.user import SearchSettings, SiteCredential, UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/today", tags=["today"], redirect_slashes=False)


# ─── Response schemas ──────────────────────────────────────────────────────────


def _iso(dt: datetime | None) -> str:
    """Return an ISO 8601 string with +00:00 offset, or empty string for NULL."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _utc_now() -> datetime:
    """Return current time as naive UTC (matches DB storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MatchBrief(BaseModel):
    id: int
    job_id: int
    score: float
    status: str
    matched_at: str  # ISO 8601
    job_title: Optional[str] = None
    company: Optional[str] = None


class NewMatchesSection(BaseModel):
    since: str  # ISO 8601 — the baseline timestamp used for "new" count
    high_confidence: list[MatchBrief]   # score >= 80
    worth_reviewing: list[MatchBrief]   # 60 <= score < 80
    skipped: list[MatchBrief]           # score < 60
    total: int


class BlockedAction(BaseModel):
    kind: str           # "broken_session" | "pending_application" | "stale_manual"
    count: int
    label: str
    href: str           # frontend route to navigate to


class BlockedActionsSection(BaseModel):
    actions: list[BlockedAction]


class WeekStatsSection(BaseModel):
    applications_submitted: int
    daily_limit_used: int
    daily_limit_total: int
    response_rate: str  # literal or percentage string


class TodayOut(BaseModel):
    new_matches: NewMatchesSection
    blocked_actions: BlockedActionsSection
    week_stats: WeekStatsSection


# ─── Route ────────────────────────────────────────────────────────────────────


@router.get("", response_model=TodayOut)
async def get_today(db: DBSession) -> TodayOut:
    """Return the Today dashboard payload.

    All three sections are computed in one DB round-trip batch.
    The ``last_dashboard_seen_at`` baseline is snapshotted *and* updated
    inside the same flush so two concurrent GETs cannot both see the
    same "new" matches.
    """
    now = _utc_now()

    # ── 1. Read & update last_dashboard_seen_at ─────────────────────────────
    profile_result = await db.execute(select(UserProfile).where(UserProfile.id == 1))
    profile = profile_result.scalar_one_or_none()

    if profile is not None:
        since_dt = profile.last_dashboard_seen_at
        profile.last_dashboard_seen_at = now
    else:
        since_dt = None

    # Flush the UPDATE so it's part of the same transaction as our SELECTs.
    # We do NOT commit yet — all counts will be within the same snapshot.
    await db.flush()

    # Baseline: NULL → last 24 h
    if since_dt is None:
        since_dt = now - timedelta(hours=24)

    # ── 2. New matches since last visit ─────────────────────────────────────
    new_matches_stmt = (
        select(JobMatch, Job)
        .outerjoin(Job, JobMatch.job_id == Job.id)
        .where(JobMatch.matched_at >= since_dt)
        .order_by(JobMatch.score.desc())
    )
    nm_result = await db.execute(new_matches_stmt)
    nm_rows = nm_result.all()

    high_confidence: list[MatchBrief] = []
    worth_reviewing: list[MatchBrief] = []
    skipped_matches: list[MatchBrief] = []

    for jm, job in nm_rows:
        brief = MatchBrief(
            id=jm.id,
            job_id=jm.job_id,
            score=jm.score,
            status=jm.status,
            matched_at=_iso(jm.matched_at),
            job_title=job.title if job is not None else None,
            company=job.company if job is not None else None,
        )
        if jm.score >= 80:
            high_confidence.append(brief)
        elif jm.score >= 60:
            worth_reviewing.append(brief)
        else:
            skipped_matches.append(brief)

    new_matches = NewMatchesSection(
        since=_iso(since_dt),
        high_confidence=high_confidence,
        worth_reviewing=worth_reviewing,
        skipped=skipped_matches,
        total=len(nm_rows),
    )

    # ── 3. Blocked actions ──────────────────────────────────────────────────
    blocked: list[BlockedAction] = []

    # Signal 1: sites with credentials but no valid session
    try:
        from backend.scraping.session_manager import BrowserSessionManager

        cred_result = await db.execute(select(SiteCredential.site_name))
        credentialed_sites = set(cred_result.scalars().all())

        # BrowserSessionManager is not async — safe to call synchronously
        session_mgr = BrowserSessionManager()
        sessions = session_mgr.list_sessions()
        valid_sites = {s.site for s in sessions if s.exists}
        broken = credentialed_sites - valid_sites
        if broken:
            blocked.append(
                BlockedAction(
                    kind="broken_session",
                    count=len(broken),
                    label=f"{len(broken)} site login{'s' if len(broken) != 1 else ''} expired",
                    href="/settings",
                )
            )
    except Exception as _exc:
        logger.warning("Could not check browser sessions for blocked actions: %s", _exc)

    # Signal 2: applications in pending / awaiting_submit
    pending_stmt = select(func.count(Application.id)).where(
        Application.status.in_(["pending", "awaiting_submit"])
    )
    pending_count: int = (await db.execute(pending_stmt)).scalar_one_or_none() or 0
    if pending_count:
        blocked.append(
            BlockedAction(
                kind="pending_application",
                count=pending_count,
                label=f"{pending_count} application{'s' if pending_count != 1 else ''} awaiting submission",
                href="/tracker",
            )
        )

    # Signal 3: JobMatch with status='selected', >24 h old, no Application row
    cutoff_24h = now - timedelta(hours=24)
    stale_stmt = (
        select(func.count(JobMatch.id))
        .where(
            JobMatch.status == "selected",
            JobMatch.matched_at <= cutoff_24h,
        )
        .where(
            ~(
                select(Application.id)
                .where(Application.job_match_id == JobMatch.id)
                .exists()
            )
        )
    )
    stale_count: int = (await db.execute(stale_stmt)).scalar_one_or_none() or 0
    if stale_count:
        blocked.append(
            BlockedAction(
                kind="stale_manual",
                count=stale_count,
                label=f"{stale_count} selected job{'s' if stale_count != 1 else ''} waiting >24 h",
                href="/queue",
            )
        )

    blocked_actions = BlockedActionsSection(actions=blocked)

    # ── 4. Week stats (7-day rolling) ──────────────────────────────────────
    week_ago = now - timedelta(days=7)

    # Applications submitted this week
    submitted_stmt = select(func.count(Application.id)).where(
        Application.applied_at >= week_ago,
        Application.status.in_(COUNTABLE_STATUSES),
    )
    apps_submitted: int = (await db.execute(submitted_stmt)).scalar_one_or_none() or 0

    # Daily limit usage today
    today = datetime.now(timezone.utc).date()
    used_today_stmt = select(func.count(Application.id)).where(
        Application.applied_at >= today,  # type: ignore[operator]
        Application.status.in_(COUNTABLE_STATUSES),
    )
    used_today: int = (await db.execute(used_today_stmt)).scalar_one_or_none() or 0

    # Resolve daily limit from settings
    from backend.defaults import DAILY_LIMIT

    ss_result = await db.execute(select(SearchSettings).where(SearchSettings.id == 1))
    ss = ss_result.scalar_one_or_none()
    daily_limit_total = (ss.daily_limit if ss is not None else None) or DAILY_LIMIT

    week_stats = WeekStatsSection(
        applications_submitted=apps_submitted,
        daily_limit_used=used_today,
        daily_limit_total=daily_limit_total,
        response_rate="— (requires Gmail integration)",
    )

    # ── 5. Commit everything (the last_dashboard_seen_at update) ───────────
    await db.commit()

    return TodayOut(
        new_matches=new_matches,
        blocked_actions=blocked_actions,
        week_stats=week_stats,
    )
