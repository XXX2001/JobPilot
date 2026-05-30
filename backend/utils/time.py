"""Centralized UTC time helpers.

Two flavours intentionally exist:

* :func:`utc_now` — timezone-aware UTC. Use in API/service code.
* :func:`naive_utc_now` — naive UTC (``tzinfo=None``). Use for ORM
  ``DateTime`` column defaults so stored values stay comparable with the
  naive datetimes SQLite already holds. Both replace the duplicated
  ``_now()`` / ``_utc_now()`` definitions that were scattered across
  ``backend/models/*`` and ``backend/api/*``.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def naive_utc_now() -> datetime:
    """Return the current UTC time with ``tzinfo`` stripped (naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
