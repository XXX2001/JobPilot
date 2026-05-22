"""Application engine — routes apply requests to the right strategy."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier import (
    RESULT_CANCELLED,
    SUCCESS_RESULT_STATUSES,
    normalize_result_status,
)
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy
from backend.config import settings
from backend.defaults import DAILY_LIMIT, MAX_LEN_ADDITIONAL_ANSWERS, MAX_LEN_EMAIL, MAX_LEN_LOCATION, MAX_LEN_PHONE
from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard
from backend.applier.manual_apply import ApplicationResult, ManualApplyStrategy
from backend.models.application import Application, ApplicationEvent

logger = logging.getLogger(__name__)


class ApplyMode(str, Enum):
    AUTO = "auto"
    ASSISTED = "assisted"
    MANUAL = "manual"


class ApplicantInfo(BaseModel):
    full_name: str = Field("", max_length=200)
    email: str = Field("", max_length=MAX_LEN_EMAIL)
    phone: str = Field("", max_length=MAX_LEN_PHONE)
    location: str = Field("", max_length=MAX_LEN_LOCATION)
    additional_answers_json: str = Field("", max_length=MAX_LEN_ADDITIONAL_ANSWERS)


class ApplicationEngine:
    """Routes application requests to AUTO / ASSISTED / MANUAL strategies.

    Also enforces the daily limit and records application lifecycle events.
    """

    def __init__(
        self,
        api_key: str,
        model: str = None,
        daily_limit: int = DAILY_LIMIT,
    ) -> None:
        self._api_key = api_key
        # If model not provided, use configured primary model
        self._model = model or settings.GOOGLE_MODEL
        self._daily_limit = daily_limit

        self._auto = AutoApplyStrategy(api_key=api_key, model=self._model)
        self._assisted = AssistedApplyStrategy(api_key=api_key, model=self._model)
        self._manual = ManualApplyStrategy()

        # Per-job asyncio events for confirm/cancel coming from WS
        self._confirm_events: dict[int, asyncio.Event] = {}
        self._cancel_events: dict[int, asyncio.Event] = {}

    # ------------------------------------------------------------------ #
    #  WS signal handlers (called by ws.py on incoming client messages)  #
    # ------------------------------------------------------------------ #

    def signal_confirm(self, job_id: int) -> None:
        """Trigger confirmation for *job_id* (``confirm_submit`` WS message)."""
        if job_id in self._confirm_events:
            self._confirm_events[job_id].set()

    def signal_cancel(self, job_id: int) -> None:
        """Trigger cancellation for *job_id* (``cancel_apply`` WS message)."""
        if job_id in self._cancel_events:
            self._cancel_events[job_id].set()

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    async def apply(
        self,
        job_match_id: int,
        mode: ApplyMode,
        db: AsyncSession,
        apply_url: str = "",
        applicant: Optional[ApplicantInfo] = None,
        cv_pdf: Optional[Path] = None,
        letter_pdf: Optional[Path] = None,
    ) -> ApplicationResult:
        """Apply to the job identified by *job_match_id* using *mode*."""

        if applicant is None:
            applicant = ApplicantInfo()

        # Atomically reserve a daily-limit slot before dispatching.
        # reserve_slot() inserts a ``pending`` Application row and
        # re-checks the count in one transaction, closing the TOCTOU
        # race that existed when we did a separate read-then-check.
        # MANUAL mode is exempt — the user opens the browser themselves
        # so we don't reserve a slot until/unless they record success.
        reserved_app_id: Optional[int] = None
        if mode != ApplyMode.MANUAL:
            guard = DailyLimitGuard(db=db, limit=self._daily_limit)
            try:
                reserved_app_id = await guard.reserve_slot(
                    job_match_id=job_match_id,
                    method=mode.value,
                )
            except DailyLimitExceeded as exc:
                logger.warning("Daily limit exceeded: %s", exc)
                return ApplicationResult(
                    status=RESULT_CANCELLED,
                    method=mode.value,
                    message=str(exc),
                )

        # Set up per-job events — guard against concurrent apply for same job
        if job_match_id in self._confirm_events:
            # Release the daily-limit reservation we just claimed,
            # otherwise the placeholder lingers as ``pending`` and
            # permanently consumes one of today's slots.
            if reserved_app_id is not None:
                await self._release_reserved_slot(db, reserved_app_id)
            return ApplicationResult(
                status=RESULT_CANCELLED,
                method=mode.value,
                message=f"Job {job_match_id} already has an application in progress.",
            )
        self._confirm_events[job_match_id] = asyncio.Event()
        self._cancel_events[job_match_id] = asyncio.Event()

        try:
            result = await self._dispatch(
                job_match_id=job_match_id,
                mode=mode,
                apply_url=apply_url,
                applicant=applicant,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
            )
        except Exception:
            # If dispatch crashes, release the reservation so the
            # placeholder doesn't permanently consume a daily slot.
            if reserved_app_id is not None:
                await self._release_reserved_slot(db, reserved_app_id)
            raise
        finally:
            self._confirm_events.pop(job_match_id, None)
            self._cancel_events.pop(job_match_id, None)

        # Persist application record — for non-manual modes this
        # *updates* the placeholder row reserved above; for manual
        # mode it inserts a fresh row.
        await self._record_application(
            db=db,
            job_match_id=job_match_id,
            result=result,
            reserved_app_id=reserved_app_id,
        )

        return result

    async def _release_reserved_slot(
        self,
        db: AsyncSession,
        reserved_app_id: int,
    ) -> None:
        """Clear ``applied_at`` on a placeholder so it stops counting.

        Used when the reservation was claimed but the apply never
        completes (early return, dispatch crash). We don't DELETE so
        any FK references / audit trail survive.
        """
        try:
            from sqlalchemy import select

            stmt = select(Application).where(Application.id == reserved_app_id)
            app = (await db.execute(stmt)).scalar_one_or_none()
            if app is not None:
                app.status = RESULT_CANCELLED
                app.applied_at = None
                await db.commit()
        except Exception as exc:
            logger.error("Failed to release reserved slot %d: %s", reserved_app_id, exc)
            await db.rollback()

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _dispatch(
        self,
        job_match_id: int,
        mode: ApplyMode,
        apply_url: str,
        applicant: ApplicantInfo,
        cv_pdf: Optional[Path],
        letter_pdf: Optional[Path],
    ) -> ApplicationResult:
        if mode == ApplyMode.AUTO:
            return await self._auto.apply(
                job_id=job_match_id,
                apply_url=apply_url,
                full_name=applicant.full_name,
                email=applicant.email,
                phone=applicant.phone,
                location=applicant.location,
                additional_answers=applicant.additional_answers_json,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
                confirm_event=self._confirm_events.get(job_match_id),
                cancel_event=self._cancel_events.get(job_match_id),
            )
        elif mode == ApplyMode.ASSISTED:
            return await self._assisted.apply(
                apply_url=apply_url,
                full_name=applicant.full_name,
                email=applicant.email,
                phone=applicant.phone,
                location=applicant.location,
                additional_answers=applicant.additional_answers_json,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
            )
        else:  # MANUAL
            return await self._manual.apply(
                apply_url=apply_url,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
            )

    async def _record_application(
        self,
        db: AsyncSession,
        job_match_id: int,
        result: ApplicationResult,
        reserved_app_id: Optional[int] = None,
    ) -> None:
        """Persist the application and an initial lifecycle event.

        If ``reserved_app_id`` is provided (the common AUTO/ASSISTED
        path), the placeholder row inserted by
        :py:meth:`DailyLimitGuard.reserve_slot` is updated in place —
        we never insert a duplicate. Otherwise (MANUAL) a fresh row is
        inserted.
        """
        try:
            from datetime import datetime

            from sqlalchemy import select

            from backend.models.job import JobMatch

            # Translate the strategy-outcome status into the canonical
            # persisted Application.status (see ``backend.applier``
            # module docstring for the vocabulary).
            is_success = result.status in SUCCESS_RESULT_STATUSES
            persisted_status = normalize_result_status(result.status)
            applied_at_value = datetime.utcnow() if is_success else None

            if reserved_app_id is not None:
                # Update the placeholder reserved by the daily-limit guard.
                stmt = select(Application).where(Application.id == reserved_app_id)
                app = (await db.execute(stmt)).scalar_one_or_none()
                if app is None:
                    # Defensive: placeholder vanished — fall back to insert.
                    logger.warning(
                        "Reserved application id=%d not found; inserting fresh row",
                        reserved_app_id,
                    )
                    app = Application(
                        job_match_id=job_match_id,
                        method=result.method,
                        status=persisted_status,
                        applied_at=applied_at_value,
                        notes=result.message or None,
                    )
                    db.add(app)
                    await db.flush()
                else:
                    app.method = result.method
                    app.status = persisted_status
                    app.applied_at = applied_at_value
                    app.notes = result.message or None
            else:
                app = Application(
                    job_match_id=job_match_id,
                    method=result.method,
                    status=persisted_status,
                    applied_at=applied_at_value,
                    notes=result.message or None,
                )
                db.add(app)
                await db.flush()

            # Lifecycle event records the original strategy outcome
            # (e.g. "manual"/"assisted") for richer history; the
            # Application.status column holds the canonical value.
            event = ApplicationEvent(
                application_id=app.id,
                event_type=result.status,
                details=result.message or None,
            )
            db.add(event)

            # Update JobMatch status so the job is removed from the queue
            # for every success outcome (applied / manual / assisted).
            if is_success:
                stmt = select(JobMatch).where(JobMatch.id == job_match_id)
                match = (await db.execute(stmt)).scalar_one_or_none()
                if match is not None:
                    match.status = "applied"

            await db.commit()
            logger.info(
                "Recorded application id=%d job_match_id=%d result=%s status=%s",
                app.id,
                job_match_id,
                result.status,
                persisted_status,
            )
        except Exception as exc:
            logger.error("Failed to record application: %s", exc)
            await db.rollback()


__all__ = [
    "ApplicationEngine",
    "ApplyMode",
    "ApplicationResult",
    "ApplicantInfo",
]
