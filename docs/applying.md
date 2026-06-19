# Auto / assisted / manual apply

How JobPilot fills and submits job applications: the `ApplicationEngine` routes each request to one of three strategies, drives a finite-state machine through the apply lifecycle, enforces a daily limit, and pauses for a human review/confirm step over WebSocket.

All of the apply machinery lives in `backend/applier/`.

## The three modes

`ApplyMode` (`backend/applier/engine.py:33`) has three values, surfaced to the API as the `method` of an application:

| Mode | Strategy | Browser | Submits? | Counts against daily limit |
|------|----------|---------|----------|----------------------------|
| `auto` | `AutoApplyStrategy` (`auto_apply.py:45`) | Headed Playwright / browser-use | Yes, after user confirm | Yes |
| `assisted` | `AssistedApplyStrategy` (`assisted_apply.py:44`) | Headed, left open | No — user submits by hand | Yes |
| `manual` | `ManualApplyStrategy` (`manual_apply.py:28`) | None | No | No |

`manual` is the fallback: it copies the CV/cover-letter PDFs into `~/Downloads` and opens the apply URL with `webbrowser.open` — no automation, no daily-limit reservation.

## Entry point: `ApplicationEngine.apply`

`ApplicationEngine` (`engine.py:47`) is instantiated once and stored on `app.state.apply_engine` (see `backend/main.py`). It holds the three strategy instances, an `ApplicationRecorder`, and the per-job confirm/cancel/review/patch registries. `apply()` (`engine.py:145`) is the single public entry, called from `POST /api/applications/{match_id}/apply` (`backend/api/applications.py:478`).

The flow inside `apply()`:

1. **Daily-limit reservation** (non-`manual` only, `engine.py:160`). Calls `DailyLimitGuard.reserve_slot`. On `DailyLimitExceeded` it returns early with `status="cancelled"`.
2. **Concurrency guard** (`engine.py:177`). Under `self._registry_lock` (an `asyncio.Lock`), it checks whether `job_match_id` already has an in-flight `confirm_event`; if so it releases the just-reserved slot and returns `cancelled`. This closes the read-then-write race where two concurrent requests for the same job could both pass the membership check.
3. **Build `ApplyContext`** (`engine.py:202`) — all mutable lifecycle state, including `confirm_event` / `cancel_event` and an `extras` dict carrying the mode, `ApplicantInfo`, and PDF paths.
4. **Build the per-mode transition table** and run the FSM (`engine.py:222`).
5. **Cleanup `finally`** (`engine.py:241`) — pops the confirm/cancel events, cached review snapshot, and pending patches for the job. A `BaseException` (incl. `asyncio.CancelledError`, which the FSM's `except Exception` does not catch) also force-stops any live browser before propagating (`engine.py:228`).

`ApplicantInfo` (`engine.py:39`) is a Pydantic model carrying `full_name`, `email`, `phone`, `location`, and `additional_answers_json`, each length-capped via constants from `backend/defaults.py`.

## The lifecycle FSM (`state.py`)

The apply lifecycle is a hand-rolled finite-state machine — no third-party FSM library, just dataclasses and a driver (`state.py`). States (`state.py:51`):

```
Reserved → CaptchaCheck → Filling → AwaitingConfirm → Submitting → Recording → Applied
                                                                              ↘ Cancelled
                                                                              ↘ Failed
                                                                              ↘ RemoteSubmittedLocalFailed
```

- `ApplyContext` (`state.py:79`) threads inputs, the `strategy_result`, the resolved outcome `(status, method, message)`, and an optional live `browser` handle through every transition.
- `Transition` (`state.py:124`) holds three optional async hooks: `on_enter`, `next` (returns the next `State`), and `on_exit`.
- `Statechart.run` (`state.py:170`) walks forward until it hits a terminal in `TERMINALS` (`state.py:68`), then runs the terminal state's `on_enter` as the compensation/cleanup step. If any non-terminal `on_enter` or `next` raises, the driver jumps straight to `State.FAILED` (`state.py:187`, `state.py:204`).

### What each state actually does

Most middle states (`CaptchaCheck`, `Filling`, `AwaitingConfirm`, `Submitting`) are **pass-through observability hooks** — they only log and advance. The real work happens in `RECORDING.on_enter` (`recording_on_enter`, `engine.py:320`), which calls `self._dispatch(ctx)` to run the chosen strategy. The middle states exist so the lifecycle is visible to tests/monitoring and provide hook points (e.g. a human captcha-solver pause).

`recording_next` (`engine.py:328`) maps the strategy result to a terminal:

- `RESULT_CANCELLED` → `CANCELLED`
- `RESULT_FAILED` → `FAILED`
- success → call `ApplicationRecorder.record`; on success → `APPLIED`, on any record exception → `REMOTE_SUBMITTED_LOCAL_FAILED` (the remote submit may have succeeded while the local DB write failed).

Terminal `on_enter` handlers (`engine.py:380`–`435`) perform compensation:

- **APPLIED** — closes the browser, *except* for `assisted` (its browser is intentionally left open for the user).
- **CANCELLED** / **FAILED** — release the reserved daily-limit slot and close the browser.
- **REMOTE_SUBMITTED_LOCAL_FAILED** — record a `db_write_failed` `ApplicationEvent` in a fresh transaction, then close the browser (mode-aware, like APPLIED).

## Strategy dispatch (`engine._dispatch`)

`_dispatch` (`engine.py:474`) reads `ctx.extras["mode"]` and calls the matching strategy. For `auto`/`assisted` it copies the strategy's live `_active_browser` onto `ctx.browser` in a `finally` so the FSM cleanup net owns it even on a crash. `manual` runs without a browser.

## Auto and assisted: two tiers

Both Tier-2 strategies are **provider-agnostic** — they get their LLM via the factory, never importing a vendor SDK directly. `make_llm_client()` (Tier-1 form filler) and `make_browser_llm()` (Tier-2 browser-use agent) in `backend/llm/factory.py` switch on `LLM_PROVIDER` / `BROWSER_LLM_PROVIDER` (`openai` | `anthropic`), so the apply code works with any configured provider.

### Tier 1 — `PlaywrightFormFiller` (`form_filler.py`)

Direct Playwright DOM manipulation plus a **single** LLM call. Tried first, but **skipped for multi-step sites** like LinkedIn (`is_multi_step_site`, `_strategy_common.py:41`) and only when `settings.APPLY_TIER1_ENABLED`. Pipeline in `fill_and_submit` (`form_filler.py:63`):

1. Launch a persistent Chromium context under `data/browser_profiles/{site_profile_key}/` (reuses saved cookies/auth), applying `playwright_stealth` if available.
2. Inline CAPTCHA check via `check_and_handle_captcha` (passing the `cancel_event` so the user can bail out of a captcha wait).
3. `_clean_form_html` (`form_filler.py:395`) strips the page to a ~15 KB form skeleton.
4. `_build_fill_prompt` (`form_filler.py:446`) → one `llm.generate_text` call → `_parse_llm_response` (`form_filler.py:504`) returns `{fields, file_inputs, submit_selector}`.
5. `page.fill` each field; `page.set_input_files` for CV/cover-letter uploads.
6. Screenshot, broadcast `apply_review` over WS, cache the snapshot via `on_review`.
7. Wait on confirm/cancel (30-min timeout).
8. On confirm, **apply user field edits** via `_apply_patches` (`form_filler.py:269`), then `page.click(submit_selector)`.

`fill_only` (`form_filler.py:286`) is the assisted variant — same fill pipeline but it deliberately leaves the context open (`context = None` so the `finally` won't close it) and returns `status="assisted"` for the user to submit manually.

Any unrecoverable error raises so the caller falls back to Tier 2.

### Tier 2 — browser-use agent

When Tier 1 is skipped or raises, `auto_apply._browser_use_apply` (`auto_apply.py:248`) / the assisted Tier-2 block (`assisted_apply.py:130`) drive a `browser_use` `Agent`:

- Build a headed `Browser` via `build_browser` (`_strategy_common.py:47`), loading the saved `state.json` storage state for the site.
- Build a detailed task prompt (`_build_fill_task`) with explicit "STOP before clicking Submit" instructions, applicant details, the `PHONE_NUMBER_NOTE` (`_strategy_common.py:66`), and uploadable file paths.
- Run the agent to fill the form, extract a `filled_fields` JSON blob from its output, capture a screenshot.
- **Auto**: broadcast `apply_review`, wait for confirm/cancel, then run a *second* agent with a submit-only task. **Assisted**: stop after fill and leave the browser open (returns `RESULT_ASSISTED`; a crash before fill completes returns `RESULT_FAILED`, not a fake success — `assisted_apply.py:194`).
- If `browser_use` is not installed, both fall back to `webbrowser.open`.

## The `apply_review` confirm/cancel flow

Both tiers pause before submitting and emit an `ApplyReview` WS message (`backend/api/ws_models.py:84`) containing `job_id`, `filled_fields`, and an optional `screenshot_base64`. The frontend renders this for the user, who responds with one of three WS messages, routed by handlers registered in `backend/main.py:228`–`250`:

| WS message | Handler | Engine method |
|------------|---------|---------------|
| `confirm_submit` | `_handle_confirm_submit` | `signal_confirm(job_id)` (`engine.py:93`) |
| `cancel_apply` | `_handle_cancel_apply` | `signal_cancel(job_id)` (`engine.py:134`) |
| `patch_fields` | `_handle_patch_fields` | `signal_patch_fields(job_id, fields)` (`engine.py:106`) |

`signal_confirm` / `signal_cancel` just `.set()` the per-job `asyncio.Event` the strategy is awaiting.

**Field patching.** The client may send `patch_fields` (a `selector → new value` map) *before* `confirm_submit` to correct mis-filled values. `signal_patch_fields` stores them in `_pending_patches`; the Tier-1 form filler re-applies them in `_apply_patches` right before clicking submit (best-effort — a failing re-fill logs a warning, never aborts). Critically, `signal_confirm` does **not** purge the patches (`engine.py:97`): `event.set()` only schedules the waiter wakeup, so the filler reads the patches *after* it resumes. Patches are purged on cancel and in `apply()`'s `finally`.

**Reconnect recovery.** Each broadcast also calls `on_review` → `record_pending_review` (`engine.py:120`), caching the snapshot in `_pending_reviews`. A client that lost its WS connection can re-fetch it over HTTP via `GET /api/applications/{job_id}/review-state` (`backend/api/applications.py:462`).

## Daily limit (`daily_limit.py`)

`DailyLimitGuard` (`daily_limit.py:55`) enforces a configurable N-applications/day cap (default `DAILY_LIMIT`, 10). Statuses counted toward the quota (`COUNTABLE_STATUSES`, `daily_limit.py:46`) are `applied` + `pending` plus the legacy aliases.

The only safe gate is `reserve_slot` (`daily_limit.py:98`), which is **atomic**:

1. Insert a `pending` `Application` placeholder with `applied_at = now`, then `flush()` (issuing the INSERT takes SQLite's RESERVED write lock).
2. Re-`COUNT` the day's countable rows. If over the limit → `rollback()` and raise `DailyLimitExceeded`.
3. Otherwise `commit()` so a concurrent reservation on another connection sees it, and return the placeholder id.

This closes the TOCTOU race between a non-atomic `can_apply` check and the subsequent insert. The read-only helpers `remaining_today`, `can_apply`, `assert_can_apply` remain for informational use (e.g. the batch runner pre-computing how many CVs to generate) and must **not** be used as the submit gate.

The reserved placeholder is later updated in place by the recorder, or released (status → `cancelled`, `applied_at` cleared) by `ApplicationRecorder.release_reserved_slot` on cancel/fail.

## Recording the outcome (`recorder.py`)

`ApplicationRecorder.record` (`recorder.py:46`) persists the result. If `reserved_app_id` is set (the AUTO/ASSISTED path) it **updates the placeholder in place** — never inserts a duplicate (which would double-count the limit); for MANUAL it inserts a fresh row. It also writes an `ApplicationEvent` lifecycle row, and on a success outcome flips the parent `JobMatch.status` to `"applied"`. Strategy outcome strings are mapped to the canonical persisted status via `normalize_result_status` (`backend/applier/__init__.py:100`); the status vocabulary and legacy aliases (`manual`/`assisted` → `applied`) are defined in that same `__init__.py`. Any DB-write failure rolls back and raises `ApplicationRecordError`, which the FSM routes to `REMOTE_SUBMITTED_LOCAL_FAILED`.

## CAPTCHA handling (`captcha_handler.py`)

`captcha_handler.py` detects CAPTCHAs (selector-based, `_CAPTCHA_SELECTORS`) and Cloudflare/bot block pages (title/body text, `_BLOCK_TITLE_FRAGMENTS`). Key functions:

- `site_profile_key(url)` (`captcha_handler.py:66`) — the **canonical** profile-dir key (lowercase host, `www.` stripped, dots → underscores, e.g. `linkedin_com`). It is the single source of truth shared by both tiers and the form filler, so Tier 2 looks in the same profile directory as Tier 1 rather than silently running as a guest.
- `check_and_handle_captcha` / `wait_for_captcha_resolution` (`captcha_handler.py:176`) — broadcast a `CaptchaDetected` WS message, poll until the user solves it in the visible browser (or the `cancel_event` fires), then `save_session` persists the storage state so future visits skip the challenge.
- `preflight_check_url` (`captcha_handler.py:270`) — a standalone headless probe that, on detecting a block, relaunches visibly for the user to solve and saves the session.

## Follow-up reminders (`follow_up.py`)

`scan_overdue` (`follow_up.py:33`) is a lazy scanner (run at startup and at the start of each batch — no background scheduler). It inserts a single `follow_up_due` `ApplicationEvent` for each `applied` application whose freshness anchor — `max(applied_at, last_correspondence_at)` — is at least `threshold_days` (default 7) old and that has no existing `follow_up_due` event (idempotent). The user resolves it by logging a `follow_up` event newer than the `follow_up_due`.

## Related

- [Architecture overview](architecture.md)
- [API reference](api-reference.md)
- [File map](file-map.md)
- [User guide](user-guide.md)
- [Documentation index](index.md)
- [Project README](../README.md)
