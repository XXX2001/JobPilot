"""Daily application limit enforcement.

This module guards the configurable N-applications-per-day cap. The
historical implementation exposed a non-atomic ``can_apply`` check that
opened a Time-Of-Check / Time-Of-Use race window: two concurrent
``apply`` requests could both read ``count = limit - 1``, both pass the
check, and both go on to insert an ``Application`` row — exceeding the
limit. Audit ticket PC-04 / DB-06 (2026-05-22) flagged the bug.

The fix is :py:meth:`DailyLimitGuard.reserve_slot`, which atomically
inserts a ``pending`` ``Application`` placeholder *and* verifies the
post-insert count inside a single transaction. SQLite serialises
writers via its RESERVED lock, so once our connection has issued the
``INSERT`` no other connection can commit a competing insert until we
either commit or rollback. The ``COUNT(*)`` we issue after the insert
therefore observes the true post-insert population, and we roll back
the placeholder if it would push us over the limit. Callers update the
returned row's ``status`` / ``notes`` once the apply finishes instead
of inserting a fresh row.

The non-atomic helpers (``remaining_today``, ``can_apply``,
``assert_can_apply``) are retained for read-only use (e.g. the batch
runner that pre-computes how many CVs to generate) — they are
informational and must NOT be used as the gate before submitting an
application.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier import (
    LEGACY_APPLIED_ALIASES,
    STATUS_APPLIED,
    STATUS_NEEDS_VERIFICATION,
    STATUS_PENDING,
)
from backend.models.application import Application
from backend.defaults import DAILY_LIMIT
from backend.utils.time import naive_utc_now, utc_now

logger = logging.getLogger(__name__)

# Statuses that count against today's quota. Includes the canonical
# ``"applied"`` plus legacy ``"manual"`` / ``"assisted"`` for backward
# compatibility with rows persisted before the vocabulary consolidation.
# W06: a possible-but-unverified submission still counts — retrying it
# must not evade the guard. Prepared (awaiting the user) work does not.
COUNTABLE_STATUSES: list[str] = sorted(
    {STATUS_APPLIED, STATUS_PENDING, STATUS_NEEDS_VERIFICATION} | LEGACY_APPLIED_ALIASES
)


def quota_day_start() -> datetime:
    """Start of the current quota day: UTC midnight, as naive UTC (W11).

    Matches how timestamps are stored, and the ``resets_at`` the UI shows.
    """
    return datetime.combine(utc_now().date(), time.min)


async def resolve_daily_limit(db: AsyncSession) -> int:
    """The configured limit (``SearchSettings.daily_limit``), else the default."""
    from backend.models.user import SearchSettings

    row = (await db.execute(select(SearchSettings).limit(1))).scalar_one_or_none()
    return (row.daily_limit if row is not None else None) or DAILY_LIMIT


class DailyLimitExceeded(Exception):
    """Raised when the daily application limit has been reached."""


class DailyLimitGuard:
    """The automated-submission guard: at most N reservations per UTC day (W11).

    A row counts when it holds a reservation for today
    (``quota_reserved_at >= quota_day_start()``) and its status is in
    :data:`COUNTABLE_STATUSES` — an active reservation, a possible
    submission, or a confirmed one. The recorder clears the reservation
    once non-submission is established (prepared for the user, cancelled,
    failed before submit), so manual and assisted preparation never
    consume the quota. Build it with :meth:`from_settings` so the
    engine, batch and UI share the configured limit.
    """

    def __init__(self, db: AsyncSession, limit: int = DAILY_LIMIT) -> None:
        self.db = db
        self.limit = limit

    @classmethod
    async def from_settings(cls, db: AsyncSession) -> "DailyLimitGuard":
        return cls(db=db, limit=await resolve_daily_limit(db))

    async def used_today(self) -> int:
        stmt = select(func.count(Application.id)).where(
            Application.quota_reserved_at >= quota_day_start(),  # type: ignore[operator]
            Application.status.in_(COUNTABLE_STATUSES),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none() or 0

    async def remaining_today(self) -> int:
        """Return how many applications can still be submitted today.

        Read-only / informational. Callers must NOT use this as the
        gate before submitting — use :py:meth:`reserve_slot` instead,
        which is atomic.
        """
        return max(0, self.limit - await self.used_today())

    async def can_apply(self) -> bool:
        """Return ``True`` if at least one application slot remains today.

        Read-only / informational — see :py:meth:`remaining_today`.
        """
        return (await self.remaining_today()) > 0

    async def assert_can_apply(self) -> None:
        """Raise :class:`DailyLimitExceeded` if the daily limit is reached.

        Read-only / informational — see :py:meth:`remaining_today`.
        """
        if not await self.can_apply():
            raise DailyLimitExceeded(
                f"Daily application limit of {self.limit} has been reached for today."
            )

    async def reserve_slot(
        self,
        *,
        job_match_id: Optional[int],
        method: str,
    ) -> int:
        """Atomically reserve one of today's application slots.

        Inserts a ``pending`` :class:`Application` row with
        ``applied_at = utcnow()`` and immediately recounts. If the
        post-insert count exceeds :attr:`limit`, the reservation is
        rolled back and :class:`DailyLimitExceeded` is raised.

        Returns the id of the reserved :class:`Application` row; the
        caller must update its ``status`` / ``notes`` once the apply
        attempt completes (do NOT insert a second row).

        This is the only safe gate for the daily limit — it closes the
        TOCTOU race that existed between ``assert_can_apply`` and the
        subsequent insert.
        """
        placeholder = Application(
            job_match_id=job_match_id,
            method=method,
            status=STATUS_PENDING,
            quota_reserved_at=naive_utc_now(),
        )
        self.db.add(placeholder)
        # flush() issues the INSERT, which takes SQLite's RESERVED
        # write lock. Once we hold it, no other connection can commit
        # a competing insert until we commit or rollback below — so
        # the COUNT we issue next sees a consistent post-insert view.
        await self.db.flush()

        count = await self.used_today()

        if count > self.limit:
            # Over the limit — roll back our reservation. We rollback
            # the whole transaction (which is what SQLAlchemy/aiosqlite
            # gives us) so no partial state leaks. The caller's session
            # is the same one used here, so it is left in a clean state
            # ready for a fresh transaction.
            await self.db.rollback()
            raise DailyLimitExceeded(
                f"Daily application limit of {self.limit} has been reached for today."
            )

        # Commit the reservation so a concurrent reserve_slot on
        # another connection sees it and gets correctly rejected.
        await self.db.commit()
        # Refresh so the id is populated and detached from the
        # transaction that just closed.
        try:
            await self.db.refresh(placeholder)
        except Exception:
            # Best effort — placeholder.id is normally set by flush
            # already on SQLite (autoincrement).
            pass
        return placeholder.id  # type: ignore[return-value]


__all__ = [
    "COUNTABLE_STATUSES",
    "DailyLimitExceeded",
    "DailyLimitGuard",
    "quota_day_start",
    "resolve_daily_limit",
]
