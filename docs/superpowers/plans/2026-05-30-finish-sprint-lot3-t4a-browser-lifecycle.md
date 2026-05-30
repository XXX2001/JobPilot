# Finish Sprint — Lot 3 (T4a items 4 & 5): Applier FSM Browser Lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ApplyContext.browser` actually carry the live Tier-2 browser and centralize failure-path browser cleanup inside the FSM, so the existing (currently dead) `failed_on_enter` compensation runs and the strategies stop hand-rolling cleanup — while still leaving the assisted-success browser open.

**Architecture:** Strategies expose the browser they create on `self._active_browser`; the engine's `_dispatch` copies it onto `ctx.browser`. The FSM closes the browser on `FAILED` and `CANCELLED`, and on `APPLIED` only when the mode is not assisted. Redundant `browser.stop()` calls in the strategies' failure/timeout/cancel paths are removed once the FSM covers them.

**Tech Stack:** asyncio, plain-Python FSM (`backend/applier/state.py`), browser-use, pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-30-finish-inflight-sprint-design.md` §7.

**Independent of Lots 1 and 2** — may land in any order relative to them, but the spec sequences it last.

---

## File Structure

- Modify: `backend/applier/state.py:36-40,106-110` — fix the browser type so `.stop()` is callable without `type: ignore`.
- Modify: `backend/applier/auto_apply.py:59-70,303,347-439` — expose `_active_browser`, reset it per call, drop redundant stops.
- Modify: `backend/applier/assisted_apply.py:53-63,163,181-201` — expose `_active_browser`, reset it per call, drop the redundant stop on the agent-failure path.
- Modify: `backend/applier/engine.py:65-68,318-339,402-439` — wire `ctx.browser` in `_dispatch`; add a shared close helper; close on `CANCELLED` and mode-aware `APPLIED`.
- Modify: `tests/test_apply_state.py` — add browser-lifecycle tests.

---

## Task 1: Resolve the `ApplyContext.browser` type

**Files:** Modify `backend/applier/state.py:36-40,106-110`
**Test:** none (type-only); verified by `pyright` in Task 4.

- [ ] **Step 1: Use the concrete `Browser` type the strategies create**

The strategies instantiate `browser_use.Browser`, not `BrowserSession`. Align the field so `ctx.browser.stop()` type-checks. In `backend/applier/state.py`:

```python
if TYPE_CHECKING:
    # Browser is the concrete type the Tier-2 strategies instantiate
    # (auto_apply / assisted_apply use ``from browser_use import Browser``).
    # Imported under TYPE_CHECKING only to avoid the heavy runtime import.
    from browser_use import Browser
```

```python
    # Extra: live Tier-2 browser (set by the engine from the strategy after
    # dispatch, for centralized cleanup in terminal states). None for Tier-1
    # and manual flows.
    browser: Optional["Browser"] = None
```

- [ ] **Step 2: Verify import + type-check**

Run: `uv run python -c "import backend.applier.state" && uv run pyright backend/applier/state.py`
Expected: import OK; pyright reports no new errors for this file.

- [ ] **Step 3: Commit**

```bash
git add backend/applier/state.py
git commit -m "refactor(T4a): type ApplyContext.browser as browser_use.Browser"
```

---

## Task 2: Expose the live browser from the strategies

**Files:** Modify `backend/applier/auto_apply.py`, `backend/applier/assisted_apply.py`

- [ ] **Step 1: Add a failing test for the dispatch wiring**

Add to `tests/test_apply_state.py`:

```python
class _FakeBrowser:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


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


class _DummyApplicant:
    full_name = email = phone = location = additional_answers_json = ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_apply_state.py::test_dispatch_copies_strategy_browser_onto_ctx -q`
Expected: FAIL — `AttributeError: 'AutoApplyStrategy' object has no attribute '_active_browser'` (or `ctx.browser is None`).

- [ ] **Step 3: Add `_active_browser` to both strategies**

In `AutoApplyStrategy.__init__` and `AssistedApplyStrategy.__init__`, add at the end:

```python
        # Live Tier-2 browser for the current apply() call, surfaced to the
        # engine so the FSM owns failure-path cleanup. Reset per apply().
        self._active_browser = None
```

At the **top of each `apply(...)` method body** (first line after the docstring), reset it so a reused strategy instance does not leak a stale handle:

```python
        self._active_browser = None
```

Immediately after each `browser = Browser(**browser_kwargs)` line (auto_apply.py:303 and assisted_apply.py:163), record the handle:

```python
        browser = Browser(**browser_kwargs)
        self._active_browser = browser
```

- [ ] **Step 4: Wire it in the engine `_dispatch`**

In `backend/applier/engine.py`, at the end of `_dispatch`, set `ctx.browser` from whichever strategy ran (before returning each result). The simplest single point: capture the result, set `ctx.browser`, then return. Replace the three `return await self._xxx.apply(...)` with assignments:

```python
    async def _dispatch(self, ctx: ApplyContext) -> ApplicationResult:
        """Dispatch to the appropriate strategy based on ctx.mode."""
        mode: ApplyMode = ctx.extras["mode"]
        applicant: ApplicantInfo = ctx.extras["applicant"]
        cv_pdf: Optional[Path] = ctx.extras["cv_pdf"]
        letter_pdf: Optional[Path] = ctx.extras["letter_pdf"]

        if mode == ApplyMode.AUTO:
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
            ctx.browser = getattr(self._auto, "_active_browser", None)
            return result
        if mode == ApplyMode.ASSISTED:
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
            ctx.browser = getattr(self._assisted, "_active_browser", None)
            return result
        # MANUAL — no browser.
        return await self._manual.apply(
            apply_url=ctx.apply_url,
            cv_pdf=cv_pdf,
            letter_pdf=letter_pdf,
        )
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/test_apply_state.py::test_dispatch_copies_strategy_browser_onto_ctx -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/applier/auto_apply.py backend/applier/assisted_apply.py backend/applier/engine.py tests/test_apply_state.py
git commit -m "feat(T4a-4): surface Tier-2 browser to ApplyContext.browser via _dispatch"
```

---

## Task 3: Centralize browser cleanup in the FSM terminals

**Files:** Modify `backend/applier/engine.py:318-339,392-396`, `backend/applier/auto_apply.py:347-439`, `backend/applier/assisted_apply.py:181-201`

- [ ] **Step 1: Add failing FSM-cleanup tests**

Add to `tests/test_apply_state.py`:

```python
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
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_apply_state.py -k "terminal_closes_browser or success" -q`
Expected: FAIL — `CANCELLED` does not close, and `APPLIED` does neither close (auto) nor preserve (assisted) the browser yet.

- [ ] **Step 3: Add a shared close helper + update terminals**

In `backend/applier/engine.py` `_build_transitions`, add a helper and update the three terminal handlers:

```python
        async def _close_browser(c: ApplyContext) -> None:
            if c.browser is not None:
                try:
                    await c.browser.stop()
                except Exception:
                    pass  # best-effort; never shadow the outcome

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
```

- [ ] **Step 4: Run the FSM tests**

Run: `uv run pytest tests/test_apply_state.py -k "terminal_closes_browser or success" -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Remove the now-redundant strategy stops**

Now that the FSM closes the browser on every terminal that should, delete the redundant `browser.stop()` calls the FSM covers:

In `backend/applier/auto_apply.py` — remove the `try: await browser.stop()` blocks in:
- the fill-phase `except` (lines ~349-352),
- the confirmation-timeout branch (lines ~392-395),
- the user-cancel branch (lines ~404-407),
- the submit-phase `finally` (lines ~435-439).

Each removal just drops the stop; keep the surrounding `return ApplicationResult(...)`. Example (timeout branch becomes):

```python
        if not done:
            logger.warning("Auto-apply confirmation timed out for job_id=%d", job_id)
            return ApplicationResult(
                status="cancelled",
                method="auto",
                message="Confirmation timed out after 30 minutes.",
            )
```

In `backend/applier/assisted_apply.py` — remove the `try: await browser.stop()` block in the agent-failure `except` (lines ~185-188); keep the `return ApplicationResult(status=RESULT_FAILED, ...)`.

> Leave the success path in `assisted_apply.py` untouched — it never called `stop()` and must keep the browser open. Leave `form_filler.py` and `captcha_handler.py` Playwright-context teardown untouched — that is a different resource (the synchronous Playwright session), not the Tier-2 `browser_use` browser the FSM now owns.

- [ ] **Step 6: Run the applier suites**

Run: `uv run pytest tests/test_apply_state.py tests/test_apply_engine.py tests/test_apply_http.py -q`
Expected: all green (existing engine/HTTP tests unchanged, new state tests pass).

- [ ] **Step 7: Commit**

```bash
git add backend/applier/engine.py backend/applier/auto_apply.py backend/applier/assisted_apply.py tests/test_apply_state.py
git commit -m "feat(T4a-5): centralize Tier-2 browser cleanup in FSM terminals"
```

---

## Task 4: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite + type-check**

Run: `uv run pytest -q && uv run pyright backend/`
Expected: pytest green; pyright at baseline error count (no new `type: ignore` introduced for `ctx.browser`).

- [ ] **Step 2: Confirm no stray Tier-2 stop remains outside the FSM**

Run: `rg -n "browser.stop\(\)" backend/applier`
Expected: matches only inside `backend/applier/engine.py` `_close_browser` (and none in `auto_apply.py` / `assisted_apply.py`).

- [ ] **Step 3: Commit (if Step 2 required a cleanup)**

```bash
git add backend/applier
git commit -m "chore(T4a): verify Tier-2 browser cleanup is FSM-only"
```

---

## Self-Review notes (carried from plan author)

- Strategy instances are created once in `ApplicationEngine.__init__` and reused; resetting `self._active_browser = None` at the top of each `apply()` is required to avoid leaking a stale handle from a prior call.
- The mode-aware `APPLIED` cleanup is the crux: auto-success closes, assisted-success stays open. Both are pinned by tests in Task 3.
- `_dispatch` passing `db=None` works for the wiring test because the AUTO branch's `apply` is mocked; do not rely on `db=None` in non-mocked paths.
- Keep `tests/test_apply_engine.py` + `tests/test_apply_http.py` green throughout — they are the behavioral safety net for the strategy edits.
