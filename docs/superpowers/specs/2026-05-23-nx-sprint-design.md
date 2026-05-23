# NX Sprint — Design Spec

**Date:** 2026-05-23
**Status:** Design — approved by user
**Predecessor:** Forward-looking improvements report Top-10 items 9 and 10; qw-sprint complete (items 1–7 shipped).
**Out of this sprint:** Gmail Phase 1 (user is doing it themselves); `scan_overdue` periodic trigger (deferred — no scheduler yet).

---

## 1. Goal

Ship the last two improvement items reachable without a new infrastructure commitment: extract the apply-flow lifecycle into an explicit state machine, then replace the queue-as-home with a "Today" dashboard. Both land as separate `--no-ff` merges on `main` matching the qw-sprint cadence.

## 2. Non-goals

- No third-party FSM library (e.g. `transitions`, `statemachine`).
- No DB migration for the new `last_dashboard_seen_at` column — use `Alembic` autogenerate if it just works, otherwise add a one-shot `add_column` migration. No schema-overhaul.
- No Gmail dependency in the Today dashboard's "response rate" — render `"—"` until Gmail Phase 1 lands.
- No deletion of `/queue` legacy route. Today becomes default landing; queue remains a stable URL.

## 3. Scope summary

| # | PR slug | Touches | Effort | Depends on |
|---|---|---|---|---|
| nx-1 | `apply-flow-fsm` | backend refactor (applier/) | L | — |
| nx-2 | `today-dashboard` | new endpoint + frontend rewrite of `/` | M | — (independent of nx-1) |

## 4. nx-1 — Apply-flow FSM extract

### 4.1 Scope

Extract the apply lifecycle into an explicit state machine in `backend/applier/state.py`. Collapse the duplicated `try/except/release-slot/close-browser` paths spread across `engine.py`, `auto_apply.py`, `assisted_apply.py`, `form_filler.py`, `captcha_handler.py`.

### 4.2 States

```
Reserved → CaptchaCheck → Filling → AwaitingConfirm → Submitting → Recording → {Applied | Cancelled | Failed | RemoteSubmittedLocalFailed}
```

Terminal states each have one compensation: `Cancelled` releases the daily-limit slot; `Failed` releases + closes the browser; `RemoteSubmittedLocalFailed` records an `application_event(event_type="db_write_failed")` (existing EH-03 behavior preserved).

### 4.3 Files

- **New `backend/applier/state.py`** — `State` enum, `Transition` dataclass, `Statechart` driver class with `async def run(ctx) -> Outcome`. Plain Python — no `transitions` library.
- **Refactor `backend/applier/engine.py`** — strip `_release_reserved_slot` (now an `on_exit` for `Reserved`); engine becomes dispatch + signal routing (~100 LOC, down from 380).
- **Refactor `backend/applier/auto_apply.py`** + **`backend/applier/assisted_apply.py`** — both become thin "build state graph + run" orchestrators (~80 LOC each, down from 439 + 287).
- **New `backend/applier/recorder.py`** — extract `_record_application` into `ApplicationRecorder` collaborator.
- **New `tests/test_apply_state.py`** — every transition + every compensation path exercised.
- **Preserved:** `backend/applier/form_filler.py` + `captcha_handler.py` — internals unchanged; they become "actions" called from state transitions, no API changes.

### 4.4 Key decisions

- **Plain dataclass FSM, no third-party lib.** Keeps deps minimal; the state graph is small (~7 states) and the transition logic is sequential.
- **WS broadcasts auto-fire from transitions.** Existing `apply_review` and `apply_result` messages currently sent from 3 hand-coded sites become `on_enter`/`on_exit` hooks; the `ConnectionManager` API is unchanged.
- **Backwards-compatible at the API boundary.** `POST /api/applications/{id}/apply` and the existing WS message types are unchanged. Internals only.
- **Existing test suite is the safety net.** `test_apply_engine.py` + `test_apply_http.py` (~289 LOC of HTTP-level coverage) must stay green throughout the refactor.

### 4.5 Acceptance

- −400 LOC net across `applier/`
- `tests/test_apply_engine.py` + `tests/test_apply_http.py` pass unchanged
- New `tests/test_apply_state.py` exercises each transition (~10 tests minimum)
- `uv run pytest --tb=no -q` → ~351 / 7 (10 new tests on top of 341)
- `pyright /home/mouad/Web-automation/backend` → 40 / 7 (baseline)
- Pre-existing pyright errors in `applier/` that get fixed as a side-effect of the rewrite are a bonus, not a goal

## 5. nx-2 — "Today" dashboard

### 5.1 Scope

Replace `/` (current Job Queue) with a Today view answering the three questions a job seeker has when they open the app: *what's new since I last looked, what needs my attention right now, how am I doing this week*. Existing queue moves to `/queue` and stays functionally identical.

### 5.2 Files

- **New `backend/api/today.py`** — `GET /api/today` returning `TodayOut` with three sections: `new_matches`, `blocked_actions`, `week_stats`.
- **New Alembic migration** — adds `UserProfile.last_dashboard_seen_at: DateTime | None`. Nullable so existing rows don't need backfill.
- **Modify `backend/models/user.py`** — add the column.
- **New `frontend/src/routes/+page.svelte`** — Today view. Three sections matching the report's design.
- **Move existing** `frontend/src/routes/+page.svelte` → `frontend/src/routes/queue/+page.svelte`. Pure relocation; behavior unchanged.
- **New components:** `frontend/src/lib/components/{NewMatchesFeed,BlockedActionsStrip,WeekStats}.svelte`.
- **Update `frontend/src/routes/+layout.svelte`** — add Queue link to nav.
- **Update `frontend/src/lib/types/api.ts`** (if exists) or add `TodayResponse` type.
- **New `tests/test_today.py`** — endpoint tests (empty / new matches / blocked actions / week stats).

### 5.3 Key decisions

- **"Since last visit" semantics.** Server tracks `UserProfile.last_dashboard_seen_at`. Every `GET /api/today` call returns counts computed against that timestamp, then UPDATEs the timestamp to NOW. First-load (NULL) falls back to "last 24 h". This means refreshing the page resets the "new" count — acceptable for an MVP.
- **Blocked actions defined narrowly.** Three exact signals: (a) sites with `SessionInfo.exists=False` that the user has credentials for, (b) applications in `pending`/`awaiting_submit` status, (c) `JobMatch.status='selected'` matches in `mode='manual'` that are >24 h old with no `Application` row. No more, no less.
- **Week stats:** 7-day rolling: applications submitted, daily-limit usage today, response rate placeholder ("— (requires Gmail integration)"). Reuse existing `/api/analytics/summary` shape where possible — extract a `summarise_week()` helper rather than duplicate.
- **`/queue` stays stable.** No behavior changes; just a path rename. A "Classic queue" link on the Today page points there.
- **No WebSocket integration in this PR.** Today view is purely poll-on-load + manual refresh button. Live update is a Nice-to-have.

### 5.4 Acceptance

- `GET /api/today` returns the three-section payload with sensible shape on empty/populated DBs
- `/queue` URL serves the existing queue view unchanged (manual smoke)
- Today view renders cleanly without backend (empty-state UI for each section)
- Tests cover: empty DB, populated `new_matches`, populated `blocked_actions`, week stats math
- `uv run pytest --tb=no -q` → ~356 / 7 (5 new tests on top of nx-1's 351)
- `pyright /home/mouad/Web-automation/backend` → 40 / 7 baseline
- `cd frontend && npx svelte-check --tsconfig ./tsconfig.json` → 0 errors / 1 pre-existing warning
- Alembic `upgrade head` runs cleanly on the test DB

## 6. Out of scope (deferred)

- **Gmail Phase 1** — user is implementing themselves.
- **`scan_overdue` periodic trigger** — no scheduler in current architecture.
- **Today view live updates via WS** — manual refresh sufficient for v1.
- **"Switch to classic queue" preference persistence** — link only; no per-user default.
- **PDF export of applications** — CSV ships in qw-6; PDF is a future call.
- **Bulk operations on queue** — out of scope.

## 7. Sprint acceptance

- Both `--no-ff` merges land on `main`.
- CHANGELOG entry `## 2026-05-23 — NX sprint (nx-1, nx-2)` added.
- Improvements report INDEX Top-10 rows 9 and 10 marked `(Shipped 2026-05-23)`.
- All baselines (pytest / pyright / svelte-check) hold.

## 8. References

- [Forward-looking improvements report](../../reports/2026-05-23-improvements/INDEX.md) — items 9 (UX-BET / Today dashboard) and 10 (BE-R4 / FSM extract)
- [Backend quality report §4](../../reports/2026-05-23-improvements/02-backend-quality.md#4-if-you-had-a-free-week-investment) — the FSM "free week" investment that nx-1 implements
- [Frontend UX report §3](../../reports/2026-05-23-improvements/01-frontend-ux.md#3-one-bigger-bet--a-today-dashboard-as-the-new-home) — the "bigger bet" that nx-2 implements
- [QW sprint design](2026-05-23-quick-wins-sprint-design.md) — convention reference for PR cadence, commit-message style, acceptance bar
