"""Applier package.

Canonical vocabulary for application/job-match status values.

DESIGN
------
``backend.applier.*`` strategies (manual, assisted, auto) return an
:class:`ApplicationResult` whose ``status`` field describes the *strategy
outcome* — one of ``RESULT_*`` below.

The engine's :meth:`ApplicationEngine._record_application` translates that
outcome into the *persisted* :class:`Application.status` value, which must
belong to :data:`APPLICATION_STATUSES`. The API layer
(``CreateApplicationRequest`` in ``backend/api/applications.py``) enforces
the same vocabulary on the read side, so callers can rely on a single
known set of strings (``"pending"``, ``"applied"``, ``"cancelled"``,
``"failed"``, ``"interview"``, ``"offer"``, ``"rejected"``).

BACKWARD COMPATIBILITY
----------------------
Older rows persisted before this consolidation may still carry the
legacy strings ``"manual"`` or ``"assisted"`` directly in
``Application.status``. Read-side filters treat those as aliases for
``"applied"`` via :data:`SUCCESS_STATUSES` and :data:`LEGACY_APPLIED_ALIASES`.
Writers MUST emit only members of :data:`APPLICATION_STATUSES`.
"""

from __future__ import annotations

# ── Strategy result statuses (in-memory, on ApplicationResult.status) ──
RESULT_APPLIED = "applied"
RESULT_MANUAL = "manual"
RESULT_ASSISTED = "assisted"
RESULT_CANCELLED = "cancelled"
RESULT_FAILED = "failed"

# ── Persisted Application.status canonical set (writer-side) ──
STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
STATUS_INTERVIEW = "interview"
STATUS_OFFER = "offer"
STATUS_REJECTED = "rejected"

#: All statuses a writer is allowed to persist into ``Application.status``.
APPLICATION_STATUSES: frozenset[str] = frozenset({
    STATUS_PENDING,
    STATUS_APPLIED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INTERVIEW,
    STATUS_OFFER,
    STATUS_REJECTED,
})

#: Legacy values previously written for successful applies. Read-side
#: filters treat these as aliases for :data:`STATUS_APPLIED` so existing
#: DB rows remain countable without a data migration.
LEGACY_APPLIED_ALIASES: frozenset[str] = frozenset({"manual", "assisted"})

#: All values that should be treated as "a submission was made" — used by
#: analytics, daily-limit, queue, and the engine's JobMatch.status update.
SUCCESS_STATUSES: frozenset[str] = frozenset({STATUS_APPLIED}) | LEGACY_APPLIED_ALIASES

#: Strategy-outcome strings that mean the engine should mark the row as
#: a successful application when persisting (i.e. set ``applied_at`` and
#: flip ``JobMatch.status`` to ``"applied"``).
SUCCESS_RESULT_STATUSES: frozenset[str] = frozenset({
    RESULT_APPLIED,
    RESULT_MANUAL,
    RESULT_ASSISTED,
})


class ApplicationRecordError(RuntimeError):
    """Raised when persisting an :class:`Application` row fails.

    The remote application submission may have already succeeded — we
    only failed to write the local DB record. Callers must surface a
    *failure* result to the user so they don't believe the submission
    was tracked; the reserved daily-limit placeholder should be marked
    ``cancelled``/``failed`` rather than left as ``pending``.

    See the EH-07 typed-exception guideline in the standards backlog.
    """


def normalize_result_status(result_status: str) -> str:
    """Map a strategy ``ApplicationResult.status`` to the canonical
    persisted :class:`Application.status` value.

    Unknown strings are returned verbatim — callers should validate
    against :data:`APPLICATION_STATUSES` if strictness is required.
    """
    if result_status in SUCCESS_RESULT_STATUSES:
        return STATUS_APPLIED
    if result_status == RESULT_CANCELLED:
        return STATUS_CANCELLED
    if result_status == RESULT_FAILED:
        return STATUS_FAILED
    return result_status


__all__ = [
    "APPLICATION_STATUSES",
    "ApplicationRecordError",
    "LEGACY_APPLIED_ALIASES",
    "SUCCESS_STATUSES",
    "SUCCESS_RESULT_STATUSES",
    "RESULT_APPLIED",
    "RESULT_MANUAL",
    "RESULT_ASSISTED",
    "RESULT_CANCELLED",
    "RESULT_FAILED",
    "STATUS_PENDING",
    "STATUS_APPLIED",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_INTERVIEW",
    "STATUS_OFFER",
    "STATUS_REJECTED",
    "normalize_result_status",
]
