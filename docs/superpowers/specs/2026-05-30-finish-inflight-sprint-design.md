# Finish In-Flight Sprint — Design Spec

**Date:** 2026-05-30
**Status:** Design — approved by user
**Predecessor:** `docs/reports/2026-05-30-dev-consolidation.md` ("Still NOT done" section) and `docs/reports/2026-05-23-codebase-deep-dive/SPRINT-STATUS.md`.
**Scope owner:** in-flight fix-sprint tracks only — T2a, T3, T4a items 4 & 5.

---

## 1. Goal

Close out the three fix-sprint tracks that were started but never landed on `main`,
restoring the codebase to the "production-ready" bar the sprint targeted:

- **T2a — Schema enforcement + unification** — make Alembic the single source of
  truth, turn on real foreign-key enforcement, centralize the UTC-now helper.
- **T3 — Silent-failure elimination** — 10 discrete deliverables that each turn a
  silently-swallowed error into an observable, typed, or timed-out failure.
- **T4a items 4 & 5 — Applier FSM browser lifecycle** — wire `ApplyContext.browser`
  and centralize browser cleanup inside the state machine.

## 2. Non-goals

- The never-started follow-on tracks: **T1b** (honest endpoints), **T4b** (strategy
  collapse), **T7b** (Settings split + WS backoff), **T2b** (schema tightening). These
  are explicitly deferred to a later sprint.
- No new product features (Gmail integration, follow-up reminders, export, etc.).
- No third-party FSM/state library; no schema redesign beyond the catch-up needed to
  make Alembic authoritative.
- No frontend rewrite — only the minimal frontend touchpoints required by a T3
  deliverable (none currently expected) or the existing review-state wiring.

## 3. Working method

- **Branch:** all work lands on `fix/finish-inflight-sprint`, never directly on `main`.
- **Execution:** subagent-driven-development — one implementer subagent per lot, each
  followed by a spec-compliance review then a code-quality review.
- **Stale worktrees are reference only.** The killed worktrees (`fix/T2a-…`,
  `fix/T3-…`, `fix/T4a-…`) are based on `a09a6fa`; `main` is now `41d2f08`. Their
  partial files (notably `tests/test_silent_failures.py`, the FK migration stubs,
  `backend/utils/time.py`) are read for intent, but all code is rebuilt fresh on top
  of current `main`.
- **Ordering is fixed by a real dependency:** T3 deliverable #5 requires FK enforcement
  to exist, so **T2a precedes T3**. T4a is independent and lands last.

## 4. Scope summary

| # | Lot | Touches | Effort | Depends on |
|---|---|---|---|---|
| 1 | T2a — schema enforcement + unification | `backend/database.py`, `backend/models/*`, `alembic/versions/*`, `backend/utils/time.py`, tests | L | — |
| 2 | T3 — silent-failure elimination | `backend/latex/*`, `backend/llm/gemini_client.py`, `backend/applier/*`, `backend/gmail/sync.py`, `backend/api/ws.py`, `backend/api/gmail*`, `backend/scraping/scrapling_fetcher.py`, `backend/config.py`, tests | M | Lot 1 (deliverable #5) |
| 3 | T4a items 4 & 5 — applier FSM browser lifecycle | `backend/applier/{state,engine,auto_apply,assisted_apply}.py`, tests | M | — |

---

## 5. Lot 1 — T2a: Schema enforcement + unification

### 5.1 Scope

Make Alembic the single authoritative schema source, enforce foreign keys at the SQLite
level, and remove the parallel runtime-migration path that has drifted from the
migrations.

### 5.2 Current state (on `main`)

- `PRAGMA foreign_keys=ON` is **not** set; only `PRAGMA journal_mode=WAL`
  (`backend/database.py:28-34`).
- Foreign keys are declared only on `ApplicationCorrespondence`
  (`backend/models/gmail.py:97-104`); the main relational columns are bare `Integer`.
- No `relationship()` declarations anywhere in `backend/models/`.
- Schema is created two ways: `Base.metadata.create_all` + an ad-hoc
  `_migrate_add_columns()` runtime migrator (`backend/database.py:40-94`), **not**
  Alembic at startup. This has drifted: Gmail tables, fit-engine columns
  (`gap_severity`/`ats_score`/`fit_assessment_json`), `jobs.country`,
  `applications.last_correspondence_at`, and several `search_settings.*` columns are
  not in any Alembic revision. A dead `search_settings.batch_time` column exists in the
  initial migration but not in the model.
- `backend/utils/time.py` does not exist; UTC-now logic is duplicated as `_now()`
  (5 models) and `_utc_now()` (~6 api modules). `datetime.utcnow()` is already gone
  from `backend/`.
- `tests/test_db_integrity.py` and `tests/test_migrations.py` do not exist.

### 5.3 Files

- **Modify `backend/database.py`** — add a `PRAGMA foreign_keys=ON` statement on the
  existing `connect` event listener (next to WAL). Remove `_migrate_add_columns()` and
  stop calling it from `init_db()`; `init_db()` becomes "ensure migrations are at head"
  (Alembic-driven) rather than `create_all` + ad-hoc patch.
- **Modify `backend/models/*.py`** — add `ForeignKey(...)` to the bare relational
  columns: `applications.job_match_id` → `job_matches.id`,
  `application_events.application_id` → `applications.id`, `jobs.source_id` →
  `job_sources.id`, `job_matches.job_id` → `jobs.id`,
  `tailored_documents.job_match_id` → `job_matches.id`. Choose `ondelete` semantics per
  relationship (default `CASCADE` for child rows; document each choice inline).
- **New Alembic catch-up revision** — single revision that brings the migration chain
  in sync with the models: create the missing Gmail tables
  (`gmail_credentials`, `gmail_messages`, `application_correspondence`), add the missing
  columns (fit-engine, `jobs.country`, `applications.last_correspondence_at`,
  `search_settings.*`), add the new FK constraints, and drop the dead
  `search_settings.batch_time` column. For SQLite, FK/column changes use Alembic
  **batch operations** (`op.batch_alter_table`).
- **New Alembic data-safety step** — before adding FK constraints, the migration must
  defend against pre-existing orphan rows (either delete orphans or fail loudly with a
  clear message). Decide and document: orphan child rows are deleted (they are
  unreachable once the parent is gone). This runs inside the same revision.
- **New `backend/utils/time.py`** — `utc_now() -> datetime` returning
  `datetime.now(timezone.utc)`. Replace the duplicated `_now()` / `_utc_now()`
  definitions across `backend/models/*` and `backend/api/*` with imports of this helper.
- **New `tests/test_db_integrity.py`** — FK enforcement is ON; inserting a child row
  with a dangling parent id raises `IntegrityError`; `ON DELETE CASCADE` removes child
  rows; the dead `batch_time` column is gone.
- **New `tests/test_migrations.py`** — `alembic upgrade head` runs cleanly on a fresh
  DB; Alembic autogenerate reports **no diff** between the models and the migration head
  (models ↔ migrations are in sync); `downgrade` of the catch-up revision is coherent.

### 5.4 Key decisions

- **Alembic is the single source of truth.** `create_all` + `_migrate_add_columns()` is
  removed. Existing user DBs are handled by stamping/upgrading to head on startup; the
  catch-up revision is written to be idempotent against DBs that already have the
  drifted columns (guard with column/table existence checks where needed).
- **FK enforcement is global** via the `connect` listener, matching the WAL pattern.
- **Test DB parity.** `tests/conftest.py` currently sets `PRAGMA foreign_keys=OFF` for
  its wipe routine; that toggle is preserved *only* around the wipe, and FK must be ON
  for the actual test session so `test_db_integrity.py` is meaningful.
- **`utc_now()` is the only UTC helper.** No behavior change — purely de-duplication.

### 5.5 Acceptance

- `PRAGMA foreign_keys` returns `1` on a live connection.
- `tests/test_db_integrity.py` + `tests/test_migrations.py` pass.
- `alembic upgrade head` is clean on a fresh DB; autogenerate shows no diff.
- `_migrate_add_columns()` is gone; no caller remains.
- `backend/utils/time.py` exists and the duplicated helpers are replaced.
- Full suite green; `pyright backend/` at baseline.

---

## 6. Lot 2 — T3: Silent-failure elimination

### 6.1 Scope

Ten discrete deliverables, each turning a swallowed/hidden failure into an observable
one. The executable spec is `tests/test_silent_failures.py` (ported from the
`fix/T3-silent-failures` worktree). Probing it against `main` today yields **16 failing
/ 1 passing** — all ten deliverables are outstanding (the one pass is the existing 429
rate-limit branch, which must stay green).

### 6.2 Deliverables

1. **Tectonic timeout** — `LaTeXCompiler.compile()` wraps the subprocess in
   `asyncio.wait_for` bounded by `settings.TECTONIC_TIMEOUT_SECONDS`; on timeout it
   kills the process and raises a new `LaTeXCompileTimeout`.
2. **Gemini call timeout** — `GeminiClient` passes `genai.types.HttpOptions(timeout=…)`
   to `genai.Client`, driven by `settings.GEMINI_TIMEOUT_SECONDS` (SDK uses
   milliseconds — multiply by 1000).
3. **Gemini error wrapping** — a non-429 failure raises a new `GeminiCallFailed` (a
   sibling of, not a subclass that is caught as, `GeminiRateLimitError`); the 429 branch
   still raises `GeminiRateLimitError`.
4. **Form-filler WARN logging** — the three swallow points in
   `PlaywrightFormFiller` log at `WARNING` with `selector=`/field context instead of
   `logger.debug`. Exact messages: `"Form fill failed: selector=…"`,
   `"CV upload failed: selector=…"`, `"Letter upload failed: selector=…"`.
5. **GmailSync IntegrityError narrowing** — `_is_gmail_dedup_violation(exc)` returns
   `True` only for the dedup UNIQUE constraint text and `False` for FK violations
   (depends on Lot 1 turning FK on, so FK violations are no longer mistaken for dedup).
6. **WS unknown-type logging** — the WS receive loop logs a `WARNING` containing the
   unknown discriminator instead of silently dropping it; known types (`ping` → `pong`)
   keep working.
7. **OAuth callback error redirect** — a bad `state` on the Gmail OAuth callback issues
   a `302` to `/settings?gmail_error=invalid_state` instead of a bare `400` JSON.
8. **`_clean_html` selector-miss alarm** — `ScraplingFetcher` tracks
   `_selector_miss_counts[site]`, logs a `WARNING` naming the site the first time a
   configured content selector matches nothing, still returns fallback text, and resets
   the counter when a later call matches.
9. **LaTeX escape audit** — `LaTeXInjector` escapes `{company_name}` substitutions via a
   `_escape_latex` helper so a hostile company name (e.g. `\input{evil}`) cannot inject
   live LaTeX; round-trip covers `& % $ _ # { }` and backslash (backslash first).
10. **`apply_review` survives no-client** — `ApplicationEngine` gains
    `record_pending_review` / `get_pending_review` and a `signal_confirm` that consumes
    the snapshot, plus a `GET /api/applications/{id}/review-state` endpoint returning the
    cached payload (404 when absent).

### 6.3 Files

- `backend/latex/compiler.py` (+ `LaTeXCompileTimeout`), `backend/latex/injector.py`
  (+ `_escape_latex`), `backend/llm/gemini_client.py` (+ `GeminiCallFailed`, HttpOptions),
  `backend/applier/form_filler.py`, `backend/gmail/sync.py`
  (+ `_is_gmail_dedup_violation`), `backend/api/ws.py`, `backend/api/gmail*.py` (callback),
  `backend/scraping/scrapling_fetcher.py`, `backend/applier/engine.py` (review cache) +
  `backend/api/applications.py` (review-state endpoint).
- `backend/config.py` — add `TECTONIC_TIMEOUT_SECONDS` and `GEMINI_TIMEOUT_SECONDS`;
  mirror in `.env.example`.
- `tests/test_silent_failures.py` — ported from worktree; the 16 assertions are the
  acceptance bar.

### 6.4 Key decisions

- **The ported test file is the contract.** Implement to make each test pass; do not
  weaken the tests. If a test's import target (`LaTeXCompileTimeout`, `GeminiCallFailed`,
  `_escape_latex`, `_is_gmail_dedup_violation`) does not exist yet, that is the signal
  for the new symbol to add.
- **Deliverable #5 lands after Lot 1** so FK violations actually occur and the negative
  test is meaningful.
- **No behavior change to the happy paths** — timeouts and warnings only fire on the
  failure branch; the 429 path and successful fills/uploads are untouched.

### 6.5 Acceptance

- `tests/test_silent_failures.py` → 17/17 pass (16 previously-failing now green).
- New config keys present in `config.py` and `.env.example`.
- Full suite green; `pyright backend/` at baseline.

---

## 7. Lot 3 — T4a items 4 & 5: Applier FSM browser lifecycle

### 7.1 Scope

Make the FSM's browser-cleanup compensation real (it is currently dead code) and stop
scattering `browser.stop()` across the strategies.

### 7.2 Current state (on `main`)

- `ApplyContext.browser: Optional[BrowserSession] = None` is declared
  (`backend/applier/state.py:106-110`) but **never assigned** (0 occurrences repo-wide).
- `ApplicationEngine.apply()` builds the context without `browser`
  (`engine.py:158-175`); `_dispatch()` calls strategies with scalar params, not `ctx`
  (`engine.py:402-439`), so the browser instance stays local to the strategies.
- `failed_on_enter` reads `c.browser` and would call `.stop()` (`engine.py:334-339`),
  but it is dead because `c.browser` is always `None`.
- Browser cleanup is scattered: `auto_apply.py` (4 `browser.stop()` sites),
  `assisted_apply.py` (1), `form_filler.py` (2 `finally` blocks),
  `captcha_handler.py` (5). Assisted **success** intentionally leaves the browser open
  (`assisted_apply.py:203-211`).
- Type mismatch: `ApplyContext.browser` is typed `BrowserSession`, but the Tier-2
  strategies use `browser_use.Browser`.

### 7.3 Files

- **`backend/applier/engine.py`** — wire the live browser into `ctx.browser` so the FSM
  owns cleanup. Either enrich the strategy return to surface the browser, or pass a
  setter/`ctx` reference into `_dispatch` and have strategies assign `ctx.browser`.
- **`backend/applier/state.py`** — resolve the `browser` type so `.stop()` is callable
  without `type: ignore`; ensure the terminal `FAILED` (and, if appropriate,
  `CANCELLED` / `REMOTE_SUBMITTED_LOCAL_FAILED`) compensation closes the browser.
- **`backend/applier/auto_apply.py` / `assisted_apply.py`** — remove the now-redundant
  `browser.stop()` sites that the FSM compensation covers, while **preserving** the
  "assisted success leaves browser open" behavior explicitly (do not let the FSM close
  it on success).
- **`tests/test_apply_state.py`** — extend: terminal `FAILED`/`CANCELLED` closes the
  browser when `ctx.browser` is set; assisted-success path does not close it; the slot
  release still fires.

### 7.4 Key decisions

- **The FSM is the single cleanup site for failure paths.** Strategy-local `stop()`
  calls for failure/timeout/cancel are removed once the compensation covers them;
  `form_filler` / `captcha_handler` Playwright-context teardown (their own `finally`
  blocks for the synchronous Playwright session) stays, since that is a different
  resource than the Tier-2 `browser_use` browser.
- **Assisted success is the explicit exception** — documented in code and covered by a
  test so a future refactor cannot silently close a browser the user still needs.
- **Backwards compatible at the API boundary** — `POST /api/applications/{id}/apply`
  and WS message types are unchanged.

### 7.5 Acceptance

- `ApplyContext.browser` is assigned on the live apply path; `failed_on_enter`'s
  `.stop()` is reached (no longer dead).
- Extended `tests/test_apply_state.py` passes; `tests/test_apply_engine.py` +
  `tests/test_apply_http.py` stay green unchanged.
- No `type: ignore` introduced for the browser field.
- Full suite green; `pyright backend/` at baseline.

---

## 8. Cross-cutting

- **Baselines hold at every lot:** `uv run pytest -q` green, `uv run pyright backend/`
  at baseline, `npx svelte-check` 0 errors, `alembic upgrade head` clean.
- **CHANGELOG:** add `## fix-sprint (finish) 2026-05-30` summarizing the three lots.
- **Status docs:** update `docs/reports/2026-05-30-dev-consolidation.md` to mark T2a,
  T3, and T4a items 4 & 5 as landed; note the still-deferred follow-on tracks.
- **Merge:** final `--no-ff` merge of `fix/finish-inflight-sprint` into `main`.

## 9. Sprint acceptance

- All three lots merged to `main` via one `--no-ff` merge.
- `tests/test_silent_failures.py` 17/17; new T2a + T4a tests pass; full suite green.
- FK enforcement ON; Alembic the sole schema source (no `_migrate_add_columns`).
- CHANGELOG + status docs updated.

## 10. References

- `docs/reports/2026-05-30-dev-consolidation.md` — "Still NOT done" section
- `docs/reports/2026-05-23-codebase-deep-dive/SPRINT-STATUS.md` — per-track status
- `docs/reports/2026-05-23-codebase-deep-dive/03-applier-subsystem.md` — applier deep-dive
- `tests/test_silent_failures.py` (in `fix/T3-silent-failures` worktree) — T3 executable spec
