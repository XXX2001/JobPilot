# JobPilot — Codebase Deep Dive (2026-05-23)

A complete documentation and critique of the JobPilot codebase, produced by **nine parallel specialist agents** the day after Gmail Phase 1 (gm-1..gm-12) was merged to `main`. Each agent read its scope in full (not sampled) and produced a self-contained markdown report. This INDEX cross-cuts the findings.

> **Why this exists.** Previous reports under `docs/reports/2026-05-22-*` were *task-driven* (what was broken, what to ship next). This one is *structure-driven* — every subsystem, top to bottom, with file:line evidence and a frank critique of what each one actually is vs what it claims to be.

---

## Reports

| # | Report | Scope | Lines | Critique items |
|---|---|---|---|---|
| 01 | [App shell & API layer](01-app-shell-and-api.md) | `main.py`, config, logging, DB engine, `deps.py`, all 13 routers, WS layer, security surface | 513 | 23 (2 CRIT, 5 HIGH, 10 MED, 6 LOW) |
| 02 | [Models & DB schema](02-models-and-schema.md) | `backend/models/`, schema-side `database.py`, alembic schema, defaults seeding | 654 | 10 SEV findings + ER diagram |
| 03 | [Applier subsystem](03-applier-subsystem.md) | `engine.py`, `state.py` FSM, three strategies, captcha, daily limit, follow-up, recorder, form filler | 872 | 12 (4 SEV-1, 4 SEV-2, 4 SEV-3) |
| 04 | [Scraping subsystem](04-scraping-subsystem.md) | Orchestrator, session manager, 6 site adapters, scrapling integration, dead-code verification | 370 | 7 ranked findings |
| 05 | [LLM + LaTeX + matching](05-llm-latex-matching.md) | Gemini client, prompts, cv_modifier/editor, job_analyzer, full LaTeX pipeline, matcher, embedder | 440 | ~15 findings (5 HIGH) |
| 06 | [Gmail integration](06-gmail-integration.md) | Full Phase 1: OAuth, token cache, REST client, sync, classifier, scheduler, REST API, WS, frontend | 547 | 12 severity-tagged items |
| 07 | [Frontend](07-frontend.md) | SvelteKit setup, every route, every component, stores, websocket, hotkeys, types, a11y | 317 | 14 findings (1 CRIT confirmed bug) |
| 08 | [Testing infrastructure](08-testing.md) | conftest, 55 test files / ~470 tests, fixtures, mocking patterns, coverage gaps | 487 | 13 findings |
| 09 | [Ops & deployment](09-ops-and-deployment.md) | `start.py`, Dockerfile, compose, env, alembic operationally, scripts, logging, observability | 353 | 10 findings (3 HIGH) |

**Total:** ~4,550 lines / ~390 KB of structured documentation across **9 specialist analyses**.

---

## Cross-cutting findings

The agents worked independently, but the same patterns surfaced repeatedly. These are the themes you cannot fix one report at a time — they require a sweep across the codebase.

### 1. "Wired but never reached" is now the dominant smell of the codebase

The previous audit cycle (`2026-05-22-improvements`) flagged this pattern 6 times. Today's deep-dive found **10+ more instances**, several of which are claimed as *shipped* by the 2026-05-23 improvements doc:

| Claim | Reality |
|---|---|
| **PG-PRE shipped** — "Wire CV upload to actually POST bytes" | `frontend/src/lib/api.ts:7` hardcodes `Content-Type: application/json`. Both upload sites (`cv/+page.svelte:58`, `SetupWizard.svelte:36`) use FormData → it goes out as `application/json` with a multipart body and no boundary. Verified by me directly: the fix never reached the client edge. **The first-run trust-break is still broken.** |
| **PG-1 shipped** — "Follow-up reminders (7-day post-apply nudge)" | `applications.last_correspondence_at` exists and is *written* by `api/correspondence.py:121`. **Nothing reads it.** `follow_up.scan_overdue` computes from `applied_at` only. The column is write-only telemetry. |
| **BE-R4 shipped** — "Extract apply-flow state machine" | The FSM exists (`applier/state.py`), but 4 of 5 middle states (`CAPTCHA_CHECK`, `FILLING`, `AWAITING_CONFIRM`, `SUBMITTING`) are pure pass-throughs. All real work still runs inside `RECORDING.on_enter` inside the strategy. `ApplyContext.browser` is declared but never assigned, so `FAILED.on_enter`'s `browser.stop()` is dead. Browser cleanup is still in each strategy's `try/finally`. |
| **The Gmail "watch_expiration" column from the design doc** | Dropped from the shipped `GmailCredential`. Phase 2 (push notifications) will need a fresh migration. |
| `PUT /api/settings/sources` | Returns 200 with a guidance message and **discards the body**. A write that lies. |
| `POST /api/documents/{match_id}/regenerate` | Returns `status="queued"`, declares an unused `BackgroundTasks` param, **queues nothing**. |
| `StatusBar.svelte` | Listens for three `*_progress` WS message types **the backend never emits**. Marked TODO in-file but still rendering. |
| `latex/validator.py` | Never imported from `pipeline.py`. Dead. |
| `LaTeXInjector.inject_summary_edit` / `inject_experience_edits` | Reference `CVSummaryEdit` / `CVExperienceEdit` that don't exist. |
| `lib/components/JobCard.svelte`, `TypewriterText.svelte` | Not imported by any route. |
| `search_settings.batch_time` column | Created `NOT NULL` by initial alembic migration. No model field. Zero references. Dead since at least 4 migrations ago. |
| `deps.py` singleton getters | Defines 5 `get_*(request)` helpers (`get_session_manager`, `get_apply_engine`, etc.). **Zero routers import them** — everyone reaches into `request.app.state` directly. |
| Inbox FE `recruiter_outreach` color | Backend never emits this category. |

The pattern is consistent: features get *scaffolded* and *committed*, but the loop is never closed. Either finish them or delete them — the current state ("the code suggests it works") is the most expensive option.

### 2. Silent failures are still the second-biggest risk

Multiple agents independently surfaced silent-failure smells; together they sketch a system where errors are routinely demoted to log lines:

- **Lifespan demotes singleton-init failures to a warning** (`main.py`). Production can boot half-broken with no signal. **(CRIT)**
- **`/api/queue/refresh` discards the `asyncio.Task` handle** (`api/queue.py:153`). The 5-minute UI timeout is a lie — backend keeps scraping.
- **Form filler swallows fill exceptions at DEBUG** (`form_filler.py:144`). The review screenshot is then taken on a half-filled form.
- **`GeminiClient` wraps every non-429 exception as `GeminiRateLimitError`** (`gemini_client.py:188`). A broken API key looks identical to a quota burst; silent unmodified CVs forever.
- **No timeout on Tectonic** (`compiler.py:72`) or on Gemini calls. A runaway PDF or hung HTTP call ties up a worker indefinitely.
- **Sites silently return 0 jobs.** No per-source health counter (the deleted `source_health.py` was meant for this; deletion left no replacement).
- **Brittle CSS selectors fall back silently** to "feed the whole page to Gemini" (`scrapling_fetcher.py:303`).
- **`GmailSyncWorker._persist_one` swallows `IntegrityError`** — legitimate dedup, but blanket-catching `IntegrityError` would also mask a real FK violation. Should narrow to `UniqueViolation`.
- **WS handler silently drops unknown message types**, with no log.
- **OAuth callback failure paths return generic redirects** with no `gmail_error` query param handler on the settings page.

The principle "no silent failures" is not enforced anywhere structurally — no linter, no review rule, no convention. Until something *enforces* it, each new feature adds more.

### 3. Twin migration tracks have drifted; foreign keys are off

| Track | What it does | What's wrong |
|---|---|---|
| `Base.metadata.create_all` + 4-entry `_migrate_add_columns` shim | Runs on every startup via `init_db()` | **The only one that actually runs.** Carries the schema in practice. |
| Alembic (`alembic/`, `env.py`, 4 revisions) | Fully configured, async-aware | **Never invoked by any deploy path.** Latest revision pre-dates Gmail, Job.country, the JobMatch fit-engine columns, cv_modification_sensitivity, last_correspondence_at, and several others. |

Anyone running `alembic upgrade head` gets a *different* schema from anyone running the app. Worse:

- **FK constraints are essentially absent.** Only `ApplicationCorrespondence` declares `ForeignKey(..., ondelete="CASCADE")`. Every other "FK" (`Job.source_id`, `JobMatch.job_id`, `Application.job_match_id`, `TailoredDocument.job_match_id`, `ApplicationEvent.application_id`) is a bare `Mapped[int]`.
- **Even the declared ones aren't enforced.** `PRAGMA foreign_keys = ON` is not set on connect — only `journal_mode=WAL` is. SQLite silently ignores FK declarations without the pragma.
- **`search_settings.batch_time` is dead but NOT NULL.** Inserts from any non-alembic schema will fail differently than from the runtime-migrator schema. Two schemas, two bugs.
- **No `CheckConstraint` on string-enum columns** (`JobMatch.status`, `Application.status`/`method`, `ApplicationCorrespondence.direction`, `GmailMessage.category`, `TailoredDocument.doc_type`).
- **`PATCH /api/applications/{id}` accepts arbitrary `status: Optional[str]`** in `UpdateApplicationRequest`, bypassing the lifecycle-state Literal set used at create-time.

### 4. Cancellation & concurrency are unsolved

- "Scan for Jobs" is uncancellable — the task handle is discarded. The frontend's 5-minute spinner is decorative.
- The 300-second captcha wait is invisible to the FSM and uncancellable from the user side.
- `GeminiClient` mutates instance state (`self._model_name`, `self._candidate_idx`) during concurrent calls. Two concurrent applies can step on each other.
- `APScheduler` boots **inside** the FastAPI lifespan. Scaling to N replicas → N pollers. No leader election. Currently latent because compose runs one replica.
- The test suite is single-DB-shared — `pytest-xdist` is ruled out by the conftest design.

### 5. Copy-paste between siblings has measurable bugs

Not just an aesthetic concern — the duplications carry **divergence**:

| Duplicated thing | Drift |
|---|---|
| `auto_apply._site_key()` vs `captcha_handler._domain_key()` | Returns `linkedin` vs `linkedin_com`. A captcha-preflight session is saved at a different directory than the Tier 2 strategies look for. Confirmed inter-strategy bug. |
| `_INDEED_DOMAINS` in `scrapling_fetcher.py` (9 entries) vs `site_prompts.py` (13) | Wrong country lookups depending on which entry point you hit. |
| `_GOOGLE_DOMAINS` (6 vs 13) | Same. |
| `_has_new_latex_commands` in `applicator.py` and `cv_editor.py` | Two copies of safety-critical LaTeX validation. One could be patched and miss the other. |
| `_utc_now()` / `_now()` 5-line helper | Duplicated across 4 model files + `correspondence.py` + `gmail/credentials.py`. |
| `AsyncMock(spec=AsyncSession)` chain-mock dance | Copy-pasted dozens of times across tests. |
| Frontend types (`Job`, `QueueMatch`, `Application`, `SetupStatus`, `Document`, `DiffEntry`) | Redefined per file. No central `lib/types/api.ts`. |
| `_FakeClient` for `GmailRestClient` | Duplicated three times across Gmail tests. |
| Strategy modules (`auto_apply.py`, `assisted_apply.py`) | ~80% copy-paste (confirmed by previous audit, still standing — BE-R4 only extracted the FSM skeleton). |

### 6. Security & secrets — solid encryption, weak operations

- **Encryption at rest is correct.** Fernet on credentials and OAuth tokens; `SecretStr` everywhere; debug logs strip `app_key`.
- **`CREDENTIAL_KEY` rotation is undocumented.** Losing `.env` permanently bricks every encrypted SiteCredential and Gmail refresh token. Neither README nor `.env.example` warns about this.
- **`.env` in the working copy has live-looking values.** Correctly gitignored; not in commits. But there's no leak alarm.
- **CV upload path traversal guard is good** (defense-in-depth `Path` resolution).
- **OAuth state is HMAC-SHA256 signed** with `CREDENTIAL_KEY`, 10-minute TTL. Good.
- **OAuth state and credential-encryption share the same key.** A single key compromise is two losses. Operationally low-risk because the key is local-only, but worth noting.

### 7. Type safety holes & weak invariants

- **Naive UTC datetimes everywhere.** No timezone-aware columns. Future cross-timezone deployment will surprise.
- **JSON-blob columns hide relational shapes**: `Job.requirements`, `Job.benefits`, `JobMatch.keyword_hits`, all `SearchSettings.*` list columns. `Mapped[Optional[dict]]` is annotated for what are actually `list`s.
- **Defensive nullables masking logic bugs**: `Application.job_match_id` and `TailoredDocument.job_match_id` are nullable despite always being carried by the applier/pipeline.
- **No `relationship()` declarations.** The ORM graph is barely used; every join is hand-written `select().join(...)`. Cheaper to write, more places to forget.
- **Frontend has `as unknown as X` casts** and 6+ duplicated types — no single source of truth for the API shape.

### 8. Testing — broad but shallow in the right places

- **55 Python test files, ~470 tests, 0 frontend tests.** No vitest, no Playwright (the file named `test_windows_playwright.py` is a manual diagnostic, not a regression suite — and currently fails on Linux CI).
- **Single shared SQLite at session scope.** Rules out `pytest-xdist`. The Gmail tests had to invent an email-prefix isolation pattern (`creds-`, `auth-u1`, `sync-u1`, etc.) because of this.
- **Applier engine tests mock `AsyncSession`.** Only `test_daily_limit.py` exercises a real on-disk DB. The full applier path is never end-to-end tested.
- **No property/fuzz tests** for the classifier or matcher.
- **No per-source scraping adapter tests** for any source except Google Jobs.
- **Brittle string-`contains` assertions** are everywhere.
- **No `--cov-fail-under` gate.** Coverage is computed, never enforced.

---

## Top 15 things to do next

Ranked by **(severity × user impact)**, not by effort. The first three are confirmed-shipped-but-broken — they should be re-opened before any new work.

| # | Severity | Item | Source | Notes |
|---|---|---|---|---|
| 1 | **CRIT** | Fix CV upload — strip `Content-Type` when body is FormData in `apiFetch` | UX/Gmail/Frontend reports | Claimed shipped (PG-PRE), still broken at the client edge. First-run trust break. |
| 2 | **CRIT** | Stop demoting singleton-init failures to warnings in lifespan | App-shell report §critique | Production can boot half-broken with no signal. |
| 3 | **CRIT** | Wire `last_correspondence_at` into `scan_overdue` (or revert PG-1) | Applier + Gmail reports | Column is write-only; follow-up reminders fire on `applied_at` and ignore the email evidence the user can see in the UI. |
| 4 | **HIGH** | Decide migration story: either invoke alembic at startup or delete it | Models + Ops reports | Two schemas in the wild = bug factory. |
| 5 | **HIGH** | Set `PRAGMA foreign_keys = ON` on connect & declare missing FKs | Models report | Currently SQLite ignores every FK in the codebase. |
| 6 | **HIGH** | Track the `/api/queue/refresh` task & make it cancellable | Scraping + App-shell | UI lies about a 5-minute timeout. |
| 7 | **HIGH** | Add timeouts on Tectonic + Gemini calls | LLM report | One runaway PDF or hung HTTP call = stuck worker. |
| 8 | **HIGH** | Stop masking all non-429 errors as `GeminiRateLimitError` | LLM report §3 | Invalid API key looks identical to quota burst. |
| 9 | **HIGH** | Remove unused body from `PUT /api/settings/sources` or implement it; same for `regenerate` `BackgroundTasks` | App-shell report | Routes that lie about their behavior are landmines. |
| 10 | **HIGH** | Delete dead code: `latex/validator.py`, dead injector methods, dead `pipeline.generate_diff`, dead frontend components (`JobCard`, `TypewriterText`), dead `StatusBar` WS handlers, `search_settings.batch_time` column | All reports | Each one is a future-you trap. |
| 11 | **MED** | Collapse `auto_apply.py` and `assisted_apply.py` into one strategy parameterized by tier (the deferred half of BE-R4) | Applier report | The FSM extract laid the foundation; this is the payoff. |
| 12 | **MED** | Unify `_site_key()` / `_domain_key()` to one canonical site identifier | Applier report | Currently a confirmed inter-module bug. |
| 13 | **MED** | Add per-source health counters back (replacement for deleted `source_health.py`) | Scraping report | 0-job sources currently invisible. |
| 14 | **MED** | Central `lib/types/api.ts` on the frontend; delete 6 duplicate type defs | Frontend report | Type drift is starting to bite. |
| 15 | **LOW** | Document `CREDENTIAL_KEY` rotation procedure (or assert that loss = data-loss) | Ops report | Operational footgun. |

---

## Cross-references to prior reports

- **Bugs already filed before this deep-dive:** [`../2026-05-22-audit/INDEX.md`](../2026-05-22-audit/INDEX.md) (8 parallel-agent audits) and [`../2026-05-22-audit/POST-SPRINT-VERIFICATION.md`](../2026-05-22-audit/POST-SPRINT-VERIFICATION.md).
- **Pre-ship standards backlog:** [`../2026-05-22-standards/`](../2026-05-22-standards/).
- **Forward-looking improvements (which this report partly invalidates):** [`../2026-05-23-improvements/INDEX.md`](../2026-05-23-improvements/INDEX.md).
- **Gmail Phase 1 design + sprint plan:** [`../2026-05-22-audit/03-gmail-integration-design.md`](../2026-05-22-audit/03-gmail-integration-design.md), [`../../superpowers/plans/2026-05-23-gmail-phase-1.md`](../../superpowers/plans/2026-05-23-gmail-phase-1.md).

The 2026-05-23 improvements doc claims PG-PRE / PG-1 / BE-R4 are **Shipped**. This deep-dive contradicts that on all three. The next sprint should either re-open them or honestly mark them as **Foundation only**.

---

## Method

Nine independent agents, each given:
- A focused scope (e.g. "applier subsystem" or "frontend")
- Full read access (no sampling — every in-scope file read top to bottom)
- A shared output structure (purpose → architecture → mechanics → critique → inventory)
- A required severity-tagged critique section
- An instruction to use `file:line` markdown links for every claim

Total work: ~9 agents × 5-9 minutes wall-clock = ~60 minutes of analysis, run in parallel against a clean working tree on `main`.

Synthesis (this INDEX) verified two cross-agent contradictions directly:
1. **`last_correspondence_at` exists** (`models/application.py:29`) — the Applier agent was wrong to say it doesn't, but its substantive point (`scan_overdue` doesn't read it) was correct.
2. **PG-PRE CV-upload is broken at the client** — confirmed by grepping `frontend/src/lib/api.ts` for `Content-Type` and seeing the hardcoded `application/json`.
