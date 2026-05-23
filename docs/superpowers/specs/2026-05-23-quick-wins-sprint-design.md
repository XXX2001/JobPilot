# Quick-Wins Sprint — Design Spec

**Date:** 2026-05-23
**Status:** Design — approved by user, awaiting plan
**Author:** Brainstorming session, summarised by Claude
**Predecessor:** [Forward-looking improvements report](../../reports/2026-05-23-improvements/INDEX.md) Top 10 items 1–7
**Successor:** Implementation plan at `docs/superpowers/plans/2026-05-23-quick-wins-sprint.md` (to be written)

---

## 1. Goal

Ship the seven highest-value-per-day improvements identified in the post-sprint forward-looking report as a sequenced set of small PRs that mirror the PR-0..PR-10 cadence. Each PR is independently reviewable, revertable, and lands on `main` with the existing acceptance bar (green pytest, 0 svelte-check errors, no new pyright regressions).

**The sprint is explicitly not a refactor sprint or a feature-bet sprint.** Larger improvements (Today dashboard, Gmail integration, apply-flow FSM) are deferred to subsequent sprints. This one optimises for *user-perceived value per engineering day*.

## 2. Non-goals

- **No new infrastructure.** No reintroduced scheduler, no notification service, no caching layer.
- **No mobile / responsive overhaul.** Per the product-gap report's contrarian position.
- **No deletion of `LetterPipeline` or `generate_diff`.** That decision is coupled to whether we build the cover-letter editor; out of scope for this sprint.
- **No configurable follow-up threshold.** Hardcoded 7 days for v1; trivial to add a setting later via the qw-3 helper.
- **No hotkey remapping.** Single fixed binding per action.
- **No filtering or pagination on the CSV export.** Users filter in their spreadsheet.

## 3. Scope summary

| # | PR slug | Touches | Effort | Depends on |
|---|---|---|---|---|
| qw-1 | `delete-dead-utils` | backend only | XS | — |
| qw-2 | `cv-upload-bytes` | backend + frontend | XS | — |
| qw-3 | `settings-upsert-helper` | backend refactor | S | — |
| qw-4 | `daily-limit-meter` | backend + frontend | S | — |
| qw-5 | `follow-up-reminders` | backend + frontend | S–M | qw-3 (recommended order; no hard dep) |
| qw-6 | `applications-export-csv` | backend + frontend | S | — |
| qw-7 | `global-hotkeys` | frontend only | S | qw-4 (sidebar settled) |

**Total estimated effort:** ~6–9 engineering days.

**Total LOC delta (estimated):** roughly +650 source / −250 dead / +400 tests.

## 4. Architecture & integration

### 4.1 What's new vs. what's extended

The sprint adds two new modules and one new frontend utility; everything else extends existing files.

**New backend files**
- `backend/applier/follow_up.py` — single function, scans for overdue applications, creates `follow_up_due` events. Pure read-then-write helper; no class.
- `backend/api/applications_export.py` — single FastAPI route, streams CSV. Kept separate from `applications.py` so the latter stays focused on apply-flow concerns.

**New frontend files**
- `frontend/src/lib/utils/hotkeys.ts` — keyboard-event dispatcher with per-route registration and an input-focus guard.
- `frontend/src/lib/components/HotkeyHelp.svelte` — modal listing all active bindings, opened by `?`.
- `frontend/src/lib/stores/dailyLimit.ts` — Svelte store, polls + WS-invalidates the daily-limit counter.

**Extended files (significant edits)**
- `backend/api/settings.py` — extract `_upsert_singleton` (qw-3), add `POST /api/settings/profile/cv-upload` (qw-2).
- `backend/api/applications.py` — extend `GET /api/applications` to accept `?needs_follow_up=true` filter (qw-5).
- `backend/main.py` lifespan — call `follow_up.scan_overdue` at startup (qw-5).
- `backend/scheduler/batch_runner.py` — same call at the top of `run_batch` (qw-5).
- `backend/models/application.py` — extend `ApplicationEvent.event_type` Literal with `"follow_up_due"`.
- `frontend/src/routes/+layout.svelte` — daily-limit pill, global hotkey listener.
- `frontend/src/routes/+page.svelte` — register queue-page hotkeys.
- `frontend/src/routes/cv/+page.svelte` and `SetupWizard.svelte` — wire CV upload to a real `fetch`.
- `frontend/src/routes/tracker/+page.svelte` — "Needs follow-up" tab, "Export CSV" button.
- `frontend/src/lib/components/CVReviewPanel.svelte` — register review-panel hotkeys.

### 4.2 Data flow

Two new data flows are introduced; nothing else changes shape.

**Follow-up reminder flow** (qw-5):

```
[batch_runner.run_batch start]   [main.py lifespan startup]
            \                       /
             v                     v
        follow_up.scan_overdue(db)
                    |
                    v
    SELECT applications WHERE
      status='applied'
      AND applied_at < now() - 7d
      AND no event of type 'follow_up_due'
      AND no event of type 'follow_up'
                    |
                    v
    INSERT ApplicationEvent(event_type='follow_up_due')
                    |
                    v
    GET /api/applications?needs_follow_up=true
            |
            v
    Tracker "Needs follow-up" tab
```

**CV upload flow** (qw-2):

```
User picks .tex in FE
        |
        v
POST /api/settings/profile/cv-upload (multipart)
        |
        v
Validate: extension, size ≤ 1 MB, no path traversal
        |
        v
Write to data/templates/<sanitized-name>.tex
        |
        v
UPDATE UserProfile.base_cv_path = '<that path>'
        |
        v
Return { path, filename, size_bytes }
        |
        v
FE shows "Registered at: templates/<name>.tex"
```

### 4.3 Compatibility

- **DB schema** — no migrations. The `ApplicationEvent.event_type` Literal extension is a Python-side type change; the column stores arbitrary strings already.
- **API contract** — purely additive. One new POST, one new GET (export), one new GET (limit-status), one extended GET (existing `/api/applications` gains an optional query param). No existing route shape changes.
- **WS protocol** — unchanged. The daily-limit meter listens to existing `apply_result` messages; no new message type.
- **Settings file format** — unchanged.

## 5. Per-PR specs

Each PR follows the template:
- **Scope** — what the PR is and isn't
- **Files** — every file touched, with reference
- **Key decisions** — design choices and their *why*
- **Acceptance criteria** — observable signals the PR is done
- **Budget** — rough LOC delta

### 5.1 qw-1 — `delete-dead-utils`

- **Scope.** Delete two backend modules with zero importers; remove their entries from `docs/file-map.md`. No behaviour change.
- **Files.**
  - Delete [`backend/utils/retry.py`](../../../backend/utils/retry.py) (41 LOC).
  - Delete [`backend/utils/source_health.py`](../../../backend/utils/source_health.py) (107 LOC).
  - Update [`docs/file-map.md`](../../file-map.md) — remove the two rows.
- **Key decisions.**
  - Re-confirm zero importers with `grep -rn 'source_health\|async_retry\|health_monitor' backend/` at PR-open time — the codebase moves and an unused-today module could grow callers tomorrow.
  - **No `LetterPipeline` deletion.** That's coupled to the cover-letter-editor decision (see product-gap report PG-2); explicitly out of this sprint.
- **Acceptance.** `pytest` green, `pyright backend` no new errors, grep confirms no surviving references.
- **Budget.** −150 LOC source, 0 LOC tests.

### 5.2 qw-2 — `cv-upload-bytes`

- **Scope.** Replace the FE-12 placebo. Browser POSTs the `.tex` file; backend writes it to `data/templates/<sanitized-filename>.tex` and sets `UserProfile.base_cv_path` to that path.
- **Files.**
  - New endpoint in [`backend/api/settings.py`](../../../backend/api/settings.py): `POST /api/settings/profile/cv-upload`. Accepts multipart upload, validates, persists, returns `{path: str, filename: str, size_bytes: int}`.
  - [`frontend/src/routes/cv/+page.svelte:56-81`](../../../frontend/src/routes/cv/+page.svelte#L56-L81) — replace the fake-register block with a real `fetch(... FormData)` call. Show the resolved path in the success message.
  - [`frontend/src/lib/components/SetupWizard.svelte:30-50`](../../../frontend/src/lib/components/SetupWizard.svelte#L30-L50) — same fix in the wizard flow.
  - New `tests/test_settings_cv_upload.py`.
- **Key decisions.**
  - **Destination = `data/templates/`** because `_resolve_cv_path` ([`backend/scheduler/batch_runner.py:60-84`](../../../backend/scheduler/batch_runner.py#L60-L84)) already auto-detects `.tex` files there. Two independent code paths — explicit `UserProfile.base_cv_path` and the templates-dir scan — both find the file. This is a deliberate redundancy: if a future bug clears `base_cv_path`, scanning still works.
  - **Filename sanitisation** — slug the user's filename (`[^a-zA-Z0-9._-]` → `_`), keep the extension, reject the result if empty after sanitisation. Explicitly reject paths containing `..` or `/` before slugging — defence in depth.
  - **Allowed extensions: `.tex`, `.cls`.** Other LaTeX inputs (`.sty`, `.bib`) might come later but aren't in the current batch_runner contract.
  - **Max size: 1 MB.** LaTeX templates that big are pathological — either binary contamination or an attempt to fill disk.
  - **Response includes the resolved path** so the FE can show "CV registered at: templates/my-cv.tex" — gives the user a breadcrumb if something later goes wrong.
- **Acceptance criteria.**
  - Happy path: upload `cv.tex` → file appears at `data/templates/cv.tex`, `UserProfile.base_cv_path` set, response 200 with payload.
  - Reject `.pdf` → 415 with explanatory message.
  - Reject 2 MB file → 413.
  - Reject `../../etc/passwd.tex` → 400 (path-traversal guard fires before slugging).
  - Existing `tests/test_api_routes.py` settings tests still pass.
- **Budget.** +120 LOC backend, +30 LOC frontend, +60 LOC tests.

### 5.3 qw-3 — `settings-upsert-helper`

- **Scope.** Extract the field-by-field upsert duplicated across `PUT /api/settings/profile` and `PUT /api/settings/search` into a single helper. Refactor only — no behaviour change.
- **Files.**
  - [`backend/api/settings.py`](../../../backend/api/settings.py) — add `_upsert_singleton(db, model_cls, row_id, body, defaults)` near the top; rewrite both `PUT` endpoints to use it. Each endpoint drops from ~50 LOC to ~10 LOC.
  - [`tests/test_api_routes.py`](../../../tests/test_api_routes.py) — add a targeted regression test for the F-Q4 bug class.
- **Key decisions.**
  - **`exclude_unset=True` is the linchpin.** `body.model_dump(exclude_unset=True)` gives PATCH-like semantics — unset fields stay at their existing DB value instead of being overwritten with `None`. This is the standard Pydantic v2 pattern and matches what every modern REST API does.
  - **Keep `defaults` explicit per endpoint.** Don't try to derive defaults from the model. Two reasons: (a) reading the route, a future engineer immediately sees what gets created on first-run; (b) some defaults (e.g., `daily_limit=10`) come from `backend.applier.daily_limit.DAILY_LIMIT` and aren't model-side.
  - **Private leading-underscore helper, not `backend/utils/`.** Two callers only; promoting to a shared util now is premature abstraction.
- **Acceptance criteria.**
  - All existing settings tests pass with no modifications.
  - New test: extend `ProfileUpdate` with a hypothetical `notes: str | None = None` field; send a PUT that omits `notes`; verify the existing `notes` value is preserved (not overwritten with `None`). The test enforces the bug class is structurally prevented.
- **Budget.** −100 LOC source net, +20 LOC tests.

### 5.4 qw-4 — `daily-limit-meter`

- **Scope.** Surface existing `DailyLimitGuard` state in the sidebar as a "N / 10 applications today" pill.
- **Files.**
  - New endpoint in [`backend/api/applications.py`](../../../backend/api/applications.py): `GET /api/applications/limit-status` → `{used: int, limit: int, resets_at: ISO}`. Reads the same data source `DailyLimitGuard.reserve()` reads — no recount logic.
  - New `frontend/src/lib/stores/dailyLimit.ts` — Svelte store; fetches on mount, sets up a 60s interval, listens for `apply_result` on the existing WS message bus and refetches.
  - [`frontend/src/routes/+layout.svelte`](../../../frontend/src/routes/+layout.svelte) — compact pill under the WS-status block.
  - New `tests/test_limit_status.py`.
- **Key decisions.**
  - **Polling + WS invalidation, not pure push.** The 60s poll is the safety net for (a) the first page load before any WS message has fired, and (b) tabs left open across midnight (limit resets, no apply happened to trigger a push).
  - **Color thresholds hardcoded in the component**: gray ≤ 6, amber 7–8, red 9–10. Not a setting — those thresholds are aesthetic, not policy.
  - **`resets_at` in the response** so the pill can show "resets in 4h" via a tooltip. Tooltip is optional in this PR.
- **Acceptance criteria.**
  - Endpoint test: fresh day → `{used: 0, ...}`; after 3 applies → `{used: 3, ...}`; at cap → `{used: 10, limit: 10}`.
  - Manual smoke: open app, trigger a test apply via the existing button, watch the counter increment without a page refresh.
- **Budget.** +40 LOC backend, +60 LOC frontend, +30 LOC tests.

### 5.5 qw-5 — `follow-up-reminders`

- **Scope.** When an application has been `applied` for ≥ 7 days with no `follow_up` event, surface it in a "Needs follow-up" tab on `/tracker`. Lazy trigger — fires on batch-run start + app startup, no background scheduler.
- **Files.**
  - New `backend/applier/follow_up.py` containing `async def scan_overdue(db: AsyncSession, threshold_days: int = 7) -> int` — returns count created. Idempotent.
  - [`backend/main.py`](../../../backend/main.py) lifespan: call `follow_up.scan_overdue(db)` once at startup, wrapped in try/except + log on error (must not block startup).
  - [`backend/scheduler/batch_runner.py`](../../../backend/scheduler/batch_runner.py) `run_batch` start (before "Searching for jobs…" broadcast): same call, same error guard.
  - [`backend/models/application.py`](../../../backend/models/application.py) — extend `ApplicationEvent.event_type` `Literal` with `"follow_up_due"`. Also update `CreateEventRequest` if it constrains the type.
  - [`backend/api/applications.py`](../../../backend/api/applications.py) — extend `GET /api/applications` to accept `?needs_follow_up=true`. When true, filter to applications that have an open `follow_up_due` event (i.e., the event exists AND no subsequent `follow_up` event by the user resolves it).
  - [`frontend/src/routes/tracker/+page.svelte`](../../../frontend/src/routes/tracker/+page.svelte) — new "Needs follow-up" tab with count badge.
  - New `tests/test_follow_up.py`.
- **Key decisions.**
  - **7 days hardcoded** for v1. If users want it configurable, qw-3's `_upsert_singleton` makes that a 5-line addition to `SearchSettings` later.
  - **Lazy trigger only**: startup + batch-run start. No background polling. Worst case the user sees the reminder one batch-run late — acceptable for a weekly cadence.
  - **Resolution model: the user logs a follow-up.** The existing `POST /api/applications/{id}/events` already accepts `event_type="follow_up"` ([`backend/api/applications.py`](../../../backend/api/applications.py)). The filter for `?needs_follow_up=true` excludes any application where a `follow_up` event exists after the `follow_up_due` event. No new resolution endpoint, no `resolved_at` field — leans on the existing event log.
  - **No notification channel beyond the in-app badge in this PR.** Browser push / email is a separate concern; the badge validates the workflow first.
- **Acceptance criteria.**
  - Test (a): empty DB → `scan_overdue` returns 0, no events created.
  - Test (b): app with `applied_at = now() - 3d` → `scan_overdue` returns 0.
  - Test (c): app with `applied_at = now() - 8d` and no prior events → `scan_overdue` returns 1, one `follow_up_due` event exists.
  - Test (d): re-run `scan_overdue` immediately → returns 0 (idempotent).
  - Test (e): user posts `follow_up` event → next call to `GET /api/applications?needs_follow_up=true` excludes the application.
  - Test (f): WebSocket smoke — none; we don't broadcast on follow-up-due creation in this PR.
- **Budget.** +120 LOC backend, +80 LOC frontend, +100 LOC tests.

### 5.6 qw-6 — `applications-export-csv`

- **Scope.** `GET /api/applications/export?format=csv` streams a CSV of all applications joined with job, match, and most-recent event. Tracker page gets a download button.
- **Files.**
  - New `backend/api/applications_export.py` — kept separate to leave `applications.py` focused on apply-flow concerns. Single route, single SQL with `selectinload` for events, streams via `StreamingResponse`.
  - Wire the new module's `router` in [`backend/api/__init__.py`](../../../backend/api/__init__.py) and `backend/main.py` `include_router`.
  - [`frontend/src/routes/tracker/+page.svelte`](../../../frontend/src/routes/tracker/+page.svelte) — "Export CSV" button in the page header. `<a href="/api/applications/export?format=csv" download>` — no JS needed.
  - New `tests/test_applications_export.py`.
- **Key decisions.**
  - **Columns** (in order): `applied_at, status, method, company, title, location, salary_text, job_url, score, ats_score, last_event_type, last_event_at, last_event_details`. ISO 8601 for all dates. Empty cells for null values.
  - **`?format=csv` query param even though we only support `csv` today.** Future-proofs the URL when PDF/JSON come; FE button reads `?format=csv` explicitly so the contract is clear. Unknown formats → 400.
  - **No filtering / no pagination.** This is a data-dump UX. Users filter in their spreadsheet. If we hit 10k+ rows per user this becomes painful, but we're far from that — leave the bridge for when we cross it.
  - **Stream, don't buffer.** `StreamingResponse` with a generator that yields rows. Python `csv` stdlib module — no `pandas` dependency.
  - **Filename:** `Content-Disposition: attachment; filename="jobpilot-applications-YYYYMMDD.csv"` — today's date, no time, no user ID (single-user product).
- **Acceptance criteria.**
  - Empty DB → CSV with header row only, `Content-Type: text/csv; charset=utf-8`, expected filename.
  - Populated DB → N+1 rows, all columns present, ISO dates render correctly.
  - `?format=json` → 400 with explanatory error.
- **Budget.** +90 LOC backend, +15 LOC frontend, +60 LOC tests.

### 5.7 qw-7 — `global-hotkeys`

- **Scope.** Keyboard navigation across queue + CV review panel + modals. The minimum useful set. Discoverable via a `?` help dialog.
- **Files.**
  - New `frontend/src/lib/utils/hotkeys.ts` — dispatcher with `register(routeId, bindings, options)` / `deregister(handle)` API. Reads `event.key`, ignores when an `<input>`/`<textarea>`/`[contenteditable]` is focused (except `Esc` which blurs). Handles `?` and `/` correctly across browser quirks.
  - New `frontend/src/lib/components/HotkeyHelp.svelte` — modal listing all active bindings for the current route, opened by `?`.
  - [`frontend/src/routes/+layout.svelte`](../../../frontend/src/routes/+layout.svelte) — single `<svelte:window onkeydown={dispatcher.handle}>`; mounts `HotkeyHelp`.
  - [`frontend/src/routes/+page.svelte`](../../../frontend/src/routes/+page.svelte) — register queue-page bindings (`j`/`k`/`a`/`m`/`s`/`Enter`/`Esc`).
  - [`frontend/src/lib/components/CVReviewPanel.svelte`](../../../frontend/src/lib/components/CVReviewPanel.svelte) — register review-panel bindings (`1`/`2`/`3`/`←`/`→`).
  - New `tests/frontend/hotkeys.test.ts` if a Vitest setup exists (check repo state at PR-open time; if not, add a manual test checklist in the PR description instead).
- **Key decisions.**
  - **No mode/leader-key system.** Direct single-key bindings. Vim-style modes have a learning cliff that isn't worth it for a job-search tool.
  - **Input-focus guard is non-negotiable** — any active `<input>`, `<textarea>`, or `[contenteditable]` swallows everything except `Esc` (which blurs the field).
  - **No remapping in this PR.** Configurable hotkeys is a settings-page surface we don't need to invent yet.
  - **`?` opens help** — universal convention, ~30 LOC, makes the rest discoverable. Without this, hotkeys are dark magic.
  - **Per-route registration.** Hotkeys only fire when their route is active. The dispatcher tracks the current route via SvelteKit's `$page.route` and only runs matching bindings.
- **Acceptance criteria.**
  - Manual checklist in PR description: queue navigation, CV review panel navigation, help dialog opens with `?`, hotkeys are silent in inputs, Esc closes modals.
  - Unit test (if Vitest exists): dispatcher's input-focus guard correctly suppresses; deregister cleans up.
- **Budget.** +180 LOC frontend, +40 LOC tests.

## 6. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **qw-2 path-traversal regression** — upload accepts a sanitised name that still escapes `data/templates/` | low | high (RCE-adjacent) | Reject pre-slug; verify post-slug path is a descendant of `data/templates/` via `Path.resolve().is_relative_to(templates_dir)`; test covers the attempt. |
| **qw-5 duplicate `follow_up_due` events** — race between the startup call and a batch-run call | medium | low (cosmetic dup) | Idempotency check inside `scan_overdue`: `WHERE NOT EXISTS (SELECT 1 FROM events WHERE app_id = ? AND event_type = 'follow_up_due')`. Test (d) enforces. |
| **qw-6 large CSV blocks event loop** — generator iterates a huge result set synchronously | low | medium (latency spike) | `StreamingResponse` + async iterator over chunks; SQLAlchemy's async session naturally yields per-batch. Add explicit `await asyncio.sleep(0)` every N rows if profiling shows blocking. |
| **qw-7 hotkey conflict with browser shortcuts** — `?` is also Firefox quick-find | low | low | Most browsers only intercept `?` when no text is focused and `event.preventDefault()` is honoured. Test in Chrome + Firefox manually. |
| **qw-3 `exclude_unset` breaks a caller that relied on `None` meaning "clear this field"** | medium | medium (data loss) | Audit current FE callers — if any send `null` to clear a field, document that pattern and use `exclude_none=False, exclude_unset=True` (Pydantic distinguishes them). Targeted test covers both omitted-vs-null. |

## 7. Out of scope (deferred to follow-up sprints)

Documented here so reviewers don't ask "why didn't you also do X":

- **"Today" dashboard** (item 9 in the report) — the bigger UX bet. Needs its own design pass.
- **Gmail Phase 1 integration** (item 8) — already pre-designed in [`docs/reports/2026-05-22-audit/03-gmail-integration-design.md`](../../reports/2026-05-22-audit/03-gmail-integration-design.md); needs OAuth scaffolding which is sprint-sized on its own.
- **Apply-flow FSM extract** (item 10) — the multi-week refactor bet. Foundation for many future product features.
- **`LetterPipeline` and `generate_diff` deletion** — paired with the cover-letter editor decision.
- **PDF export** — CSV ships first; PDF is one design iteration away (template needs review).
- **Configurable follow-up threshold** — trivial follow-up once qw-3 lands.
- **Hotkey remapping** — settings-surface design pass needed.
- **Browser push notifications for follow-ups** — separate sprint; needs service worker + permission UX.

## 8. Acceptance bar for the whole sprint

The sprint is "done" when:
- All 7 PRs are merged to `main` in order.
- `pytest` is green (currently 315 / 0 / 7; +6 tests across PRs → target ~321 / 0 / 7).
- `pyright backend` shows no new errors above the current 40 / 7 baseline.
- `svelte-check` shows 0 errors (1 pre-existing a11y warning is unchanged).
- The CHANGELOG.md gains a "2026-05-25 — Quick-wins sprint (qw-1 .. qw-7)" entry mirroring the PR-0..PR-10 format.
- The `docs/reports/2026-05-23-improvements/INDEX.md` Top-10 table is updated to mark items 1–7 as `Shipped <date>`.

## 9. References

- [Forward-looking improvements report](../../reports/2026-05-23-improvements/INDEX.md) — Top 10 items 1–7
- [Frontend UX report](../../reports/2026-05-23-improvements/01-frontend-ux.md) — qw-2 (CV upload), qw-4 (limit meter), qw-7 (hotkeys)
- [Backend quality report](../../reports/2026-05-23-improvements/02-backend-quality.md) — qw-1 (dead utils), qw-3 (upsert helper)
- [Product gaps report](../../reports/2026-05-23-improvements/03-product-gaps.md) — qw-5 (follow-up reminders), qw-6 (CSV export)
- [CHANGELOG.md](../../../CHANGELOG.md) — PR-0..PR-10 format reference for the eventual sprint entry
- [Post-sprint verification](../../reports/2026-05-22-audit/POST-SPRINT-VERIFICATION.md) — the current acceptance bar this sprint matches
