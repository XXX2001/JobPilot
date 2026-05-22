# JobPilot — Deep-Audit Reports (2026-05-22)

Eight parallel read-only audits covering the dimensions **not** addressed by the standards/naming/error-handling backlog at [`../2026-05-22-standards/INDEX.md`](../2026-05-22-standards/INDEX.md).

> **Scope:** the full repo — Python backend (`backend/`, `alembic/`, `tests/`), SvelteKit frontend (`frontend/src/`), and forward-looking integration design.
> **Source:** 8 specialised agents, each with focused scope, codegraph-assisted exploration, and a mandate to cite `file:line` evidence.
> **How to use:** each report is self-contained — pick the report whose theme you want to fix and follow its prioritised findings.

---

## Reports

| # | Report | Focus | Findings | Key takeaway |
|---|---|---|---|---|
| 01 | [LLM token efficiency](01-llm-token-efficiency.md) | Gemini prompt caching, model tiers, structured output, batching, embeddings | 16 | `Embedder` + `FitEngine` are wired but **never injected** at startup → entire gap-driven CV-skip path is dead. Plus zero context caching, single-tier model routing, no embedding persistence. |
| 02 | [Database intelligence](02-database-intelligence.md) | Schema, indexes, queries, migrations, Postgres-native opportunities | 24 | **Zero indexes**, **zero ForeignKeys**, **zero relationships** in any model. Alembic exists but is bypassed at startup; migrations have drifted ~20 columns from the models — PG migration is impossible today. |
| 03 | [Gmail integration design](03-gmail-integration-design.md) | Forward-looking design proposal (auth, sync, classification, status state machine, auto-adapt) | n/a (design) | Concrete 3-phase rollout (M+L+L) reusing existing Fernet encryption, CV pipeline, and WS protocol. Cost estimate ~$0.04/wk LLM/user via Flash-Lite triage + ATS-domain heuristics. |
| 04 | [Frontend SvelteKit audit](04-frontend-audit.md) | Type safety, state, a11y, WS protocol drift, dead components | 36 | WebSocket vocabulary diverges **three ways** (backend broadcaster ↔ Pydantic models ↔ Svelte handlers) — most of `StatusBar.svelte` is dead code. Plus no API typing layer; CV "upload" never sends file bytes. |
| 05 | [API design](05-api-design.md) | REST conventions, status codes, response models, OpenAPI quality, WS protocol | 17 | 18 of 41 routes have **no `response_model=`** → OpenAPI is unusable for codegen. 3 distinct error envelopes. `POST /apply` is non-idempotent + racy. WS doesn't use its own typed models. |
| 06 | [Performance & concurrency](06-performance-concurrency.md) | Blocking I/O in async, race conditions, batching opportunities | 20 | `GeminiClient` holds an `asyncio.Lock` across `await sleep` → every LLM call is serialised, defeating the `Semaphore(CONCURRENCY_GEMINI)`. TOCTOU race in daily-apply limit. 1+N in `/api/jobs`. |
| 07 | [Testing coverage & quality](07-testing-audit.md) | Coverage gaps, isolation, mocking discipline | 23 | Tests share the **production SQLite file**. `captcha_handler.py` (364 LOC, hot path) has **zero tests**. `POST /applications/{match_id}/apply` — the main endpoint — has no HTTP test. No coverage tooling. |
| 08 | [Observability & ops](08-observability-ops.md) | Structured logging, metrics, tracing, health, deploy readiness | 23 | Zero metrics, zero error tracking, zero tracing. `data/logs/` is created but never written to. `JOBPILOT_LOG_LEVEL` is defined but never read. No Dockerfile / Procfile / compose. **Not deployable beyond 127.0.0.1.** |

---

## The 12 most important issues across all reports

Ordered by combination of **blast radius** × **effort to fix**.

| # | ID | Title | Why it matters | Report | Effort |
|---|---|---|---|---|---|
| 1 | **DB-01** | Add indexes on `JobMatch.job_id`, `Application.job_match_id`, `created_at`, etc. | Every list view is full-scan. One PR, no behaviour change. | 02 | S |
| 2 | **LLM-02** | Inject `Embedder`/`FitEngine` in lifespan (one line in `main.py:144`) | Unlocks the entire CV-skip path (`pipeline.py:182-187`) — 30-50% fewer LLM calls per batch | 01 | XS |
| 3 | **PC-01** | `GeminiClient._wait_for_rate_limit` holds lock across `await sleep` | Two-line fix unblocks the `Semaphore(CONCURRENCY_GEMINI)` that's already set up | 06 | XS |
| 4 | **FE-01 / API-WS** | Unify WS vocabulary across backend broadcaster, `ws_models.py`, and `StatusBar.svelte` | Most live-update UI is dead code today; pick one vocabulary, delete the others | 04 + 05 | S |
| 5 | **DB-02** | Re-baseline Alembic from current models, then bypass `create_all` | Migrations are unrunnable. Without this, no PG path, no schema-change discipline. | 02 | M |
| 6 | **TS-01** | Tests share `data/jobpilot.db` with production | Autouse fixture redirecting `jobpilot_data_dir` to a tmp path. Single fixture, big confidence gain. | 07 | S |
| 7 | **API-RM** | Add `response_model=` to the 18 routes missing it | Unlocks frontend codegen (kills the hand-rolled `apiFetch<T>` ceremony in FE-02) | 05 | M |
| 8 | **LLM-01** | Reorder prompts so invariant block (CV + rules) is prefix | Unlocks Gemini's **free implicit caching** with no API changes | 01 | S |
| 9 | **DB-06 / PC-04** | Daily-apply-limit race — replace read-then-write with `SELECT FOR UPDATE` or atomic UPSERT counter | User-facing correctness bug: limits silently bypassed under concurrency | 02 + 06 | S |
| 10 | **PC-02** | `asyncio.gather` the fit-assessment loop in `morning_batch.py:328` | Batch wall-time drops proportionally to `CONCURRENCY_GEMINI` (after PC-01 is fixed) | 06 | S |
| 11 | **OBS-01..03** | Structured JSON logs + `/health` actually pings DB + `JOBPILOT_LOG_LEVEL` honored | Three small fixes; required before any non-local deploy | 08 | S |
| 12 | **TS-02 + TS-17** | Add tests for `captcha_handler.py` and `POST /apply` | Highest-blast-radius hot paths with no tests today | 07 | M |

---

## Cross-cutting themes

**1. "Wired but unused" pattern.** Three independent reports flagged scaffolding that exists but is never reached:
- `Embedder`/`FitEngine` defined but not injected (LLM-02)
- `AsyncIOScheduler` imported but `.start()` never called (cf. standards DC-01 + Gmail design Phase 1)
- `ws_models.py` discriminated union defined but never used by the broadcaster (FE-01 + API)
- `conftest.py` `mock_gemini` / `test_settings` fixtures never imported (TS-04)
- `data/logs/` directory created but no handler writes there (OBS-01)
- `JOBPILOT_LOG_LEVEL` defined but never read (OBS-02)

Pattern: features were started, broadcast in code structure, but never landed. Each is a one-line activation away from working — or a one-paragraph deletion away from being honest.

**2. Vocabulary drift between layers.** WebSocket message types, application statuses, and `dedup_hash` formulas all have **different vocabularies in different modules**. The status drift is a real bug (`engine.py` writes `"manual"` which the queue API filter rejects, DB-07). The dedup-hash drift means whitespace-divergent duplicates pass the UNIQUE.

**3. Concurrency built but defeated.** A `Semaphore(CONCURRENCY_GEMINI)` and per-job `asyncio.Event` registries exist (good design). But: lock-across-sleep (PC-01), in-memory state lost on restart (OBS-12), TOCTOU on daily limit (PC-04, DB-06), and serial `await`s where `gather` was clearly intended (PC-02). Every concurrency primitive in the codebase is undermined by one bug.

**4. Schema is in code, not in the DB.** Six different modules write `ALTER TABLE` at startup; six others assume those columns exist. The DB has no foreign keys, no indexes, no enums, no relationships. Whatever invariants the code believes hold are not enforced by Postgres/SQLite.

**5. Test suite is a status display, not a safety net.** Tests share the prod DB (TS-01), skip silently when binaries are missing (TS-05), swallow 500s with disjunctive asserts (TS-06), and have no coverage tooling. The hottest paths (`captcha_handler`, `POST /apply`) are untested. The well-written tests (`test_sanitizer`, `test_fit_engine`) prove the team knows how — it just hasn't been applied to the critical surface.

**6. Not deployable.** Beyond the standards backlog's ship blockers, the observability/ops audit identifies the unfixable-without-rework items: writing `.env` at startup (breaks read-only FS), in-memory apply event state (lost on restart), no graceful shutdown of Playwright contexts. These are launch-blockers for anything beyond 127.0.0.1.

---

## Suggested attack order

If you have **one afternoon**: do items #1, #2, #3 from the top-12. All are XS/S, all unblock measurable wins.

If you have **one week**: top-12 in order — they're already prioritised by ROI. End-of-week deliverable is "indexed DB, working concurrency, ~30-50% fewer LLM tokens, response models on every route".

If you have **one sprint**: top-12 + report-08 minimum-ops checklist + adopt the Gmail-integration Phase 1 (read-only sync + manual link) as a stretch.

If you have **one quarter**: tackle each report's full finding list, then ship Gmail Phase 2 (auto-classify) and revisit this audit.

---

## Coordinating with the standards backlog

This audit deliberately does **not** repeat items from [`../2026-05-22-standards/INDEX.md`](../2026-05-22-standards/INDEX.md). Cross-references where overlap was unavoidable:

| Audit ID | Standards-backlog overlap | Resolution |
|---|---|---|
| PC-12 (orphaned `create_task`) | EH-08 (fire-and-forget retention) | This audit lists every site; backlog fixes the pattern. Do EH-08 first, then sweep PC-12 sites. |
| DB-time / OBS-tz | ST-05 (`utcnow` migration) | Coordinate: add tz-aware `TimestampMixin` once. |
| API-error-envelope | EH-05 (HTTP exception leak) | API design needs a single error envelope; EH-05 says don't leak raw text. Solve together. |
| OBS-credential-key-runtime-write | ST-09 (import-side-effects) + ST-02 (SecretStr) | Same root cause — fix all three in one PR. |
| FE-WS / API-WS | (none — frontend not in standards backlog) | This audit owns. |

---

## Reports are self-contained

Each report follows the same structure:
- **TL;DR** — biggest issues in 3-5 bullets
- **Findings table** — ID | Title | Severity | File:line | Suggested change
- **Per-finding detail** — problem, evidence, fix
- **"Already good"** — what to preserve

If you want to brief a teammate on one slice, send them the single relevant report — they don't need the rest.
