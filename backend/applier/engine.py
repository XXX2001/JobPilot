"""Application engine — routes apply requests to the right strategy.

Refactored to delegate DB persistence to
:class:`~backend.applier.recorder.ApplicationRecorder` and use a
:class:`~backend.applier.state.Statechart` FSM for the apply lifecycle.
The public API (``apply``, ``signal_confirm``, ``signal_cancel``) is
unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Optional, cast

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier import RESULT_CANCELLED, RESULT_FAILED
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy
from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard
from backend.applier.manual_apply import ApplicationResult, ManualApplyStrategy
from backend.applier.recorder import ApplicationRecorder
from backend.applier.state import ApplyContext, State, Statechart, Transition
from backend.config import settings
from backend.defaults import DAILY_LIMIT, MAX_LEN_ADDITIONAL_ANSWERS, MAX_LEN_EMAIL, MAX_LEN_LOCATION, MAX_LEN_PHONE

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

    Enforces the daily limit, records the application lifecycle, and
    manages per-job confirm/cancel events for the WS flow.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        daily_limit: int = DAILY_LIMIT,
    ) -> None:
        self._api_key = api_key
        self._model = model or settings.GOOGLE_MODEL
        self._daily_limit = daily_limit

        self._auto = AutoApplyStrategy(api_key=api_key, model=self._model)
        self._assisted = AssistedApplyStrategy(api_key=api_key, model=self._model)
        self._manual = ManualApplyStrategy()
        self._recorder = ApplicationRecorder()

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

        # ── Daily-limit reservation (non-MANUAL only) ───────────────────
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

        # ── Guard against concurrent apply for the same job ────────────
        if job_match_id in self._confirm_events:
            if reserved_app_id is not None:
                try:
                    await self._recorder.release_reserved_slot(db, reserved_app_id)
                except Exception:
                    pass  # Already logged inside the helper.
            return ApplicationResult(
                status=RESULT_CANCELLED,
                method=mode.value,
                message=f"Job {job_match_id} already has an application in progress.",
            )

        self._confirm_events[job_match_id] = asyncio.Event()
        self._cancel_events[job_match_id] = asyncio.Event()

        # ── Build FSM context ───────────────────────────────────────────
        ctx = ApplyContext(
            job_match_id=job_match_id,
            mode=mode.value,
            apply_url=apply_url,
            db=db,
            reserved_app_id=reserved_app_id,
            confirm_event=self._confirm_events.get(job_match_id),
            cancel_event=self._cancel_events.get(job_match_id),
            outcome_status=RESULT_FAILED,
            outcome_method=mode.value,
            outcome_message=None,
            extras={
                "applicant": applicant,
                "cv_pdf": cv_pdf,
                "letter_pdf": letter_pdf,
                "mode": mode,
            },
        )

        # ── Build per-mode transition table ─────────────────────────────
        transitions = self._build_transitions(ctx)
        chart = Statechart(transitions=transitions, initial=State.RESERVED)

        try:
            status, method, message = await chart.run(ctx)
        finally:
            self._confirm_events.pop(job_match_id, None)
            self._cancel_events.pop(job_match_id, None)

        return ApplicationResult(status=status, method=method, message=message or "")

    # ------------------------------------------------------------------ #
    #  FSM transition table builder                                        #
    # ------------------------------------------------------------------ #

    def _build_transitions(self, ctx: ApplyContext) -> dict[State, Transition]:
        """Build the transition table for the apply lifecycle FSM.

        All state logic is expressed as async closures that capture
        ``ctx`` and ``self``. The 20% divergence between AUTO/ASSISTED
        and MANUAL lives in the ``_dispatch_state`` function.
        """
        recorder = self._recorder

        # ── RESERVED ──────────────────────────────────────────────────
        async def reserved_next(c: ApplyContext) -> State:
            # Slot already reserved by the time we enter this state.
            # Route through the observable middle states so the full lifecycle
            # is visible to tests, monitoring, and future per-state hooks.
            return State.CAPTCHA_CHECK

        # ── CAPTCHA_CHECK (pass-through) ───────────────────────────────
        # NOTE: real captcha-detection work lives in the strategy _dispatch.
        # This state exists to (a) make the lifecycle observable in the FSM
        # transition log and (b) provide a hook point for future per-state
        # interception (e.g. pause-and-wait for a human captcha solver).
        async def captcha_check_on_enter(c: ApplyContext) -> None:
            logger.debug("entering %s", State.CAPTCHA_CHECK)

        async def captcha_check_next(c: ApplyContext) -> State:
            return State.FILLING

        # ── FILLING (pass-through) ─────────────────────────────────────
        # NOTE: the actual form-filling work is done by the strategy in
        # _dispatch (called from RECORDING's on_enter).  This state exists
        # to (a) make the lifecycle observable and (b) allow future hooks
        # such as injecting per-field validation before submission.
        async def filling_on_enter(c: ApplyContext) -> None:
            logger.debug("entering %s", State.FILLING)

        async def filling_next(c: ApplyContext) -> State:
            return State.AWAITING_CONFIRM

        # ── AWAITING_CONFIRM (pass-through) ───────────────────────────
        # NOTE: the confirm/cancel wait is handled inside the strategy.
        # This state exists to (a) make the lifecycle observable and
        # (b) allow future interception (e.g. per-application review UI).
        async def awaiting_confirm_on_enter(c: ApplyContext) -> None:
            logger.debug("entering %s", State.AWAITING_CONFIRM)

        async def awaiting_confirm_next(c: ApplyContext) -> State:
            return State.SUBMITTING

        # ── SUBMITTING (pass-through) ──────────────────────────────────
        # NOTE: the actual submit click is performed by the strategy in
        # _dispatch.  This state exists to (a) make the lifecycle observable
        # and (b) allow future hooks such as rate-limiting or audit logging
        # immediately before a form is submitted.
        async def submitting_on_enter(c: ApplyContext) -> None:
            logger.debug("entering %s", State.SUBMITTING)

        async def submitting_next(c: ApplyContext) -> State:
            return State.RECORDING

        # ── RECORDING ─────────────────────────────────────────────────
        async def recording_on_enter(c: ApplyContext) -> None:
            """Dispatch to the appropriate strategy and capture the result."""
            result = await self._dispatch(c)
            c.strategy_result = result
            c.outcome_status = result.status
            c.outcome_method = result.method
            c.outcome_message = result.message or None

        async def recording_next(c: ApplyContext) -> State:
            raw = c.strategy_result
            if raw is None:
                return State.FAILED

            # Cast from Optional[object] to ApplicationResult — safe because
            # recording_on_enter always assigns an ApplicationResult or leaves None.
            result = cast(ApplicationResult, raw)

            # Decide terminal state based on strategy outcome
            if result.status == RESULT_CANCELLED:
                return State.CANCELLED
            if result.status == RESULT_FAILED:
                return State.FAILED

            # Success path — persist the record
            try:
                await recorder.record(
                    db=c.db,
                    job_match_id=c.job_match_id,
                    result_status=result.status,
                    result_method=result.method,
                    result_message=result.message or None,
                    reserved_app_id=c.reserved_app_id,
                )
            except Exception as exc:
                logger.exception("record() failed after strategy returned %s", result.status)
                # EH-03: remote may have submitted but local record failed.
                # Release the reserved slot (best-effort).
                if c.reserved_app_id is not None:
                    try:
                        await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                    except Exception:
                        pass  # Already logged inside release_reserved_slot.
                # EH-03: remote submit succeeded but local record failed.
                # ANY exception here routes to REMOTE_SUBMITTED_LOCAL_FAILED so the
                # db_write_failed audit event is always persisted (and the caller is
                # told to verify on the job site).  Previously only ApplicationRecordError
                # was checked — that left generic exceptions silently routing to FAILED
                # and losing the audit trail.
                c.outcome_status = RESULT_FAILED
                c.outcome_method = result.method
                c.outcome_message = (
                    "Application may have been submitted to the remote site, "
                    "but recording it locally failed: "
                    f"{exc}. Please verify on the job site and retry if needed."
                )
                return State.REMOTE_SUBMITTED_LOCAL_FAILED

            return State.APPLIED

        # ── APPLIED (terminal) ─────────────────────────────────────────
        async def applied_on_enter(c: ApplyContext) -> None:
            # Nothing extra — outcome already set in recording_next.
            pass

        # ── CANCELLED (terminal) ───────────────────────────────────────
        async def cancelled_on_enter(c: ApplyContext) -> None:
            """Release daily-limit slot on cancellation."""
            if c.reserved_app_id is not None:
                try:
                    await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                except Exception:
                    pass  # Already logged; don't shadow the cancel outcome.

        # ── FAILED (terminal) ──────────────────────────────────────────
        async def failed_on_enter(c: ApplyContext) -> None:
            """Release slot + best-effort browser cleanup."""
            if c.reserved_app_id is not None:
                try:
                    await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                except Exception:
                    pass  # Already logged.
            # Close browser if one was left open
            if c.browser is not None:
                try:
                    await c.browser.stop()
                except Exception:
                    pass
            if c.outcome_status != RESULT_FAILED:
                c.outcome_status = RESULT_FAILED

        # ── REMOTE_SUBMITTED_LOCAL_FAILED (terminal) ───────────────────
        async def rslf_on_enter(c: ApplyContext) -> None:
            """EH-03: record db_write_failed event (best-effort)."""
            # The normal db.commit() already failed, so we attempt a
            # new minimal write in a fresh implicit transaction.
            try:
                from backend.models.application import ApplicationEvent

                event = ApplicationEvent(
                    application_id=c.reserved_app_id,
                    event_type="db_write_failed",
                    details=c.outcome_message,
                )
                c.db.add(event)
                await c.db.commit()
            except Exception:
                logger.exception(
                    "Could not persist db_write_failed event for job_match_id=%d",
                    c.job_match_id,
                )

        return {
            State.RESERVED: Transition(
                on_enter=None,
                next=reserved_next,
                on_exit=None,
            ),
            State.CAPTCHA_CHECK: Transition(
                on_enter=captcha_check_on_enter,
                next=captcha_check_next,
            ),
            State.FILLING: Transition(
                on_enter=filling_on_enter,
                next=filling_next,
            ),
            State.AWAITING_CONFIRM: Transition(
                on_enter=awaiting_confirm_on_enter,
                next=awaiting_confirm_next,
            ),
            State.SUBMITTING: Transition(
                on_enter=submitting_on_enter,
                next=submitting_next,
            ),
            State.RECORDING: Transition(
                on_enter=recording_on_enter,
                next=recording_next,
                on_exit=None,
            ),
            # Terminals
            State.APPLIED: Transition(on_enter=applied_on_enter),
            State.CANCELLED: Transition(on_enter=cancelled_on_enter),
            State.FAILED: Transition(on_enter=failed_on_enter),
            State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=rslf_on_enter),
        }

    # ------------------------------------------------------------------ #
    #  Strategy dispatch (called from FSM RECORDING state)                #
    # ------------------------------------------------------------------ #

    async def _dispatch(self, ctx: ApplyContext) -> ApplicationResult:
        """Dispatch to the appropriate strategy based on ctx.mode."""
        mode: ApplyMode = ctx.extras["mode"]
        applicant: ApplicantInfo = ctx.extras["applicant"]
        cv_pdf: Optional[Path] = ctx.extras["cv_pdf"]
        letter_pdf: Optional[Path] = ctx.extras["letter_pdf"]

        if mode == ApplyMode.AUTO:
            return await self._auto.apply(
                job_id=ctx.job_match_id,
                apply_url=ctx.apply_url,
                full_name=applicant.full_name,
                email=applicant.email,
                phone=applicant.phone,
                location=applicant.location,
                additional_answers=applicant.additional_answers_json,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
                confirm_event=ctx.confirm_event,
                cancel_event=ctx.cancel_event,
            )
        if mode == ApplyMode.ASSISTED:
            return await self._assisted.apply(
                apply_url=ctx.apply_url,
                full_name=applicant.full_name,
                email=applicant.email,
                phone=applicant.phone,
                location=applicant.location,
                additional_answers=applicant.additional_answers_json,
                cv_pdf=cv_pdf,
                letter_pdf=letter_pdf,
            )
        # MANUAL
        return await self._manual.apply(
            apply_url=ctx.apply_url,
            cv_pdf=cv_pdf,
            letter_pdf=letter_pdf,
        )


__all__ = [
    "ApplicationEngine",
    "ApplyMode",
    "ApplicationResult",
    "ApplicantInfo",
]
