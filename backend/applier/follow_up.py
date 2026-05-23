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
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.application import Application, ApplicationEvent

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return current UTC time as a naive datetime (matches DB storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def scan_overdue(db: AsyncSession, threshold_days: int = 7) -> int:
    """Create ``follow_up_due`` events for overdue applications.

    An application is overdue when:
    - Its ``status`` is ``"applied"``; AND
    - Its ``applied_at`` is at least ``threshold_days`` days ago; AND
    - It has no existing ``follow_up_due`` event (idempotency guard).

    Returns the number of new events created (0 when nothing is overdue or
    on an empty database).
    """
    cutoff: datetime = _utc_now() - timedelta(days=threshold_days)

    # Find applied applications older than the threshold that do NOT yet
    # have a follow_up_due event (idempotency: safe to call repeatedly).
    already_has_event = exists().where(
        ApplicationEvent.application_id == Application.id,
        ApplicationEvent.event_type == "follow_up_due",
    )

    stmt = (
        select(Application)
        .where(
            Application.status == "applied",
            Application.applied_at != None,  # noqa: E711
            Application.applied_at <= cutoff,
            ~already_has_event,
        )
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    if not candidates:
        return 0

    now = _utc_now()
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
