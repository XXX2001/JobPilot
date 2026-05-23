"""Apply-flow finite state machine.

Defines the State enum, Context dataclass, Transition dataclass, and
Statechart driver that govern the application lifecycle inside
:class:`~backend.applier.engine.ApplicationEngine`.

States (linear forward path + 4 terminals)::

    Reserved → CaptchaCheck → Filling → AwaitingConfirm → Submitting
        → Recording → Applied
                    ↘ Cancelled
                    ↘ Failed
                    ↘ RemoteSubmittedLocalFailed

Compensation paths (preserved exactly):
- Cancelled  → release daily-limit slot via DailyLimitGuard
- Failed     → release slot + close browser cleanly
- RemoteSubmittedLocalFailed → record ApplicationEvent(event_type="db_write_failed")

Design constraints:
- No third-party FSM library. Plain Python dataclasses + driver class.
- No new ``# type: ignore`` comments.
- No ``_``-prefix on unused vars — use ``del`` style.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    # BrowserSession is the concrete browser type from browser-use.
    # Imported under TYPE_CHECKING only to avoid the heavy browser_use import
    # at runtime — the attribute is always Optional and may be None.
    from browser_use.browser.session import BrowserSession

from backend.applier import RESULT_FAILED

logger = logging.getLogger(__name__)


# ── State enum ───────────────────────────────────────────────────────────────


class State(Enum):
    """Linear forward states and terminal states for the apply lifecycle."""

    RESERVED = "reserved"
    CAPTCHA_CHECK = "captcha_check"
    FILLING = "filling"
    AWAITING_CONFIRM = "awaiting_confirm"
    SUBMITTING = "submitting"
    RECORDING = "recording"
    # Terminals
    APPLIED = "applied"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REMOTE_SUBMITTED_LOCAL_FAILED = "remote_submitted_local_failed"


#: Set of terminal states — Statechart.run() stops when it reaches one.
TERMINALS: frozenset[State] = frozenset({
    State.APPLIED,
    State.CANCELLED,
    State.FAILED,
    State.REMOTE_SUBMITTED_LOCAL_FAILED,
})


# ── Context ──────────────────────────────────────────────────────────────────


@dataclass
class ApplyContext:
    """All mutable state threaded through the apply lifecycle.

    Populated by the engine before calling ``Statechart.run()``; each
    state transition reads/writes it in place.
    """

    # Inputs
    job_match_id: int
    mode: str  # "auto" | "assisted" | "manual"
    apply_url: str
    db: AsyncSession

    # Optional inputs
    reserved_app_id: Optional[int] = None
    confirm_event: Optional[asyncio.Event] = None
    cancel_event: Optional[asyncio.Event] = None

    # Written by Filling / Submitting states — typed as object to avoid
    # a circular import; callers cast via ApplicationResult attributes.
    strategy_result: Optional[object] = None  # ApplicationResult from strategy

    # Written by terminal on_enter handlers
    outcome_status: str = RESULT_FAILED
    outcome_method: str = "auto"
    outcome_message: Optional[str] = None

    # Extra: browser reference (optional, for cleanup).
    # Typed as Optional[BrowserSession] so callers can call .stop() without
    # type: ignore — the import is guarded by TYPE_CHECKING to avoid the
    # heavy browser_use import at runtime.
    browser: Optional[BrowserSession] = None

    # Free-form extras (strategy-specific)
    extras: dict = field(default_factory=dict)


# ── Transition ───────────────────────────────────────────────────────────────

#: Callable type alias for state action hooks.
StateHook = Callable[[ApplyContext], Awaitable[None]]
#: Callable type alias for the next-state decision function.
NextStateFn = Callable[[ApplyContext], Awaitable[State]]


@dataclass
class Transition:
    """Describes the hooks and next-state logic for a single state.

    ``on_enter``  — called when the state is entered (before ``next``).
    ``next``      — async callable that returns the next :class:`State`.
                    Terminal states may omit this (``None``).
    ``on_exit``   — called after ``next`` resolves, before the state changes.
    """

    on_enter: Optional[StateHook] = None
    next: Optional[NextStateFn] = None
    on_exit: Optional[StateHook] = None


# ── Statechart driver ────────────────────────────────────────────────────────


class Statechart:
    """Drive an apply lifecycle through a ``dict[State, Transition]`` table.

    Usage::

        chart = Statechart(transitions=my_table, initial=State.RESERVED)
        outcome = await chart.run(ctx)

    The driver runs the forward walk until it reaches a terminal state,
    then executes the terminal state's ``on_enter`` (compensation/cleanup)
    and returns the outcome extracted from ``ctx``.

    If any non-terminal ``next`` callable raises, the driver transitions
    to ``State.FAILED`` and runs its compensation.
    """

    def __init__(
        self,
        transitions: dict[State, Transition],
        initial: State,
    ) -> None:
        self._transitions = transitions
        self._state = initial

    @property
    def state(self) -> State:
        return self._state

    async def run(self, ctx: ApplyContext) -> tuple[str, str, Optional[str]]:
        """Run the state machine.

        Returns ``(status, method, message)`` — the three fields of
        :class:`~backend.applier.manual_apply.ApplicationResult`.
        """
        while self._state not in TERMINALS:
            t = self._transitions.get(self._state)
            if t is None:
                logger.error("No transition defined for state %s — failing", self._state)
                self._state = State.FAILED
                break

            # on_enter
            if t.on_enter is not None:
                try:
                    await t.on_enter(ctx)
                except Exception:
                    logger.exception(
                        "on_enter raised in state %s — transitioning to FAILED",
                        self._state,
                    )
                    self._state = State.FAILED
                    break

            # next
            if t.next is None:
                # Should not happen for non-terminal states; treat as FAILED.
                logger.error("No next() defined for non-terminal state %s", self._state)
                self._state = State.FAILED
                break

            try:
                next_state = await t.next(ctx)
            except Exception:
                logger.exception(
                    "next() raised in state %s — transitioning to FAILED",
                    self._state,
                )
                # on_exit is NOT called when next() raises — we jump straight to FAILED.
                self._state = State.FAILED
                break

            # on_exit (only if next() succeeded)
            if t.on_exit is not None:
                try:
                    await t.on_exit(ctx)
                except Exception:
                    logger.exception(
                        "on_exit raised in state %s — continuing to %s anyway",
                        self._state,
                        next_state,
                    )
                    # on_exit failure is non-fatal; proceed to next_state.

            self._state = next_state

        # Run the terminal state's on_enter (compensation / cleanup).
        terminal_t = self._transitions.get(self._state)
        if terminal_t is not None and terminal_t.on_enter is not None:
            try:
                await terminal_t.on_enter(ctx)
            except Exception:
                logger.exception(
                    "Terminal on_enter raised in state %s", self._state
                )

        return ctx.outcome_status, ctx.outcome_method, ctx.outcome_message


__all__ = [
    "ApplyContext",
    "State",
    "TERMINALS",
    "Transition",
    "Statechart",
]
