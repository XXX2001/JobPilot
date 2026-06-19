"""Follow-up reminder scanner.

Scans for applications that have been in ``applied`` status for at least
``threshold_days`` days with no ``follow_up_due`` event already created.
On each qualifying application it inserts a single ``follow_up_due`` event
so the frontend can surface them in a "Needs follow-up" tab.

Design decisions:
- **Lazy trigger**: called at app startup and at the start of each batch run.
  No background scheduler.
- **Idempotency**: skips applications that already have a ``follow_up_due``
  event — running the function twice in a row has no side effects.
- **Resolution**: the user logs a ``follow_up`` event via the existing
  ``POST /api/applications/{id}/events`` endpoint. The API filters it out
  when ``needs_follow_up=true`` once a ``follow_up`` event exists that is
  *newer* than the most-recent ``follow_up_due`` event.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select

from backend.database import AsyncSessionLocal
from backend.models.application import Application, ApplicationEvent
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


async def scan_overdue(threshold_days: int = 7) -> int:
    """Create ``follow_up_due`` events for overdue applications.

    An application is overdue when:
    - Its ``status`` is ``"applied"``; AND
    - Its **freshness anchor** is at least ``threshold_days`` days ago, where
      the freshness anchor is ``max(applied_at, last_correspondence_at)``
      (whichever is newer); AND
    - It has no existing ``follow_up_due`` event (idempotency guard).

    The ``last_correspondence_at`` column is written by
    ``POST /api/correspondence/link`` whenever a Gmail message is linked to
    the application, so a recruiter reply (or a manual link) pushes the
    follow-up window forward. When the column is NULL the scanner falls back
    to ``applied_at`` alone — matching the original 7-day post-apply
    behaviour for applications with no linked correspondence. (Re-opens PG-1:
    column was previously write-only — see
    ``docs/reports/2026-05-23-codebase-deep-dive/06-gmail-integration.md`` §15.10.)

    Returns the number of new events created (0 when nothing is overdue or
    on an empty database).

    Always opens its own short-lived session so callers do not need to pass
    a session and the function never commits inside a borrowed long-lived
    session (which would expire ORM objects still in use by the caller).
    """
    cutoff: datetime = utc_now() - timedelta(days=threshold_days)

    # Find applied applications older than the threshold that do NOT yet
    # have a follow_up_due event (idempotency: safe to call repeatedly).
    already_has_event = exists().where(
        ApplicationEvent.application_id == Application.id,
        ApplicationEvent.event_type == "follow_up_due",
    )

    # Freshness anchor (per-row): the LATER of applied_at and
    # last_correspondence_at. We can't use SQL MAX() in a WHERE (that's an
    # aggregate); instead require BOTH columns to be ``<= cutoff`` — applied_at
    # is non-null by the predicate above, and COALESCE(last_correspondence_at,
    # applied_at) replaces NULL with applied_at so the second clause becomes a
    # no-op when there's no linked correspondence yet.
    correspondence_anchor = func.coalesce(
        Application.last_correspondence_at, Application.applied_at
    )

    stmt = (
        select(Application)
        .where(
            Application.status == "applied",
            Application.applied_at != None,  # noqa: E711
            Application.applied_at <= cutoff,
            correspondence_anchor <= cutoff,
            ~already_has_event,
        )
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        if not candidates:
            return 0

        now = utc_now()
        count = 0
        for app in candidates:
            event = ApplicationEvent(
                application_id=app.id,
                event_type="follow_up_due",
                details=f"No follow-up after {threshold_days} days",
                event_date=now,
            )
            db.add(event)
            count += 1

        await db.commit()

    logger.info("scan_overdue: created %d follow_up_due event(s)", count)
    return count
