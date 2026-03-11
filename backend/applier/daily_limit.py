"""Daily application limit enforcement."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.application import Application

logger = logging.getLogger(__name__)


class DailyLimitExceeded(Exception):
    """Raised when the daily application limit has been reached."""


class DailyLimitGuard:
    """Enforces the configurable N applications/day limit (default 10).

    Counts applications whose ``applied_at`` is today and whose
    status is ``"applied"`` or ``"pending"``.
    """

    def __init__(self, db: AsyncSession, limit: int = 10) -> None:
        self.db = db
        self.limit = limit

    async def remaining_today(self) -> int:
        """Return how many applications can still be submitted today."""
        today = date.today()
        stmt = select(func.count(Application.id)).where(
            Application.applied_at >= today,  # type: ignore[operator]
            Application.status.in_(["applied", "pending"]),
        )
        count = (await self.db.execute(stmt)).scalar_one_or_none() or 0
        return max(0, self.limit - count)

    async def can_apply(self) -> bool:
        """Return ``True`` if at least one application slot remains today."""
        return (await self.remaining_today()) > 0

    async def assert_can_apply(self) -> None:
        """Raise :class:`DailyLimitExceeded` if the daily limit is reached."""
        if not await self.can_apply():
            raise DailyLimitExceeded(
                f"Daily application limit of {self.limit} has been reached for today."
            )


__all__ = ["DailyLimitGuard", "DailyLimitExceeded"]
