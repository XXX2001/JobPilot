"""Application engine — routes apply requests to the right strategy.

Refactored to delegate DB persistence to
:class:`~backend.applier.recorder.ApplicationRecorder` and use a
:class:`~backend.applier.state.Statechart` FSM for the apply lifecycle.
The public API (``apply``, ``signal_confirm``, ``signal_cancel``) is
unchanged.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import uuid
import logging
from enum import Enum
from pathlib import Path
from typing import Optional, cast

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier import RESULT_CANCELLED, RESULT_CONFIRMED, RESULT_FAILED, RESULT_NEEDS_VERIFICATION
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy
from backend.applier.daily_limit import DailyLimitExceeded, DailyLimitGuard
from backend.applier.manual_apply import ApplicationResult, ManualApplyStrategy
from backend.utils.time import utc_now
from backend.applier.recorder import ApplicationRecorder
from backend.applier.state import ApplyContext, State, Statechart, Transition
from backend.defaults import MAX_LEN_ADDITIONAL_ANSWERS, MAX_LEN_EMAIL, MAX_LEN_LOCATION, MAX_LEN_PHONE

logger = logging.getLogger(__name__)


class ApplyMode(str, Enum):
    AUTO = "auto"
    ASSISTED = "assisted"
    MANUAL = "manual"


class ApplicantInfo(BaseModel):
    full_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=MAX_LEN_EMAIL)
    phone: str = Field(default="", max_length=MAX_LEN_PHONE)
    location: str = Field(default="", max_length=MAX_LEN_LOCATION)
    additional_answers_json: str = Field(default="", max_length=MAX_LEN_ADDITIONAL_ANSWERS)


class ReviewConflict(Exception):
    """A review decision does not match the review currently awaiting one (W07)."""


class ApplicationEngine:
    """Routes application requests to AUTO / ASSISTED / MANUAL strategies.

    Enforces the daily limit, records the application lifecycle, and
    manages per-job confirm/cancel events for the WS flow.
    """

    def __init__(
        self,
        daily_limit: int | None = None,
    ) -> None:
        self._daily_limit = daily_limit

        self._auto = AutoApplyStrategy(
            on_review=self.record_pending_review,
            on_get_patches=self.get_pending_patches,
        )
        self._assisted = AssistedApplyStrategy(
            on_review=self.record_pending_review,
            on_get_patches=self.get_pending_patches,
        )
        self._manual = ManualApplyStrategy()
        self._recorder = ApplicationRecorder()

        # Per-job asyncio events for confirm/cancel coming from WS
        self._confirm_events: dict[int, asyncio.Event] = {}
        self._cancel_events: dict[int, asyncio.Event] = {}
        # Cached review snapshots so a reconnecting client can re-fetch the
        # pending apply_review payload over HTTP (engine survives no WS client).
        self._pending_reviews: dict[int, dict] = {}
        # Per-job user edits to mis-filled review fields (selector→new value),
        # applied by the form filler right before submit.
        self._pending_patches: dict[int, dict[str, str]] = {}
        # W07: per-job review revision counter and acknowledged decisions
        # (idempotency key → (fingerprint, ack)); bounded, survives the attempt
        # so a client retrying a lost response still gets its ack.
        self._review_revisions: dict[int, int] = {}
        self._decisions: "OrderedDict[tuple[int, str], tuple[tuple, dict]]" = OrderedDict()
        # job_id → reserved Application id of the live attempt (R03 midnight check).
        self._reservations: dict[int, int] = {}
        # T4a: guard the re-entrancy check (read-then-write of the events
        # dicts) so two concurrent apply() calls for the same job_match_id
        # cannot both pass the membership check before either one inserts.
        # The window is real because the API route awaits reserve_slot()
        # *before* this check; under high concurrency two requests can be
        # mid-reservation simultaneously and both arrive at the check
        # with no entry present yet.
        self._registry_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  WS signal handlers (called by ws.py on incoming client messages)  #
    # ------------------------------------------------------------------ #

    def decide(
        self,
        job_id: int,
        *,
        review_id: str,
        revision: int,
        decision: str,
        fields: dict[str, str],
        idempotency_key: str,
    ) -> dict:
        """Apply one review decision to the review awaiting it (W07 / F11).

        Approval (with its field corrections) is accepted only for the
        current snapshot of an attempt that is waiting; premature, stale,
        unknown or already-consumed reviews raise :class:`ReviewConflict`.
        A repeated identical command returns the saved acknowledgment.
        """
        fingerprint = (review_id, revision, decision, tuple(sorted(fields.items())))
        saved = self._decisions.get((job_id, idempotency_key))
        if saved is not None:
            if saved[0] != fingerprint:
                raise ReviewConflict("This idempotency key was already used for a different decision.")
            return saved[1]

        pending = self._pending_reviews.get(job_id)
        if pending is None or job_id not in self._confirm_events:
            raise ReviewConflict("No review is awaiting a decision for this job (not ready, or already decided).")
        if pending.get("review_id") != review_id or pending.get("revision") != revision:
            raise ReviewConflict("This review is out of date; reload it and review the current values.")
        if decision == "approve":
            # W08: corrections may only target fields of the server's own
            # snapshot (no client-chosen selectors), and only real changes
            # count — an unchanged echo is not a correction.
            inventory = pending.get("filled_fields") or {}
            unknown = sorted(set(fields) - set(inventory))
            if unknown:
                raise ReviewConflict(f"Unknown review field(s): {', '.join(unknown[:5])}.")
            changed = {k: v for k, v in fields.items() if inventory.get(k) != v}
            self.signal_patch_fields(job_id, changed)
            self.signal_confirm(job_id)
        elif decision == "cancel":
            self.signal_cancel(job_id)
        else:
            raise ReviewConflict(f"Unknown decision {decision!r}.")

        ack = {
            "job_id": job_id,
            "review_id": review_id,
            "revision": revision,
            "decision": decision,
            "accepted_at": utc_now().isoformat(),
        }
        self._decisions[(job_id, idempotency_key)] = (fingerprint, ack)
        while len(self._decisions) > 256:  # ponytail: in-memory; durable decisions are W14/W15
            self._decisions.popitem(last=False)
        return ack

    def reservation_for(self, job_id: int) -> Optional[int]:
        """The quota reservation of the attempt now running for *job_id*, if any."""
        return self._reservations.get(job_id)

    def signal_confirm(self, job_id: int) -> None:
        """Release the waiting strategy for *job_id*. Callers go through :meth:`decide`."""
        if job_id in self._confirm_events:
            self._confirm_events[job_id].set()
        self._pending_reviews.pop(job_id, None)
        # NOTE: do NOT purge ``_pending_patches`` here. ``event.set()`` only
        # *schedules* the waiter wakeup; the form-filler coroutine resumes
        # later and only then reads the patches via ``get_pending_patches``.
        # Popping synchronously here would clear the edits before they are
        # re-filled, making the feature a no-op. The patches are purged by
        # ``signal_cancel`` and by the ``apply()`` ``finally`` block, so no
        # stale entry leaks into a later apply for the same job_id.

    def signal_patch_fields(self, job_id: int, fields: dict[str, str]) -> None:
        """Store user-edited review fields (``patch_fields`` WS message).

        The values are re-filled by the form filler right before submit. The
        client sends this *before* ``confirm_submit``; the patches must
        survive the confirm signal so the form filler can read them after its
        wait resumes. Purge happens on cancel and in ``apply()``'s ``finally``.
        """
        self._pending_patches[job_id] = dict(fields)

    def get_pending_patches(self, job_id: int) -> dict[str, str]:
        """Return the pending patches for *job_id*, or ``{}`` if none."""
        return self._pending_patches.get(job_id, {})

    def record_pending_review(
        self, job_id: int, *, filled_fields: dict, screenshot_b64: Optional[str]
    ) -> dict:
        """Record a new review snapshot (new ``review_id`` and next revision) and return it.

        The snapshot is cached so a reconnecting client can fetch it; the
        strategy broadcasts the returned identity, and approval must name it.
        """
        revision = self._review_revisions.get(job_id, 0) + 1
        self._review_revisions[job_id] = revision
        snapshot = {
            "job_id": job_id,
            "review_id": uuid.uuid4().hex,
            "revision": revision,
            "filled_fields": filled_fields,
            "screenshot_b64": screenshot_b64,
        }
        self._pending_reviews[job_id] = snapshot
        return snapshot

    def get_pending_review(self, job_id: int) -> Optional[dict]:
        """Return the cached snapshot for *job_id*, or None."""
        return self._pending_reviews.get(job_id)

    def signal_cancel(self, job_id: int) -> None:
        """Trigger cancellation for *job_id* (``cancel_apply`` WS message)."""
        if job_id in self._cancel_events:
            self._cancel_events[job_id].set()
        self._pending_reviews.pop(job_id, None)
        self._pending_patches.pop(job_id, None)

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
            # W11: the configured limit is resolved at reservation time (the
            # constructor value only overrides it, e.g. in tests).
            guard = (
                DailyLimitGuard(db=db, limit=self._daily_limit)
                if self._daily_limit is not None
                else await DailyLimitGuard.from_settings(db)
            )
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
        # T4a: serialise the membership-check + insert under
        # ``_registry_lock`` so two concurrent calls cannot both pass the
        # check before either one inserts. ``asyncio.Lock`` is cheap and
        # uncontended in the normal single-request case.
        already_in_flight = False
        async with self._registry_lock:
            if job_match_id in self._confirm_events:
                already_in_flight = True
            else:
                self._confirm_events[job_match_id] = asyncio.Event()
                self._cancel_events[job_match_id] = asyncio.Event()

        if already_in_flight:
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

        if reserved_app_id is not None:
            self._reservations[job_match_id] = reserved_app_id

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
        except BaseException:
            # Abnormal exit (incl. asyncio.CancelledError, which the FSM's
            # ``except Exception`` does NOT catch) bypasses the terminal
            # compensation, so close the live browser here before propagating.
            # Safe: an exception path is never the assisted-success case
            # (that returns normally), so we never close a browser the user
            # still needs.
            if ctx.browser is not None:
                try:
                    await ctx.browser.stop()
                except Exception:
                    pass
            raise
        finally:
            self._confirm_events.pop(job_match_id, None)
            self._cancel_events.pop(job_match_id, None)
            self._pending_reviews.pop(job_match_id, None)
            self._pending_patches.pop(job_match_id, None)
            self._reservations.pop(job_match_id, None)

        sr = ctx.strategy_result
        extra = (
            {"evidence": sr.evidence, "next_action_url": sr.next_action_url}
            if isinstance(sr, ApplicationResult) and sr.status == status
            else {}
        )
        return ApplicationResult(status=status, method=method, message=message or "", **extra)

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

        async def _close_browser(c: ApplyContext) -> None:
            if c.browser is not None:
                try:
                    await c.browser.stop()
                except Exception:
                    pass  # best-effort; never shadow the outcome

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
                    evidence=result.evidence,
                )
            except Exception as exc:
                logger.exception("record() failed after strategy returned %s", result.status)
                possibly_submitted = result.status in (RESULT_CONFIRMED, RESULT_NEEDS_VERIFICATION)
                # Review I5: a remote submission that may have happened keeps
                # its reservation (the pending row still counts) — releasing it
                # would let a retry duplicate the submission. Only an outcome
                # that cannot have submitted (prepared for the user) releases.
                if c.reserved_app_id is not None and not possibly_submitted:
                    try:
                        await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                    except Exception:
                        pass  # Already logged inside release_reserved_slot.
                # ANY exception here routes to REMOTE_SUBMITTED_LOCAL_FAILED so the
                # db_write_failed audit event is always persisted (and the caller is
                # told to verify on the job site).
                c.outcome_status = RESULT_NEEDS_VERIFICATION if possibly_submitted else RESULT_FAILED
                c.outcome_method = result.method
                c.outcome_message = (
                    "Application may have been submitted to the remote site, "
                    "but recording it locally failed: "
                    f"{exc}. Check the job site before applying again."
                )
                return State.REMOTE_SUBMITTED_LOCAL_FAILED

            return State.APPLIED

        # ── APPLIED (terminal) ─────────────────────────────────────────
        async def applied_on_enter(c: ApplyContext) -> None:
            # Auto-apply submitted and is done — close its browser. Assisted
            # success intentionally leaves the browser open so the user can
            # review and submit manually.
            if c.mode != ApplyMode.ASSISTED.value:
                await _close_browser(c)

        # ── CANCELLED (terminal) ───────────────────────────────────────
        async def cancelled_on_enter(c: ApplyContext) -> None:
            """Release daily-limit slot + close browser on cancellation."""
            if c.reserved_app_id is not None:
                try:
                    await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                except Exception:
                    pass  # Already logged; don't shadow the cancel outcome.
            await _close_browser(c)

        # ── FAILED (terminal) ──────────────────────────────────────────
        async def failed_on_enter(c: ApplyContext) -> None:
            """Release slot + close browser cleanly."""
            if c.reserved_app_id is not None:
                try:
                    await recorder.release_reserved_slot(c.db, c.reserved_app_id)
                except Exception:
                    pass  # Already logged.
            await _close_browser(c)
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
            # Auto-apply already submitted remotely; its browser is no longer
            # needed, so close it the same mode-aware way as APPLIED. Assisted
            # keeps the browser open so the user can still review/submit even
            # though the local record failed.
            if c.mode != ApplyMode.ASSISTED.value:
                await _close_browser(c)

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
            # Copy the live browser onto ctx in a finally so the FSM/cleanup
            # net can close it even when apply() raises or is cancelled before
            # returning (otherwise the handle would leak).
            try:
                result = await self._auto.apply(
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
            finally:
                ctx.browser = getattr(self._auto, "_active_browser", None)
            return result
        if mode == ApplyMode.ASSISTED:
            try:
                result = await self._assisted.apply(
                    apply_url=ctx.apply_url,
                    full_name=applicant.full_name,
                    email=applicant.email,
                    phone=applicant.phone,
                    location=applicant.location,
                    additional_answers=applicant.additional_answers_json,
                    cv_pdf=cv_pdf,
                    letter_pdf=letter_pdf,
                )
            finally:
                ctx.browser = getattr(self._assisted, "_active_browser", None)
            return result
        # MANUAL — no browser.
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
