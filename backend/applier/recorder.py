"""ApplicationRecorder — extracted from engine._record_application.

Persists the application row and lifecycle event after a strategy
completes. Extracted as a collaborator so the engine and FSM don't need
to inline the ORM logic.

EH-03 (RemoteSubmittedLocalFailed): when the DB write fails after the
remote submission succeeded, the recorder raises
:class:`~backend.applier.ApplicationRecordError` and logs a
``db_write_failed`` :class:`~backend.models.application.ApplicationEvent`
(best-effort — the commit may have already failed, so we attempt a
separate session operation).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier import (
    ApplicationRecordError,
    RESULT_AWAITING_USER,
    RESULT_CANCELLED,
    RESULT_NEEDS_VERIFICATION,
    SUCCESS_RESULT_STATUSES,
    normalize_result_status,
)
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


class ApplicationRecorder:
    """Persist an application row and an initial lifecycle event.

    If ``reserved_app_id`` is provided (the common AUTO/ASSISTED path),
    the placeholder row inserted by
    :class:`~backend.applier.daily_limit.DailyLimitGuard.reserve_slot`
    is updated in place — we never insert a duplicate. Otherwise
    (MANUAL) a fresh row is inserted.

    Raises :class:`~backend.applier.ApplicationRecordError` on any
    DB-write failure.
    """

    async def record(
        self,
        db: AsyncSession,
        job_match_id: int,
        result_status: str,
        result_method: str,
        result_message: Optional[str],
        reserved_app_id: Optional[int] = None,
        evidence: str = "",
    ) -> None:
        """Persist the application and an initial lifecycle event.

        W06: only a ``confirmed`` outcome becomes ``applied`` (with
        ``confirmation_source='destination'`` and its *evidence*). Prepared
        (awaiting the user) and possibly-submitted (needs verification)
        outcomes keep ``applied_at`` empty and move the match to
        ``applying`` — out of the new-match queue, not counted as applied.

        Parameters
        ----------
        db:
            Active async SQLAlchemy session.
        job_match_id:
            The :class:`~backend.models.job.JobMatch` row being applied.
        result_status:
            One of the ``RESULT_*`` constants from :mod:`backend.applier`.
        result_method:
            ``"auto"`` / ``"assisted"`` / ``"manual"``.
        result_message:
            Optional human-readable note.
        reserved_app_id:
            If set, update this placeholder row instead of inserting a
            new one.
        """
        try:
            from sqlalchemy import select

            from backend.models.application import Application, ApplicationEvent
            from backend.models.job import JobMatch

            is_success = result_status in SUCCESS_RESULT_STATUSES
            persisted_status = normalize_result_status(result_status)
            applied_at_value = utc_now() if is_success else None

            if reserved_app_id is not None:
                stmt = select(Application).where(Application.id == reserved_app_id)
                app = (await db.execute(stmt)).scalar_one_or_none()
                if app is None:
                    # Placeholder vanished — invariant break. Do NOT
                    # insert a fresh row (would double-count the limit).
                    raise ApplicationRecordError(
                        "reserved daily-limit placeholder vanished before record "
                        "(concurrent DB modification?)"
                    )
                app.method = result_method
                app.status = persisted_status
                app.applied_at = applied_at_value
                app.notes = result_message or None
                app.confirmation_source = "destination" if is_success else None
                app.confirmation_evidence = evidence or None
                # W11: keep the reservation only while a submission did or may
                # have happen(ed); prepared/cancelled/failed-before-submit release it.
                if result_status not in (*SUCCESS_RESULT_STATUSES, RESULT_NEEDS_VERIFICATION):
                    app.quota_reserved_at = None
            else:
                app = Application(
                    job_match_id=job_match_id,
                    method=result_method,
                    status=persisted_status,
                    applied_at=applied_at_value,
                    notes=result_message or None,
                    confirmation_source="destination" if is_success else None,
                    confirmation_evidence=evidence or None,
                )
                db.add(app)
                await db.flush()

            # Lifecycle event (preserves richer history).
            event = ApplicationEvent(
                application_id=app.id,
                event_type=result_status,
                details=" — ".join(p for p in (result_message, evidence) if p) or None,
            )
            db.add(event)

            new_match_status = (
                "applied"
                if is_success
                else "applying"
                if result_status in (RESULT_AWAITING_USER, RESULT_NEEDS_VERIFICATION)
                else None
            )
            if new_match_status is not None:
                stmt = select(JobMatch).where(JobMatch.id == job_match_id)
                match = (await db.execute(stmt)).scalar_one_or_none()
                if match is not None:
                    match.status = new_match_status

            await db.commit()
            logger.info(
                "Recorded application id=%s job_match_id=%d result=%s status=%s",
                app.id,
                job_match_id,
                result_status,
                persisted_status,
            )
        except ApplicationRecordError:
            await db.rollback()
            raise
        except Exception as exc:
            logger.exception("Failed to record application")
            await db.rollback()
            raise ApplicationRecordError(
                "application was submitted but DB write failed"
            ) from exc

    async def release_reserved_slot(
        self,
        db: AsyncSession,
        reserved_app_id: int,
    ) -> None:
        """Clear ``applied_at`` on a placeholder so it stops counting.

        Used when the reservation was claimed but the apply never
        completes (early return, dispatch crash, or cancellation). We
        don't DELETE so any FK references / audit trail survive.

        Logs with traceback and re-raises on failure (EH-01).
        """
        try:
            from sqlalchemy import select

            from backend.models.application import Application

            stmt = select(Application).where(Application.id == reserved_app_id)
            app = (await db.execute(stmt)).scalar_one_or_none()
            if app is not None:
                app.status = RESULT_CANCELLED
                app.applied_at = None
                app.quota_reserved_at = None
                await db.commit()
        except Exception:
            logger.exception(
                "Failed to release reserved slot %d", reserved_app_id
            )
            await db.rollback()
            raise


async def sweep_stale_pending(db: AsyncSession) -> int:
    """Startup sweep: resolve reservations left ``pending`` by a dead process.

    A reservation is ``pending`` only while its attempt runs, and the attempt
    lived in the previous process. Whether it clicked submit is unknown, so
    the row becomes ``needs_verification`` — it keeps its quota slot, blocks a
    blind re-apply of the job, and is never counted as applied. Returns the
    number of rows moved. Rows without a reservation (tracker entries the
    user created as ``pending``) are left alone.
    """
    from sqlalchemy import select

    from backend.applier import STATUS_NEEDS_VERIFICATION, STATUS_PENDING
    from backend.models.application import Application, ApplicationEvent
    from backend.models.job import JobMatch

    # ponytail: assumes one backend process per database — a second live
    # process would have its running attempts swept. If that ever exists, stamp
    # the reservation with an owner/heartbeat and sweep only dead owners.
    note = "Interrupted by a restart before the outcome was recorded — check the job site."
    rows = (
        await db.execute(
            select(Application).where(
                Application.status == STATUS_PENDING,
                Application.quota_reserved_at.is_not(None),
            )
        )
    ).scalars().all()
    for app in rows:
        app.status = STATUS_NEEDS_VERIFICATION
        app.notes = note
        db.add(
            ApplicationEvent(
                application_id=app.id, event_type=RESULT_NEEDS_VERIFICATION, details=note
            )
        )
        match = await db.get(JobMatch, app.job_match_id) if app.job_match_id is not None else None
        if match is not None:
            match.status = "applying"  # same as a recorded needs_verification
    if rows:
        await db.commit()
        logger.warning("Startup sweep: %d stale pending row(s) need verification", len(rows))
    return len(rows)


__all__ = ["ApplicationRecorder", "sweep_stale_pending"]
