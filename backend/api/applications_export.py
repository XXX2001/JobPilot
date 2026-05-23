"""GET /api/applications/export — CSV export of all applications (qw-6).

Kept separate from applications.py so the apply-flow module stays focused.
Streams via StreamingResponse; uses Python csv stdlib (no pandas).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query  # type: ignore
from fastapi.responses import StreamingResponse  # type: ignore
from sqlalchemy import select

from backend.api.deps import DBSession
from backend.models.application import Application, ApplicationEvent
from backend.models.job import Job, JobMatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applications", tags=["applications-export"], redirect_slashes=False)

# Columns emitted in the CSV, in spec order.
_COLUMNS = [
    "applied_at",
    "status",
    "method",
    "company",
    "title",
    "location",
    "salary_text",
    "job_url",
    "score",
    "ats_score",
    "last_event_type",
    "last_event_at",
    "last_event_details",
]


def _iso(dt: datetime | None) -> str:
    """Return an ISO 8601 string for *dt*, or an empty string for NULL."""
    if dt is None:
        return ""
    return dt.isoformat()


def _str(value: object) -> str:
    """Return str(value), or empty string for None."""
    if value is None:
        return ""
    return str(value)


@router.get("/export")
async def export_applications(
    db: DBSession,
    format: str = Query(..., description="Export format — only 'csv' is supported"),
) -> StreamingResponse:
    """Stream all applications as a CSV file.

    Query params:
    - ``format=csv`` (required; anything else → 400)

    The response uses ``Content-Disposition: attachment`` so browsers trigger a
    download.  The filename is ``jobpilot-applications-YYYYMMDD.csv`` (UTC date,
    no time component, no user-ID — single-user product).
    """
    if format != "csv":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {format!r}. Only 'csv' is supported.",
        )

    # ── Fetch all applications + joined job data in one query ────────────────
    stmt = (
        select(Application, JobMatch, Job)
        .outerjoin(JobMatch, Application.job_match_id == JobMatch.id)
        .outerjoin(Job, JobMatch.job_id == Job.id)
        .order_by(Application.created_at.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    # ── Batch-fetch events, then find the most-recent per application ────────
    app_ids = [app.id for app, *_ in rows]

    # last_event_by_app: app_id → ApplicationEvent with greatest event_date
    last_event_by_app: dict[int, ApplicationEvent] = {}
    if app_ids:
        events_stmt = (
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id.in_(app_ids))
            .order_by(ApplicationEvent.application_id, ApplicationEvent.event_date.asc())
        )
        events_result = await db.execute(events_stmt)
        for ev in events_result.scalars().all():
            # Later rows overwrite earlier ones (ordered asc → last write = newest)
            last_event_by_app[ev.application_id] = ev

    # ── Build the streaming generator ────────────────────────────────────────
    today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"jobpilot-applications-{today_utc}.csv"

    async def _generate() -> AsyncIterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Header row
        writer.writerow(_COLUMNS)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()

        # Data rows
        for app, jm, job in rows:
            last_ev = last_event_by_app.get(app.id)
            writer.writerow(
                [
                    _iso(app.applied_at),
                    _str(app.status),
                    _str(app.method),
                    _str(job.company if job is not None else None),
                    _str(job.title if job is not None else None),
                    _str(job.location if job is not None else None),
                    _str(job.salary_text if job is not None else None),
                    _str(job.url if job is not None else None),
                    _str(jm.score if jm is not None else None),
                    _str(jm.ats_score if jm is not None else None),
                    _str(last_ev.event_type if last_ev is not None else None),
                    _iso(last_ev.event_date if last_ev is not None else None),
                    _str(last_ev.details if last_ev is not None else None),
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
