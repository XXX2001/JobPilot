"""Tests for the apply-flow FSM — backend/applier/state.py.

Exercises:
  - Statechart driver: forward walk, terminal detection, FAILED fallback
  - Per-state behavior: CANCELLED compensation, FAILED slot release,
    REMOTE_SUBMITTED_LOCAL_FAILED db_write_failed event
  - Every transition edge from the normal path
  - Error paths: on_enter raises, next() raises
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.applier.state import (
    ApplyContext,
    State,
    TERMINALS,
    Transition,
    Statechart,
)
from backend.applier import RESULT_APPLIED, RESULT_CANCELLED, RESULT_FAILED


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(
    job_match_id: int = 1,
    mode: str = "auto",
    apply_url: str = "https://example.com/job",
    db: object = None,
    reserved_app_id: int | None = None,
    confirm_event: asyncio.Event | None = None,
    cancel_event: asyncio.Event | None = None,
    outcome_status: str = RESULT_FAILED,
    outcome_method: str = "auto",
    outcome_message: str | None = None,
) -> ApplyContext:
    """Return a minimal ApplyContext with mock db."""
    if db is None:
        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
    else:
        mock_db = db  # type: ignore[assignment]
    return ApplyContext(
        job_match_id=job_match_id,
        mode=mode,
        apply_url=apply_url,
        db=mock_db,  # type: ignore[arg-type]
        reserved_app_id=reserved_app_id,
        confirm_event=confirm_event if confirm_event is not None else asyncio.Event(),
        cancel_event=cancel_event if cancel_event is not None else asyncio.Event(),
        outcome_status=outcome_status,
        outcome_method=outcome_method,
        outcome_message=outcome_message,
    )


async def _noop(ctx: ApplyContext) -> None:
    pass


async def _next_applied(ctx: ApplyContext) -> State:
    ctx.outcome_status = RESULT_APPLIED
    return State.APPLIED


async def _next_cancelled(ctx: ApplyContext) -> State:
    ctx.outcome_status = RESULT_CANCELLED
    return State.CANCELLED


async def _next_failed(ctx: ApplyContext) -> State:
    ctx.outcome_status = RESULT_FAILED
    return State.FAILED


# ── TERMINALS constant ────────────────────────────────────────────────────────


def test_terminals_contains_all_terminal_states():
    assert State.APPLIED in TERMINALS
    assert State.CANCELLED in TERMINALS
    assert State.FAILED in TERMINALS
    assert State.REMOTE_SUBMITTED_LOCAL_FAILED in TERMINALS
    assert State.RESERVED not in TERMINALS
    assert State.RECORDING not in TERMINALS


# ── Statechart — happy forward path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_statechart_forward_to_applied():
    """Linear path RESERVED → RECORDING → APPLIED returns APPLIED outcome."""
    ctx = _make_ctx(outcome_status=RESULT_APPLIED)

    async def recording_enter(c: ApplyContext) -> None:
        c.outcome_status = RESULT_APPLIED
        c.outcome_method = "auto"

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(on_enter=recording_enter, next=_next_applied),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    status, method, message = await chart.run(ctx)

    assert status == RESULT_APPLIED
    assert chart.state == State.APPLIED


@pytest.mark.asyncio
async def test_statechart_direct_to_cancelled():
    """next() returning CANCELLED routes to CANCELLED terminal."""
    ctx = _make_ctx()

    async def recording_next(c: ApplyContext) -> State:
        c.outcome_status = RESULT_CANCELLED
        return State.CANCELLED

    cancelled_entered = []

    async def cancelled_enter(c: ApplyContext) -> None:
        cancelled_entered.append(True)

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(next=recording_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=cancelled_enter),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    status, method, message = await chart.run(ctx)

    assert chart.state == State.CANCELLED
    assert cancelled_entered == [True], "CANCELLED on_enter must run once"


@pytest.mark.asyncio
async def test_statechart_next_raises_transitions_to_failed():
    """If next() raises an exception, driver transitions to FAILED."""
    ctx = _make_ctx()

    async def bad_next(c: ApplyContext) -> State:
        raise RuntimeError("simulated strategy crash")

    failed_entered = []

    async def failed_enter(c: ApplyContext) -> None:
        failed_entered.append(True)

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(next=bad_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=failed_enter),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    status, method, message = await chart.run(ctx)

    assert chart.state == State.FAILED
    assert failed_entered == [True], "FAILED on_enter must run when next() raises"


@pytest.mark.asyncio
async def test_statechart_on_enter_raises_transitions_to_failed():
    """If on_enter raises, driver transitions to FAILED without calling next()."""
    ctx = _make_ctx()

    async def bad_enter(c: ApplyContext) -> None:
        raise ValueError("on_enter blew up")

    next_called = []

    async def recording_next(c: ApplyContext) -> State:
        next_called.append(True)
        return State.APPLIED

    failed_entered = []

    async def failed_enter(c: ApplyContext) -> None:
        failed_entered.append(True)

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(on_enter=bad_enter, next=recording_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=failed_enter),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert chart.state == State.FAILED
    assert next_called == [], "next() must NOT be called after on_enter raises"
    assert failed_entered == [True]


@pytest.mark.asyncio
async def test_statechart_on_exit_failure_does_not_prevent_transition():
    """on_exit failure is non-fatal — transition still proceeds."""
    ctx = _make_ctx()

    async def bad_exit(c: ApplyContext) -> None:
        raise RuntimeError("on_exit boom")

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    async def recording_next(c: ApplyContext) -> State:
        c.outcome_status = RESULT_APPLIED
        return State.APPLIED

    applied_entered = []

    async def applied_enter(c: ApplyContext) -> None:
        applied_entered.append(True)

    transitions = {
        State.RESERVED: Transition(next=reserved_next, on_exit=bad_exit),
        State.RECORDING: Transition(next=recording_next),
        State.APPLIED: Transition(on_enter=applied_enter),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    status, method, message = await chart.run(ctx)

    # Despite on_exit raising, we reach APPLIED.
    assert chart.state == State.APPLIED
    assert applied_entered == [True]


@pytest.mark.asyncio
async def test_statechart_missing_transition_goes_to_failed():
    """A state with no entry in the transition table causes FAILED."""
    ctx = _make_ctx()

    # RESERVED has no entry
    transitions: dict[State, Transition] = {
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert chart.state == State.FAILED


# ── ApplyContext ──────────────────────────────────────────────────────────────


def test_apply_context_defaults():
    """ApplyContext initialises with sane defaults."""
    ctx = _make_ctx()
    assert ctx.job_match_id == 1
    assert ctx.reserved_app_id is None
    assert ctx.strategy_result is None
    assert ctx.outcome_status == RESULT_FAILED
    assert ctx.extras == {}


# ── Compensation paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_compensation_releases_slot():
    """CANCELLED terminal on_enter calls recorder.release_reserved_slot."""
    from backend.applier.recorder import ApplicationRecorder

    recorder = ApplicationRecorder()
    release_calls = []

    async def fake_release(db, reserved_app_id):
        release_calls.append(reserved_app_id)

    recorder.release_reserved_slot = fake_release  # type: ignore[method-assign]

    ctx = _make_ctx(reserved_app_id=42)

    async def recording_next(c: ApplyContext) -> State:
        c.outcome_status = RESULT_CANCELLED
        return State.CANCELLED

    async def cancelled_enter(c: ApplyContext) -> None:
        if c.reserved_app_id is not None:
            await recorder.release_reserved_slot(c.db, c.reserved_app_id)

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(next=recording_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=cancelled_enter),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert release_calls == [42], "release_reserved_slot must be called with reserved_app_id"


@pytest.mark.asyncio
async def test_failed_compensation_releases_slot():
    """FAILED terminal on_enter releases the reserved slot."""
    released = []

    async def reserved_next(c: ApplyContext) -> State:
        return State.RECORDING

    async def recording_next(c: ApplyContext) -> State:
        raise RuntimeError("strategy crashed")

    async def failed_enter(c: ApplyContext) -> None:
        if c.reserved_app_id is not None:
            released.append(c.reserved_app_id)

    ctx = _make_ctx(reserved_app_id=7)

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.RECORDING: Transition(next=recording_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=failed_enter),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert chart.state == State.FAILED
    assert released == [7]


@pytest.mark.asyncio
async def test_remote_submitted_local_failed_terminal():
    """REMOTE_SUBMITTED_LOCAL_FAILED on_enter runs without crashing."""
    entered = []

    async def rslf_enter(c: ApplyContext) -> None:
        entered.append("rslf")

    async def reserved_next(c: ApplyContext) -> State:
        return State.REMOTE_SUBMITTED_LOCAL_FAILED

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=rslf_enter),
    }

    ctx = _make_ctx()
    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert chart.state == State.REMOTE_SUBMITTED_LOCAL_FAILED
    assert entered == ["rslf"]


# ── State enum completeness ───────────────────────────────────────────────────


def test_all_states_defined():
    """Every expected state exists in the State enum."""
    expected = {
        "RESERVED",
        "CAPTCHA_CHECK",
        "FILLING",
        "AWAITING_CONFIRM",
        "SUBMITTING",
        "RECORDING",
        "APPLIED",
        "CANCELLED",
        "FAILED",
        "REMOTE_SUBMITTED_LOCAL_FAILED",
    }
    actual = {s.name for s in State}
    assert expected == actual, f"State enum mismatch: {expected ^ actual}"


def test_state_values_are_strings():
    """State enum values are lowercase strings (for serialisation)."""
    for s in State:
        assert isinstance(s.value, str)
        assert s.value == s.value.lower()


# ── Full linear pipeline walks every middle state ────────────────────────────


@pytest.mark.asyncio
async def test_full_linear_pipeline_visits_all_middle_states():
    """RESERVED → CAPTCHA_CHECK → FILLING → AWAITING_CONFIRM → SUBMITTING → RECORDING → APPLIED.

    Verifies that all four middle states are entered in order and the chart
    reaches APPLIED.  The strategy _dispatch is not involved — on_enter for
    each middle state just records a visit; next() advances linearly.
    """
    visited: list[State] = []

    def _make_enter(state: State):
        async def on_enter(c: ApplyContext) -> None:
            visited.append(state)
        return on_enter

    async def next_captcha(c: ApplyContext) -> State:
        return State.FILLING

    async def next_filling(c: ApplyContext) -> State:
        return State.AWAITING_CONFIRM

    async def next_awaiting(c: ApplyContext) -> State:
        return State.SUBMITTING

    async def next_submitting(c: ApplyContext) -> State:
        return State.RECORDING

    async def next_recording(c: ApplyContext) -> State:
        c.outcome_status = RESULT_APPLIED
        return State.APPLIED

    async def reserved_next(c: ApplyContext) -> State:
        return State.CAPTCHA_CHECK

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.CAPTCHA_CHECK: Transition(on_enter=_make_enter(State.CAPTCHA_CHECK), next=next_captcha),
        State.FILLING: Transition(on_enter=_make_enter(State.FILLING), next=next_filling),
        State.AWAITING_CONFIRM: Transition(on_enter=_make_enter(State.AWAITING_CONFIRM), next=next_awaiting),
        State.SUBMITTING: Transition(on_enter=_make_enter(State.SUBMITTING), next=next_submitting),
        State.RECORDING: Transition(on_enter=_make_enter(State.RECORDING), next=next_recording),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=_noop),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    ctx = _make_ctx()
    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    status, _method, _msg = await chart.run(ctx)

    assert chart.state == State.APPLIED
    assert status == RESULT_APPLIED
    # All four middle states must have been entered, in the correct order
    assert visited == [
        State.CAPTCHA_CHECK,
        State.FILLING,
        State.AWAITING_CONFIRM,
        State.SUBMITTING,
        State.RECORDING,
    ], f"Unexpected visit sequence: {visited}"


@pytest.mark.asyncio
async def test_middle_states_are_not_terminals():
    """CAPTCHA_CHECK, FILLING, AWAITING_CONFIRM, and SUBMITTING are not in TERMINALS."""
    for state in (State.CAPTCHA_CHECK, State.FILLING, State.AWAITING_CONFIRM, State.SUBMITTING):
        assert state not in TERMINALS, f"{state} should not be a terminal"


# ── Browser lifecycle (T4a items 4 & 5) ──────────────────────────────────────


class _FakeBrowser:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _DummyApplicant:
    full_name = email = phone = location = additional_answers_json = ""


@pytest.mark.asyncio
async def test_dispatch_copies_strategy_browser_onto_ctx(monkeypatch):
    from unittest.mock import AsyncMock

    from backend.applier.engine import ApplicationEngine, ApplyMode
    from backend.applier.manual_apply import ApplicationResult
    from backend.applier.state import ApplyContext

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    fake = _FakeBrowser()
    engine._auto._active_browser = fake
    engine._auto.apply = AsyncMock(
        return_value=ApplicationResult(status="applied", method="auto")
    )

    ctx = ApplyContext(
        job_match_id=1, mode="auto", apply_url="https://x", db=None,
        extras={"mode": ApplyMode.AUTO, "applicant": _DummyApplicant(), "cv_pdf": None, "letter_pdf": None},
    )
    await engine._dispatch(ctx)
    assert ctx.browser is fake


def _build_ctx(engine, mode: str):
    from backend.applier.engine import ApplyMode
    from backend.applier.state import ApplyContext
    return ApplyContext(
        job_match_id=1, mode=mode, apply_url="https://x", db=None,
        extras={"mode": ApplyMode(mode)},
    )


@pytest.mark.asyncio
async def test_failed_terminal_closes_browser():
    from backend.applier.engine import ApplicationEngine
    from backend.applier.state import State

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    ctx = _build_ctx(engine, "auto")
    ctx.browser = _FakeBrowser()
    transitions = engine._build_transitions(ctx)
    await transitions[State.FAILED].on_enter(ctx)
    assert ctx.browser.stopped is True


@pytest.mark.asyncio
async def test_cancelled_terminal_closes_browser():
    from backend.applier.engine import ApplicationEngine
    from backend.applier.state import State

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    ctx = _build_ctx(engine, "auto")
    ctx.browser = _FakeBrowser()
    transitions = engine._build_transitions(ctx)
    await transitions[State.CANCELLED].on_enter(ctx)
    assert ctx.browser.stopped is True


@pytest.mark.asyncio
async def test_assisted_success_leaves_browser_open():
    from backend.applier.engine import ApplicationEngine
    from backend.applier.state import State

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    ctx = _build_ctx(engine, "assisted")
    ctx.browser = _FakeBrowser()
    transitions = engine._build_transitions(ctx)
    await transitions[State.APPLIED].on_enter(ctx)
    assert ctx.browser.stopped is False


@pytest.mark.asyncio
async def test_auto_success_closes_browser():
    from backend.applier.engine import ApplicationEngine
    from backend.applier.state import State

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    ctx = _build_ctx(engine, "auto")
    ctx.browser = _FakeBrowser()
    transitions = engine._build_transitions(ctx)
    await transitions[State.APPLIED].on_enter(ctx)
    assert ctx.browser.stopped is True


@pytest.mark.asyncio
async def test_full_pipeline_fails_partway_through_middle_state():
    """If a middle-state next() raises, driver should land in FAILED.

    Specifically: RESERVED → CAPTCHA_CHECK → FILLING (next raises) → FAILED.
    """
    failed_entered = []

    async def reserved_next(c: ApplyContext) -> State:
        return State.CAPTCHA_CHECK

    async def captcha_next(c: ApplyContext) -> State:
        return State.FILLING

    async def filling_next_boom(c: ApplyContext) -> State:
        raise RuntimeError("filling failed mid-pipeline")

    async def failed_enter(c: ApplyContext) -> None:
        failed_entered.append(True)

    transitions = {
        State.RESERVED: Transition(next=reserved_next),
        State.CAPTCHA_CHECK: Transition(next=captcha_next),
        State.FILLING: Transition(next=filling_next_boom),
        State.AWAITING_CONFIRM: Transition(next=_next_applied),
        State.SUBMITTING: Transition(next=_next_applied),
        State.RECORDING: Transition(next=_next_applied),
        State.APPLIED: Transition(on_enter=_noop),
        State.CANCELLED: Transition(on_enter=_noop),
        State.FAILED: Transition(on_enter=failed_enter),
        State.REMOTE_SUBMITTED_LOCAL_FAILED: Transition(on_enter=_noop),
    }

    ctx = _make_ctx()
    chart = Statechart(transitions=transitions, initial=State.RESERVED)
    await chart.run(ctx)

    assert chart.state == State.FAILED
    assert failed_entered == [True], "FAILED on_enter must fire when a middle state next() raises"
