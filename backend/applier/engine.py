"""Application engine — routes apply requests to the right strategy."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier.manual_apply import ApplicationResult, ManualApplyStrategy
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy
from backend.applier.daily_limit import DailyLimitGuard, DailyLimitExceeded
from backend.models.application import Application, ApplicationEvent

logger = logging.getLogger(__name__)


class ApplyMode(str, Enum):
    AUTO = "auto"
    ASSISTED = "assisted"
    MANUAL = "manual"


class ApplicantInfo(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    additional_answers_json: str = ""


class ApplicationEngine:
    """Routes application requests to AUTO / ASSISTED / MANUAL strategies.

    Also enforces the daily limit and records application lifecycle events.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        daily_limit: int = 10,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._daily_limit = daily_limit

        self._auto = AutoApplyStrategy(api_key=api_key, model=model)
        self._assisted = AssistedApplyStrategy(api_key=api_key, model=model)
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

        # Enforce daily limit (skip for manual — user is in control)
        if mode != ApplyMode.MANUAL:
            guard = DailyLimitGuard(db=db, limit=self._daily_limit)
            try:
                await guard.assert_can_apply()
            except DailyLimitExceeded as exc:
                logger.warning("Daily limit exceeded: %s", exc)
                return ApplicationResult(
                    status="cancelled",
                    method=mode.value,
                    message=str(exc),
                )

        # Set up per-job events
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
        finally:
            self._confirm_events.pop(job_match_id, None)
            self._cancel_events.pop(job_match_id, None)

        # Persist application record
        await self._record_application(
            db=db,
            job_match_id=job_match_id,
            result=result,
        )

        return result

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
    ) -> None:
        """Persist the application and an initial lifecycle event."""
        try:
            from datetime import datetime

            app = Application(
                job_match_id=job_match_id,
                method=result.method,
                status=result.status,
                applied_at=datetime.utcnow() if result.status == "applied" else None,
                notes=result.message or None,
            )
            db.add(app)
            await db.flush()

            event = ApplicationEvent(
                application_id=app.id,
                event_type=result.status,
                details=result.message or None,
            )
            db.add(event)
            await db.commit()
            logger.info(
                "Recorded application id=%d job_match_id=%d status=%s",
                app.id,
                job_match_id,
                result.status,
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
