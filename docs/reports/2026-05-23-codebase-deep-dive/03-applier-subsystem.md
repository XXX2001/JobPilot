# 03 — The Applier Subsystem

Deep dive of `backend/applier/`. The applier is JobPilot's load-bearing
subsystem — it is the thing that actually submits an application to a remote
job site via browser automation. Every other piece (scraper, matcher, CV
pipeline, scheduler) eventually exists to feed input into this module.

**Scope:** 11 files, 2,820 LOC. The reading order below mirrors the call graph:
`engine.py` (orchestrator) → `state.py` (FSM) → `daily_limit.py` (guard) →
the three strategy modules → `form_filler.py` (Tier-1 helper) →
`captcha_handler.py` → `recorder.py` (persistence) → `follow_up.py` (scheduler
piggyback) → `recorder.py` (audit) → `__init__.py` (vocabulary).

---

## 1. Purpose

The applier is the verb in "JobPilot applies to a job". Given a `JobMatch.id`
and an `ApplyMode`, it:

1. Reserves one of the user's N daily submission slots (atomic).
2. Drives a finite-state machine through CAPTCHA check → form fill →
   user review → submit → DB write.
3. Returns an `ApplicationResult` to the caller.

The caller is almost always the API route
[`POST /api/applications/{match_id}/apply`](backend/api/applications.py#L454)
in [`api/applications.py:454`](backend/api/applications.py#L454), which is hit
by the React frontend's "Apply" button. The route resolves the user profile,
finds the tailored CV / cover-letter PDFs for that match, then calls
[`ApplicationEngine.apply()`](backend/applier/engine.py#L92).

Two side users:

- WebSocket message handlers in [`backend/main.py:202-212`](backend/main.py#L202)
  forward `confirm_submit` / `cancel_apply` events into
  [`engine.signal_confirm`](backend/applier/engine.py#L78) and
  [`engine.signal_cancel`](backend/applier/engine.py#L83).
- The batch runner ([`backend/scheduler/batch_runner.py:181`](backend/scheduler/batch_runner.py#L181))
  and FastAPI startup ([`backend/main.py:179`](backend/main.py#L179)) call
  [`scan_overdue()`](backend/applier/follow_up.py#L37) — the 7-day follow-up
  scanner — which lives in the applier package but does not touch the engine
  itself.

There is exactly one `ApplicationEngine` per process, instantiated at startup
in [`main.py:142`](backend/main.py#L142) and stashed on `app.state.apply_engine`.

---

## 2. High-level flow

```
                ┌──────────────────────────────────────┐
 API POST /apply│  apply_to_job() — api/applications   │
                └────────────────┬─────────────────────┘
                                 │ resolves user profile, CV, letter
                                 ▼
                ┌──────────────────────────────────────┐
                │  ApplicationEngine.apply()           │
                │  • DailyLimitGuard.reserve_slot()    │
                │    (skipped for MANUAL)              │
                │  • guards re-entrancy per job_id     │
                │  • builds ApplyContext + Statechart  │
                └────────────────┬─────────────────────┘
                                 │ chart.run(ctx)
                                 ▼
         RESERVED ──► CAPTCHA_CHECK ──► FILLING ──► AWAITING_CONFIRM
                                                       │
                                                       ▼
                          ┌────────────── SUBMITTING ◄─┘
                          │
                          ▼
                      RECORDING ── on_enter dispatches to strategy:
                          │           ┌──────────────────────────┐
                          │           │ AUTO  → AutoApplyStrategy │
                          │           │   Tier 1: PlaywrightFormFiller
                          │           │   Tier 2: browser-use Agent
                          │           ├──────────────────────────┤
                          │           │ ASSISTED → fill_only +    │
                          │           │   open browser, user      │
                          │           │   submits manually        │
                          │           ├──────────────────────────┤
                          │           │ MANUAL → webbrowser.open  │
                          │           └──────────────────────────┘
                          │
            strategy returns ApplicationResult
                          │
              ┌───────────┼────────────┬───────────────────┐
              ▼           ▼            ▼                   ▼
          APPLIED      CANCELLED     FAILED       REMOTE_SUBMITTED_
        (success +     (release      (release       LOCAL_FAILED
         JobMatch       slot)         slot +       (db_write_failed
         flipped)                     browser.stop)  audit event)
```

CAPTCHA and login interruptions are **not** modelled as FSM branches. They
happen inside the strategy / form_filler: `check_and_handle_captcha()` blocks
on a polling loop, broadcasts `captcha_detected` / `captcha_resolved` over
WebSocket, and either resolves (continuing the strategy) or raises (routing
the FSM to `FAILED`). The `CAPTCHA_CHECK` state in the FSM is purely
observational — see §4.

---

## 3. Engine orchestration

[`ApplicationEngine`](backend/applier/engine.py#L48) is a single long-lived
singleton. It holds:

- An instance of each strategy
  ([`engine.py:65-67`](backend/applier/engine.py#L65)), constructed once with
  the Gemini API key + model.
- One [`ApplicationRecorder`](backend/applier/recorder.py#L33) for DB writes.
- Two `dict[int, asyncio.Event]` maps for per-job `confirm` / `cancel`
  signalling ([`engine.py:71-72`](backend/applier/engine.py#L71)).

The DB session is **not** held by the engine — it is passed in per call from
the FastAPI request scope. This is correct: each apply is one HTTP request,
and the session must not outlive the request.

### Lifecycle of a single `apply()` call

[`engine.py:92-170`](backend/applier/engine.py#L92):

1. **Reserve slot** ([`engine.py:108-122`](backend/applier/engine.py#L108)) —
   for AUTO/ASSISTED only, call `DailyLimitGuard.reserve_slot()` which inserts
   a `pending` placeholder and commits in the same transaction (atomic, §8).
   MANUAL skips this entirely — opening a browser tab does not count toward
   the daily cap.
2. **Re-entrancy guard** ([`engine.py:125-135`](backend/applier/engine.py#L125)) —
   if `job_match_id` already has an in-flight `confirm_event` entry, refuse
   and release the slot. Prevents a double-click on the Apply button from
   spawning two browser sessions.
3. **Create events** ([`engine.py:137-138`](backend/applier/engine.py#L137)) —
   register fresh confirm/cancel events for this job id.
4. **Build context + transitions** ([`engine.py:141-162`](backend/applier/engine.py#L141)) —
   build an `ApplyContext` carrying inputs, events, slot id, and a free-form
   `extras` dict for strategy-specific data. Build the transition table.
5. **Run FSM** ([`engine.py:165`](backend/applier/engine.py#L165)) — call
   `chart.run(ctx)`. The FSM's `RECORDING.on_enter` calls
   [`_dispatch()`](backend/applier/engine.py#L385) which picks the strategy.
6. **Cleanup** ([`engine.py:167-168`](backend/applier/engine.py#L167)) — pop
   the per-job events in a `finally`. This always runs, even on FSM crash.

### Strategy selection

Done in [`_dispatch()` — `engine.py:385-422`](backend/applier/engine.py#L385).
It is a flat `if/elif/else` keyed on `ApplyMode`. The signatures of the three
strategy `apply()` methods differ:

- AUTO takes `job_id`, `confirm_event`, `cancel_event` (it broadcasts the
  `apply_review` WS message and waits for user confirmation).
- ASSISTED takes no events (the strategy pre-fills, returns, user does the
  rest in the browser).
- MANUAL takes only `apply_url`, `cv_pdf`, `letter_pdf` (no LLM, no events).

The fact that the three signatures diverge is one of the reasons the strategy
collapse (§5, §12) is awkward.

### Integration with `BrowserSessionManager`

Loose. The applier does **not** use `BrowserSessionManager` directly. Both
[`auto_apply.py`](backend/applier/auto_apply.py#L262-L298),
[`assisted_apply.py`](backend/applier/assisted_apply.py#L136-L158), and
[`form_filler.py`](backend/applier/form_filler.py#L78-L101) read the
`data/browser_profiles/{site}/state.json` snapshot directly off disk and feed
it to `browser_use.Browser(storage_state=...)` or to Playwright's
`launch_persistent_context(user_data_dir=...)`. The session manager (used by
the scraper to *create* those snapshots) is a sibling, not a collaborator.

That is fine architecturally — the applier reads, the manager writes — but
the lack of a shared abstraction means each consumer hardcodes the path layout
(`data/browser_profiles/{site}/state.json`). Three different `_site_key()` /
`_domain_key()` functions exist:
[`auto_apply.py:33`](backend/applier/auto_apply.py#L33),
[`assisted_apply.py:37`](backend/applier/assisted_apply.py#L37),
[`captcha_handler.py:66`](backend/applier/captcha_handler.py#L66) — and they
disagree (one returns `linkedin`, two return `linkedin_com`). See §12.

---

## 4. State machine

[`state.py`](backend/applier/state.py) is the BE-R4 deliverable: a small
home-grown FSM framework with three dataclasses (`State`, `ApplyContext`,
`Transition`) and a 60-line `Statechart` driver.

### What `state.py` actually expresses today

The `State` enum has **10 members**
([`state.py:53-63`](backend/applier/state.py#L53)):

| State | Role | Action |
|---|---|---|
| `RESERVED` | initial | no-op, jumps to CAPTCHA_CHECK |
| `CAPTCHA_CHECK` | observational | logs entry only |
| `FILLING` | observational | logs entry only |
| `AWAITING_CONFIRM` | observational | logs entry only |
| `SUBMITTING` | observational | logs entry only |
| `RECORDING` | **real work** | dispatches to strategy, records result |
| `APPLIED` | terminal | no-op |
| `CANCELLED` | terminal | releases reserved slot |
| `FAILED` | terminal | releases slot + closes browser |
| `REMOTE_SUBMITTED_LOCAL_FAILED` | terminal | inserts `db_write_failed` event |

### The honest truth about middle states

Four of the five "middle" states (`CAPTCHA_CHECK`, `FILLING`,
`AWAITING_CONFIRM`, `SUBMITTING`) are pass-through stubs. Their `on_enter`
literally just calls `logger.debug("entering %s", ...)` and their `next()`
returns the next state in the linear chain. See
[`engine.py:197-233`](backend/applier/engine.py#L197).

The comments on each ([`engine.py:192-228`](backend/applier/engine.py#L192))
explain that they exist *"to make the lifecycle observable in the FSM
transition log and provide a hook point for future per-state interception"*.
That is honest documentation: they are scaffolding, not active mechanism.

All four phases (captcha detection, filling, awaiting confirm, submitting)
are actually performed inside the strategy's `apply()` call, which is itself
triggered from `RECORDING.on_enter`
([`engine.py:236-242`](backend/applier/engine.py#L236)). So the FSM observes
*one* logical phase (`RECORDING`) and labels the entry into the others, but
nothing dispatches per middle state.

### What the FSM actually buys you today

Two real wins:

1. **Compensation is centralised.** `CANCELLED`, `FAILED`, and `REMOTE_*` each
   have an `on_enter` that *must* run when the chart lands there. Before BE-R4
   this was scattered try/except/finally inside `engine._record_application`.
   The driver guarantees it ([`state.py:227-236`](backend/applier/state.py#L227)).
2. **Uniform error handling.** Any exception in any `on_enter` or `next()`
   routes to `FAILED` ([`state.py:184-211`](backend/applier/state.py#L184)),
   and then `FAILED.on_enter` releases the slot and stops the browser. Before
   BE-R4 a strategy crash would have left the placeholder `pending` row
   counted against the daily limit forever.

### What the FSM does **not** buy you today

The strategy collapse was deferred. The intra-strategy flow (preflight,
captcha, fill, wait, submit) still lives as imperative code inside
[`auto_apply.py:_browser_use_apply`](backend/applier/auto_apply.py#L236) and
[`form_filler.py:fill_and_submit`](backend/applier/form_filler.py#L52). The
FSM has no visibility into it — `CAPTCHA_CHECK` is logged before the strategy
runs, but the actual captcha handling happens *inside* `fill_and_submit`,
between calling `page.goto()` and `_clean_form_html()`
([`form_filler.py:113-118`](backend/applier/form_filler.py#L113)).

So if a captcha appears mid-fill, the FSM stays in `RECORDING` while
`check_and_handle_captcha` polls for up to 300 s. From the FSM's perspective,
that 300 s is silent.

---

## 5. Strategy walkthrough

Three strategies live side-by-side. They are reported as ~80% copy-paste; that
is roughly accurate for `auto_apply` vs `assisted_apply`, and is entirely
accurate for the four helper definitions duplicated across them.

### MANUAL — [`manual_apply.py`](backend/applier/manual_apply.py)

63 lines, no LLM, no Playwright. It:

1. Copies CV/letter PDFs into `~/Downloads` if not already present
   ([`manual_apply.py:31-45`](backend/applier/manual_apply.py#L31)).
2. Calls `webbrowser.open(apply_url)`
   ([`manual_apply.py:48`](backend/applier/manual_apply.py#L48)).
3. Returns `ApplicationResult(status="manual", method="manual", ...)`.

Triggered when the user picks "Manual" in the UI — the engine *does not*
reserve a daily slot for this path
([`engine.py:109`](backend/applier/engine.py#L109)). Rationale: opening a tab
is not an automated submission. Consequence: a user can spam-click "Manual"
indefinitely. That is the intended behaviour.

This module is also the home of the `ApplicationResult` Pydantic model
([`manual_apply.py:15-18`](backend/applier/manual_apply.py#L15)) that all
three strategies use. The placement is incidental — `manual_apply.py` happens
to be the smallest file. It would be more natural in `__init__.py` or a
`models.py`.

### ASSISTED — [`assisted_apply.py`](backend/applier/assisted_apply.py)

286 lines. Two-tier:

- **Tier 1** ([`assisted_apply.py:88-111`](backend/applier/assisted_apply.py#L88)) —
  `PlaywrightFormFiller.fill_only()`. Pre-fills the form, leaves the browser
  open, returns `status="assisted"`. Skipped for "multi-step sites"
  (LinkedIn).
- **Tier 2** ([`assisted_apply.py:113-208`](backend/applier/assisted_apply.py#L113)) —
  `browser_use.Agent` + `ChatGoogle`. Spins up a Chromium with the saved
  storage state, hands a fill-task prompt to the agent, and returns when the
  agent stops. The browser stays alive (`keep_alive=True`).

Triggered when the user picks "Assisted". The engine reserves a slot before
calling. On success the placeholder row is updated to `status="applied"`.
The user finishes the submission manually in the open browser.

### AUTO — [`auto_apply.py`](backend/applier/auto_apply.py)

439 lines. Same two-tier shape, plus an extra confirm-loop and a real submit
click:

- **Tier 1** ([`auto_apply.py:102-131`](backend/applier/auto_apply.py#L102)) —
  `PlaywrightFormFiller.fill_and_submit()`. This calls the form filler which
  broadcasts `apply_review` to the WS, blocks on confirm/cancel, then clicks
  the submit selector. The block lives inside the form filler
  ([`form_filler.py:194-208`](backend/applier/form_filler.py#L194)).
- **Tier 2** ([`auto_apply.py:236-436`](backend/applier/auto_apply.py#L236)) —
  fill task → agent → screenshot → `apply_review` WS broadcast → wait for
  confirm/cancel ([`auto_apply.py:373-407`](backend/applier/auto_apply.py#L373))
  → submit task → second agent run.

### Where the copy-paste lives

Concrete duplications, file:line precise:

| Helper / concept | `auto_apply.py` | `assisted_apply.py` |
|---|---|---|
| `_MULTI_STEP_DOMAINS` constant | L30 | L29 |
| `_site_key()` | L33-39 | L37-42 |
| `_is_multi_step_site()` | L42-45 | L32-34 |
| browser-use import fallback | L18-27 | L17-26 |
| Tier-1-disabled guard pattern (`use_tier1 = settings.APPLY_TIER1_ENABLED and self._form_filler is not None and not _is_multi_step_site(...)`) | L96-100 | L82-86 |
| `browser_kwargs` dict (`headless=False, keep_alive=True, minimum_wait_page_load_time=3.0, wait_for_network_idle_page_load_time=15.0, disable_security=True`) | L286-292 | L146-152 |
| `state_path` resolution (`profiles_dir / site_key / "state.json"`) | L263-265 | L137-139 |
| `_build_fill_task()` (LinkedIn vs generic prompt) | L148-234 | L210-283 |
| `additional_answers` JSON parsing | L213-221 | L269-277 |
| Phone-country-code prompt note (8-line block) | L201-205 | L258-261 |
| File-paths assembly for `available_file_paths=` | L307-311 | L166-170 |
| `browser.stop()` in `except` | L347-349, L391-393, L402-404 | L182-185 |

The two `_build_fill_task` functions diverge only in:

- Step 7 wording: AUTO says *"STOP before the final 'Submit application' / 'Review' button"*, ASSISTED says *"STOP before the final 'Submit application' button"*.
- AUTO closes with an *"After filling all fields … report all the fields you filled as a JSON object"* clause ([`auto_apply.py:229-232`](backend/applier/auto_apply.py#L229)). ASSISTED closes with *"Do NOT click Submit"* ([`assisted_apply.py:280`](backend/applier/assisted_apply.py#L280)).

Everything else — applicant block, file upload mentions, additional answers
parsing, the LinkedIn vs generic branch — is identical. The collapse target
would be a single `BuildFillTask(mode: ApplyMode, ...)` helper.

The Tier-1/Tier-2 fallback pattern (try Tier 1, except → fall back to Tier 2)
is implemented twice with subtly different exception semantics: AUTO falls
back on **any** exception ([`auto_apply.py:126-131`](backend/applier/auto_apply.py#L126)),
ASSISTED falls back on any exception ([`assisted_apply.py:107-111`](backend/applier/assisted_apply.py#L107))
but unlike AUTO does **not** treat a Tier-1 `cancelled` return as terminal —
ASSISTED only has `try / except` because `fill_only` never returns
"cancelled" (it doesn't broadcast review).

---

## 6. Form filler

[`form_filler.py`](backend/applier/form_filler.py) is `PlaywrightFormFiller`:
the Tier-1 implementation that bypasses the browser-use agent loop for simple
sites. It uses Playwright directly + a single Gemini call.

### Architecture (mirrors `ScraplingFetcher`)

The 10-step pipeline is documented in the module docstring
([`form_filler.py:1-14`](backend/applier/form_filler.py#L1)). The two public
methods are:

- [`fill_and_submit()`](backend/applier/form_filler.py#L52) — full flow,
  used by AUTO. Phases 1–8 (preflight captcha → fill → screenshot → broadcast
  → wait → submit). The Playwright `context` is closed in `finally`.
- [`fill_only()`](backend/applier/form_filler.py#L231) — pre-fill only, used
  by ASSISTED. Phases 1–4 (no screenshot, no broadcast, no wait, no submit).
  Critically, the `context` is set to `None` before the `finally`
  ([`form_filler.py:322`](backend/applier/form_filler.py#L322)) to prevent
  it being closed. The browser stays open for the user. The Playwright
  driver process (`pw`) is stopped, which on some platforms may leave the
  Chromium process orphaned (see §12).

### Selector strategy

The filler asks Gemini for a JSON document with three keys:
```
{
  "fields":          [{"selector": "...", "value": "..."}],
  "file_inputs":     [{"selector": "...", "file": "cv|letter"}],
  "submit_selector": "..."
}
```
([`form_filler.py:437-441`](backend/applier/form_filler.py#L437)). Each
selector is fed straight into `page.fill()` / `page.set_input_files()` /
`page.click()` with a 3 000 ms timeout — no validation, no XPath fallback, no
retry, no scoring.

### Fallback strategy

There is none beyond "Tier 1 raised → Tier 2 takes over". Within Tier 1, an
unknown selector raises a Playwright `TimeoutError`, which is caught and
logged at DEBUG level ([`form_filler.py:147-148`](backend/applier/form_filler.py#L147))
— so a malformed selector silently drops the field and the apply may submit
with required fields empty. See §12 (silent-failure smell).

### HTML cleaning

[`_clean_form_html()`](backend/applier/form_filler.py#L340) strips `<script>`,
`<style>`, `<nav>`, `<footer>`, `<header>`, `<noscript>`, `<svg>`, `<iframe>`,
keeps `{id, name, type, placeholder, required, for, class, action, method}`
attributes, runs through `markdownify`, and truncates to 15 000 chars. If
`markdownify` drops void elements (it does, for `<input>`) the function falls
back to cleaned HTML — handled at
[`form_filler.py:381-385`](backend/applier/form_filler.py#L381).

The 15 000-char cap is hard-coded
([`form_filler.py:31`](backend/applier/form_filler.py#L31)). Workday and
SAP SuccessFactors application pages routinely exceed this even after
cleaning. They will be silently truncated.

### Gemini response parsing

[`_parse_gemini_response()`](backend/applier/form_filler.py#L449) strips
markdown fences, regex-finds the first `{...}`, json-loads it, and returns
a safe default on any parse failure
([`form_filler.py:454`](backend/applier/form_filler.py#L454)). The default
`submit_selector` is `"button[type=submit]"` which is the right call for
plain HTML forms but wrong for the ~50% of modern ATSes that use a
`<button>` with an `onClick` handler and no `type` attribute.

---

## 7. Captcha handler

[`captcha_handler.py`](backend/applier/captcha_handler.py) handles both
CAPTCHA widgets (reCAPTCHA, hCaptcha, Cloudflare Turnstile) and Cloudflare
block pages.

### Detection (two-pronged)

- **Selector-based** — [`_CAPTCHA_SELECTORS`](backend/applier/captcha_handler.py#L31)
  is a flat list of 12 selectors checked via `page.query_selector()` +
  `is_visible()`. First-match wins.
- **Text-based** — [`_BLOCK_TITLE_FRAGMENTS`](backend/applier/captcha_handler.py#L52)
  is a list of 10 lowercase substrings matched against `page.title()` and
  the first 500 chars of `document.body.innerText`. Catches Cloudflare
  *"Just a moment..."* / *"Attention Required"* pages that have no
  CAPTCHA widget.

### Manual takeover flow

[`wait_for_captcha_resolution()`](backend/applier/captcha_handler.py#L152):

1. Broadcast `CaptchaDetected` over WS (so the frontend can surface a
   banner).
2. Poll `detect_any_block(page)` every 2 s for up to 300 s.
3. On clear: broadcast `CaptchaResolved`, call `save_session(page)` to
   persist storage state to disk, return `True`.
4. On timeout: broadcast `CaptchaResolved` (note: same event, no
   "expired" variant) and return `False`.

[`preflight_check_url()`](backend/applier/captcha_handler.py#L204) is the
two-phase helper:

1. Headless probe — load the URL with stealth patches, if no block detected
   save storage_state and return True.
2. If blocked — relaunch *visible*, notify user, wait for resolution, save
   session, close.

This function is called by scraping, **not** by the applier. The applier
calls only the inline `check_and_handle_captcha()`
([`form_filler.py:113`](backend/applier/form_filler.py#L113)).

---

## 8. Daily limit guard

[`daily_limit.py`](backend/applier/daily_limit.py) — the TOCTOU race fix
documented in the module docstring
([`daily_limit.py:1-26`](backend/applier/daily_limit.py#L1)).

### Counting

A row counts if `applied_at >= today (UTC date)` AND its status is in
`{applied, pending, manual, assisted}` (the last two being legacy aliases —
[`daily_limit.py:49-51`](backend/applier/daily_limit.py#L49)). The query is
[`daily_limit.py:77-81`](backend/applier/daily_limit.py#L77) and
[`daily_limit.py:136-139`](backend/applier/daily_limit.py#L136).

### Reset cadence

UTC midnight. The reset is implicit — `date.today()` rolls over and
yesterday's rows stop matching. There is no scheduled job. The
`/api/applications/limit-status` endpoint computes a `resets_at` field by
adding one day to `date.today()` ([`api/applications.py:240-247`](backend/api/applications.py#L240)).

### The atomic reservation

[`reserve_slot()`](backend/applier/daily_limit.py#L101) is the only safe
gate. It:

1. INSERTs a `pending` Application placeholder.
2. `flush()`es — SQLite issues the INSERT and takes the RESERVED write lock.
3. COUNTs rows in today's window.
4. If `count > limit`, `rollback()` (the whole transaction, removing the
   placeholder) and raise `DailyLimitExceeded`.
5. Otherwise `commit()` and `refresh()` to get the new id.

The atomicity argument hinges on SQLite serialising writers via the
RESERVED lock. The test
[`test_daily_limit.py:test_concurrent_reservations_never_exceed_limit`](tests/test_daily_limit.py#L114)
exercises this with two real concurrent connections against an on-disk SQLite
file; it asserts exactly one succeeds and the on-disk row count equals the
limit.

The trick has a real cost: every reserve_slot commits a transaction.
Anything the caller's session held in pre-flush state is **gone** after the
guard runs. The recorder later picks up the same `reserved_app_id` and
mutates it ([`recorder.py:83-96`](backend/applier/recorder.py#L83)) — that
works because both runs share the same session, but it leaks the "I committed
mid-request" detail outside the guard.

### Integration with the engine

The engine calls `reserve_slot()` exactly once at the top of `apply()`, only
for AUTO/ASSISTED. On `DailyLimitExceeded` it returns
`ApplicationResult(status="cancelled", ...)` — note the `cancelled` status,
not `failed`, because hitting the cap is a user-facing limit, not a bug
([`engine.py:116-122`](backend/applier/engine.py#L116)).

On the success path the recorder mutates the placeholder *in place*. On the
cancel/fail path the FSM compensation calls
`recorder.release_reserved_slot()` which sets `status="cancelled"`,
`applied_at=None` ([`recorder.py:141-170`](backend/applier/recorder.py#L141))
so the placeholder no longer counts.

---

## 9. Follow-up reminders (PG-1)

[`follow_up.py`](backend/applier/follow_up.py) — 93 lines, one async
function: [`scan_overdue(threshold_days=7)`](backend/applier/follow_up.py#L37).

### What it does

Finds Applications where:
- `status = 'applied'`
- `applied_at <= now - 7 days`
- No existing `follow_up_due` ApplicationEvent (idempotency
  — [`follow_up.py:56-59`](backend/applier/follow_up.py#L56))

…and inserts one `follow_up_due` event per qualifying application
([`follow_up.py:80-87`](backend/applier/follow_up.py#L80)). Returns the
count of events created.

The 7-day window comes from `applied_at`, **not** from a
`last_correspondence_at` column. The task brief mentioned
`last_correspondence_at` integration — there is no such column anywhere in
the codebase (`grep -rn "last_correspondence_at"` returns zero hits). The
sole signal is the time since submission.

### Resolution semantics

The user clears a reminder by POSTing a `follow_up` event (no `_due` suffix)
via `POST /api/applications/{id}/events`. The list endpoint
`GET /api/applications?needs_follow_up=true` then excludes the application
because its query
([`api/applications.py:176-184`](backend/api/applications.py#L176)) requires:
*there exists a `follow_up_due` event AND no `follow_up` event with
`event_date > follow_up_due.event_date`*.

This means a `follow_up` event dated *before* the `follow_up_due` does not
resolve it — the user has to log a *new* follow-up after each reminder. That
is documented ([`follow_up.py:14-17`](backend/applier/follow_up.py#L14)) but
unintuitive.

### Who triggers it

Two places, lazily:

1. **App startup** ([`main.py:177-184`](backend/main.py#L177)) — on FastAPI
   lifespan startup, wrapped in a non-fatal try/except.
2. **Each batch run** ([`batch_runner.py:179-188`](backend/scheduler/batch_runner.py#L179))
   — before the scrape step, also non-fatal.

There is no APScheduler job for it. The docstring describes this as the
intent ("Lazy trigger") — running it more often is harmless because it is
idempotent, and running it less often means reminders just appear later.

### Session lifecycle

The function deliberately opens its own `AsyncSessionLocal()` and commits
inside ([`follow_up.py:71-90`](backend/applier/follow_up.py#L71)). The
docstring spells out why: borrowing a long-lived session and committing in
it would expire any ORM objects the caller is still using.

---

## 10. Recorder

[`recorder.py`](backend/applier/recorder.py) — `ApplicationRecorder`. Two
methods, both small:

- [`record()`](backend/applier/recorder.py#L46) — the success-path write.
  If `reserved_app_id` is set (AUTO/ASSISTED), **mutates the placeholder in
  place**; otherwise (MANUAL) inserts a fresh row. Also inserts an
  `ApplicationEvent` of `event_type=<result_status>` (so we get a row per
  outcome — "applied", "cancelled", "failed"). On success, also flips
  `JobMatch.status = "applied"` so the match disappears from the queue.
- [`release_reserved_slot()`](backend/applier/recorder.py#L141) — the
  compensation path. Sets `status="cancelled"`, `applied_at=None`. Doesn't
  DELETE — keeps the row for audit / FK survival.

The contract documented in
[`__init__.py:80-90`](backend/applier/__init__.py#L80): the
`ApplicationRecordError` exception is the EH-07 typed exception. The remote
submission *may* have succeeded; the caller (engine `RECORDING.next` —
[`engine.py:260-291`](backend/applier/engine.py#L260)) is responsible for
routing to `REMOTE_SUBMITTED_LOCAL_FAILED` and surfacing
"verify on the job site" to the user.

What gets recorded:

- `Application` row: method, status, applied_at, notes, job_match_id, id.
- `ApplicationEvent` row: application_id, event_type, details, event_date
  (defaulted by the model).
- `JobMatch.status` flip — only when `is_success` is true.

What is **not** recorded:

- Screenshots from the apply_review WS broadcast (they live only in memory
  for the duration of the broadcast — see §11 critique).
- The Gemini prompt or response used by Tier 1.
- The filled-fields JSON from Tier 2 agent output (it is in the WS payload
  but not in the DB).
- The strategy that won (Tier 1 vs Tier 2 — only `method` is recorded,
  which is `auto`/`assisted`/`manual`).

---

## 11. Public API surface

What the rest of the codebase calls into:

### From the engine
- [`ApplicationEngine(api_key, model, daily_limit)`](backend/applier/engine.py#L55) — constructed once in `main.py:142`.
- [`engine.apply(job_match_id, mode, db, apply_url, applicant, cv_pdf, letter_pdf)`](backend/applier/engine.py#L92) — called by `api/applications.py:593` (the apply route).
- [`engine.signal_confirm(job_id)`](backend/applier/engine.py#L78) and [`engine.signal_cancel(job_id)`](backend/applier/engine.py#L83) — called by `main.py:206` and `main.py:212` (WS message dispatchers).

### From other modules (not the engine)
- [`scan_overdue()`](backend/applier/follow_up.py#L37) — called by `main.py:179` (startup) and `batch_runner.py:181` (each batch).
- [`COUNTABLE_STATUSES`](backend/applier/daily_limit.py#L49) — imported by `api/today.py:33` and `api/applications.py:14` to compute the limit-status counter.
- [`DailyLimitGuard`](backend/applier/daily_limit.py#L58) — imported by `scheduler/batch_runner.py:18` to size the next batch.
- [`LEGACY_APPLIED_ALIASES`, `STATUS_APPLIED`](backend/applier/__init__.py#L41) — imported by `api/applications.py:13` for filter expansion.
- [`ApplicationResult`](backend/applier/manual_apply.py#L15) — imported by `api/applications.py:15` for response typing.
- [`ApplicantInfo, ApplicationEngine, ApplyMode`](backend/applier/engine.py#L425) — imported lazily by `api/applications.py:462`.

The package's `__init__.py` is a vocabulary file: it exports the canonical
status string constants, the `ApplicationRecordError`, and the
`normalize_result_status()` mapper. The constants
([`__init__.py:31-77`](backend/applier/__init__.py#L31)) are a small but
genuine value: the difference between strategy `RESULT_*` (what
strategies return) and persisted `STATUS_*` (what goes in the DB column)
plus `LEGACY_APPLIED_ALIASES` for the pre-consolidation rows — a single file
to grep when a new status is added.

---

## 12. Critique

### [SEV-1] CAPTCHA / login is invisible to the FSM
The FSM logs entry into `CAPTCHA_CHECK` *before* the strategy runs
([`engine.py:200-201`](backend/applier/engine.py#L200)). Real captcha
detection happens inside `form_filler.fill_and_submit` after `page.goto()`
([`form_filler.py:113-118`](backend/applier/form_filler.py#L113)) and can
block for up to 300 s — entirely in state `RECORDING`. If a user cancels
during a captcha wait, the cancel event is consumed by the post-fill
`asyncio.wait` ([`form_filler.py:199-208`](backend/applier/form_filler.py#L199))
*after* the captcha resolves — i.e. the user can't actually cancel until they
have solved the captcha. The FSM cannot model a "stuck in captcha for 5
minutes" scenario today.

### [SEV-1] The bug class the FSM is meant to eliminate is mostly, but not entirely, gone

The pre-BE-R4 bug class was: "we reserved a slot, then the strategy crashed,
and we forgot to release it / close the browser / record the outcome". The
FSM eliminates the slot leak (compensation in `CANCELLED`/`FAILED`
on_enter) and the browser leak (`FAILED.on_enter` calls `browser.stop()` —
[`engine.py:317-322`](backend/applier/engine.py#L317)).

What it does **not** eliminate:

1. `ApplyContext.browser` is set by **nobody**. The dataclass declares it
   ([`state.py:110`](backend/applier/state.py#L110)), but no `on_enter` or
   `next` ever assigns to it. So `FAILED.on_enter`'s `browser.stop()` call
   is dead code — the Playwright `context` in `form_filler.py` and the
   `browser_use.Browser` in `auto_apply._browser_use_apply` are both local
   variables, freed (or leaked) by the strategy's own `try/finally`. **The
   FSM cannot clean up a browser the strategy left open**; the strategies
   are still doing it themselves. This is the largest remaining "did we
   close it?" failure mode.
2. `fill_only()` in `form_filler.py:322` deliberately nulls out `context` to
   prevent `finally` from closing it, then calls `pw.stop()`. Stopping the
   playwright driver while keeping a context open is undocumented; on some
   platforms it orphans the Chromium subprocess. The browser process is
   never tied to the engine's lifecycle.

### [SEV-1] Silent failures hide bad form fills

[`form_filler.py:144-148`](backend/applier/form_filler.py#L144):
```python
try:
    await page.fill(sel, val, timeout=3_000)
    filled_fields[sel] = val
except Exception as exc:
    logger.debug("Could not fill %r: %s", sel, exc)
```
A selector that doesn't match → 3 s timeout → DEBUG log → field skipped →
review screenshot taken → user sees "looks good" because their eye
doesn't catch the empty required field → user clicks confirm → submit is
attempted → either succeeds with bad data or fails on a server validation
that we never surface. Same pattern for file uploads
([`form_filler.py:155-158`](backend/applier/form_filler.py#L155)) and
[`fill_only()`](backend/applier/form_filler.py#L299-L302).

DEBUG is below the default log level (INFO). In production this is
completely silent.

### [SEV-1] Strategy duplication is 80%+, including subtly different logic

`auto_apply.py` and `assisted_apply.py` duplicate ~250 lines of essentially
identical scaffolding (see §5 table). Worse, the duplication is *not* a
strict copy: the two `_site_key()` implementations differ from
`_domain_key()` in `captcha_handler.py` — two return `linkedin`, one returns
`linkedin_com`. The state.json path uses `_site_key` in
`auto_apply.py:265` (giving `data/browser_profiles/linkedin/state.json`) but
`_domain_key` in `form_filler.py:79` (giving
`data/browser_profiles/linkedin_com/state.json`). **These do not refer to
the same directory.** A session saved by the captcha preflight (which uses
`_domain_key`) will not be picked up by Tier 2 auto/assisted (which use
`_site_key`).

### [SEV-2] Race condition: re-entrancy guard reads dict then writes — but not atomically

[`engine.py:125-138`](backend/applier/engine.py#L125):
```python
if job_match_id in self._confirm_events:
    ...
    return ApplicationResult(status=RESULT_CANCELLED, ...)
self._confirm_events[job_match_id] = asyncio.Event()
self._cancel_events[job_match_id] = asyncio.Event()
```
There is no lock. Two simultaneous WS requests (same job, two browser tabs)
can both pass the membership check before either writes. Practically rare
(the engine instance is single-threaded asyncio, and dict-insert + return
happens between awaits) — but the API route does `await` between calls and
the engine's `apply()` itself does `await reserve_slot()` before this guard.
A second concurrent request that *runs the daily-limit reservation in
parallel* will see no `_confirm_events` entry until the first one inserts
it.

### [SEV-2] Confirmation timeout differs by tier, and is invisible to the engine

Tier 1 ([`form_filler.py:204`](backend/applier/form_filler.py#L204)) waits
30 minutes. Tier 2 auto ([`auto_apply.py:383`](backend/applier/auto_apply.py#L383))
also waits 30 minutes. The engine itself has no timeout —
`Statechart.run()` is unbounded. If a strategy hangs (e.g. browser-use
agent's `agent.run()` stalls before reaching the confirm wait), the engine
will block forever, holding the per-job events in `_confirm_events`. A
second apply for the same job will hit the re-entrancy guard for the rest
of the process lifetime.

### [SEV-2] Tests mock the parts that matter

- [`test_apply_engine.py:test_engine_manual_apply_records_application`](tests/test_apply_engine.py#L135)
  uses `AsyncMock(spec=AsyncSession)`. The mock returns a `MagicMock` for
  every `.execute()` call, scalar-or-none returns whatever the test sets.
  The DailyLimitGuard's `flush()` → COUNT → `commit()` flow is exercised
  only at the mock level — the test cannot catch a real SQLite transaction
  bug. (The atomicity test in `test_daily_limit.py` *does* use a real
  on-disk SQLite, but the engine tests don't.)
- [`test_apply_engine.py:test_auto_apply_tier1_success_no_tier2`](tests/test_apply_engine.py#L279)
  asserts Tier 2 is not called when Tier 1 returns success — but the test
  patches `strategy._form_filler.fill_and_submit` with an `AsyncMock`. The
  real `PlaywrightFormFiller` never opens a browser in any test. There is
  no integration test for the full applier path end-to-end.
- [`test_form_filler.py`](tests/test_form_filler.py) covers only the four
  pure helper methods (`_clean_form_html`, `_build_fill_prompt`,
  `_parse_gemini_response`). The async public methods
  (`fill_and_submit`, `fill_only`) have zero direct test coverage.

### [SEV-2] `apply_review` WS messages dropped if no client connected

[`auto_apply.py:369-370`](backend/applier/auto_apply.py#L369),
[`form_filler.py:190-191`](backend/applier/form_filler.py#L190): broadcast
failures are caught and logged at WARNING. The strategy then proceeds to
wait on `confirm_event` for 30 minutes — but no client ever received the
review and no client will send `confirm_submit`. The user sees a browser
sitting on a pre-filled form with no UI affordance. Eventually the
confirmation times out and the apply is cancelled. The screenshot, the
filled fields, the Gemini analysis — all discarded.

### [SEV-3] Hard-coded selectors and brittle scraping
- [`_CAPTCHA_SELECTORS`](backend/applier/captcha_handler.py#L31) — 12
  flat selectors, no per-domain overrides. Newer Cloudflare Turnstile
  variants (`cf-turnstile-wrapper`, custom iframes) are not in the list.
- [`_BLOCK_TITLE_FRAGMENTS`](backend/applier/captcha_handler.py#L52) is
  English-only. Cloudflare's localised pages (German "Einen Moment bitte"
  / French "Veuillez patienter") will not match.
- [`_MULTI_STEP_DOMAINS`](backend/applier/auto_apply.py#L30) contains only
  LinkedIn. Workday, Greenhouse Modals, SmartRecruiters, Lever, iCIMS —
  all behave like LinkedIn (modal-opens-on-click) and will all be funnelled
  through Tier 1 where they will reliably fail.

### [SEV-3] Coverage gaps
- No test for `recorder.record()` updating an existing placeholder (the
  AUTO/ASSISTED success path), and **no test for the
  `REMOTE_SUBMITTED_LOCAL_FAILED` route end-to-end** — the recorder's
  failure handling is only covered by `test_apply_state.py`'s
  `test_remote_submitted_local_failed_terminal` which fakes the FSM, not
  the recorder.
- No test for cancellation **during** a Tier 2 captcha wait, or during
  a Tier 1 form fill (only post-fill confirm-wait is covered).
- No test verifying `engine._confirm_events` is cleaned up after a crash
  in `chart.run()` — the `finally` exists ([`engine.py:166-168`](backend/applier/engine.py#L166))
  but isn't asserted by any test.
- No test for the `linkedin` vs `linkedin_com` directory mismatch.

### [SEV-3] Dead code / unused branches
- `ApplyContext.browser` ([`state.py:110`](backend/applier/state.py#L110)) — declared, never assigned.
- `Transition.on_exit` — only the FSM tests use it
  ([`test_apply_state.py:236-267`](tests/test_apply_state.py#L236)); no
  production transition in [`engine.py:347-379`](backend/applier/engine.py#L347)
  defines `on_exit`. The whole exit-hook code path in the driver
  ([`state.py:214-223`](backend/applier/state.py#L214)) is exercised only
  by tests.
- `LEGACY_APPLIED_ALIASES` ([`__init__.py:64`](backend/applier/__init__.py#L64))
  is "for backwards compatibility with pre-consolidation rows" — there is
  no DB migration removing those rows and no plan to drop the aliases. It
  is permanent baggage, not transitional.
- `STATUS_INTERVIEW`, `STATUS_OFFER`, `STATUS_REJECTED`
  ([`__init__.py:46-48`](backend/applier/__init__.py#L46)) are declared
  but no writer in the codebase persists them — confirmed by the comment
  *"No writer persists these yet; they are part of the canonical vocabulary
  so that PATCH validators … can accept them once the lifecycle UI lands.
  Forward use only — do not grep-trace as dead code."* This is honest
  documentation, but it is still un-exercised code.
- The MANUAL strategy's downloads-copy logic
  ([`manual_apply.py:31-45`](backend/applier/manual_apply.py#L31)) is
  exercised in tests only via mocking `webbrowser.open`; no test verifies
  that the CV actually lands in `~/Downloads`.

### [SEV-3] Mixed concerns: `daily_limit.py` imports `_utc_now` from itself, recorder imports it back

[`recorder.py:28`](backend/applier/recorder.py#L28) imports `_utc_now` from
`daily_limit.py`. The function is module-private (leading underscore) but
is consumed by another module. It should live in a shared `utils.py` or be
exposed under a public name. Same nit: `follow_up.py:32` defines its **own**
`_utc_now` — three definitions across the package.

---

## 13. File inventory

| File | LOC | Role |
|---|---|---|
| [`__init__.py`](backend/applier/__init__.py) | 128 | Canonical status vocabulary (`RESULT_*`, `STATUS_*`, `LEGACY_APPLIED_ALIASES`) + `normalize_result_status()` + `ApplicationRecordError` (EH-07 typed exception). |
| [`engine.py`](backend/applier/engine.py) | 430 | `ApplicationEngine` — orchestrator. Builds the FSM transition table, runs `Statechart`, dispatches to the three strategies, signals confirm/cancel via per-job events. |
| [`state.py`](backend/applier/state.py) | 246 | The BE-R4 FSM foundation: `State` enum (10 states), `ApplyContext` dataclass, `Transition` dataclass, `Statechart` driver. Strategy collapse deferred — middle states are pass-through. |
| [`daily_limit.py`](backend/applier/daily_limit.py) | 167 | `DailyLimitGuard` with the atomic `reserve_slot()` that closes the PC-04/DB-06 TOCTOU race. Read-only `remaining_today`/`can_apply` retained for the batch runner. |
| [`auto_apply.py`](backend/applier/auto_apply.py) | 439 | `AutoApplyStrategy` — full submit. Tier 1 → `PlaywrightFormFiller.fill_and_submit`; Tier 2 → browser-use agent with `apply_review` WS broadcast + confirm/cancel wait + second agent run for the submit click. |
| [`assisted_apply.py`](backend/applier/assisted_apply.py) | 286 | `AssistedApplyStrategy` — pre-fill only. Tier 1 → `PlaywrightFormFiller.fill_only`; Tier 2 → browser-use agent that fills and leaves the window open for the user. ~80% copy-paste with `auto_apply.py`. |
| [`manual_apply.py`](backend/applier/manual_apply.py) | 63 | `ManualApplyStrategy` — opens the apply URL in the OS default browser, copies CV/letter to `~/Downloads`, returns. No LLM, no Playwright. Also hosts the `ApplicationResult` Pydantic model. |
| [`form_filler.py`](backend/applier/form_filler.py) | 477 | `PlaywrightFormFiller` — Tier-1 implementation. `_clean_form_html` → single Gemini prompt → JSON selector mapping → `page.fill()`/`page.set_input_files()`/`page.click()`. Two public methods: `fill_and_submit` (auto) and `fill_only` (assisted). |
| [`captcha_handler.py`](backend/applier/captcha_handler.py) | 318 | CAPTCHA & Cloudflare-block detection (selector + title/body-text), `wait_for_captcha_resolution` polling loop with WS broadcasts, `preflight_check_url` two-phase headless-then-visible helper. Session state persisted to `data/browser_profiles/{site}/state.json`. |
| [`recorder.py`](backend/applier/recorder.py) | 173 | `ApplicationRecorder` — persists the `Application` row + initial `ApplicationEvent`, flips `JobMatch.status` on success; mutates the reserved placeholder in-place. `release_reserved_slot()` for the cancel/fail compensation path. Raises `ApplicationRecordError` (EH-03) on DB write failure. |
| [`follow_up.py`](backend/applier/follow_up.py) | 93 | `scan_overdue(threshold_days=7)` — inserts one `follow_up_due` event per `applied` application older than 7 days that doesn't already have one. Idempotent, opens its own session. Triggered at startup and at each batch run, never from the engine. |
