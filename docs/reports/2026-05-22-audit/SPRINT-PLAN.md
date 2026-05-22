# JobPilot Pre-Ship Sprint Plan (2026-05-22)

Concrete grouping of the [standards backlog](../2026-05-22-standards/INDEX.md) (36 items) + the [deep-audit findings](INDEX.md) (~159 items) into **9 thematic PRs**.

## Principles
- **Each PR leaves the codebase internally consistent.** No half-migrated states between PRs.
- **Group by theme, not by report.** Same area = same PR even if findings come from different audits.
- **Order by dependency × risk × ROI.** Foundation first, polish last.
- **Gmail integration is post-ship.** It's a feature, not a fix.
- **Effort scale:** XS (< ½ day), S (½–1 day), M (1–3 days), L (3–7 days), XL (> 1 week).

---

## Execution order

### **PR 0 — Type-checking foundation** (XS, 0–1 day)
**Why first:** every subsequent PR writes better code with Pyright on.
- **TY-01** Enable Pyright (`typeCheckingMode: "basic"` or `"standard"`)
- Add stubs for third-party libs that lack them
- Add `pyright` to lint step (don't fail CI yet — collect baseline)

**Risk:** Low. Tooling-only, no behaviour change.
**Output:** type errors visible. Fix the easy ones (`TY-07` `model: str = None` implicit-Optional) inline as you encounter them in later PRs.

---

### **PR 1 — Honesty pass: remove or activate dead scaffolding** (M, 2 days)
**Why second:** highest cost/benefit cleanup. The LLM-02 line alone unlocks 30–50% fewer LLM calls per batch. Deleting dead code shrinks every subsequent PR's diff.

| ID | Action |
|---|---|
| **LLM-02** | Inject `Embedder` + `FitEngine` in `lifespan` (1 line in `backend/main.py:144`) — unlocks the CV-skip path |
| **DC-01** | Delete the `AsyncIOScheduler` scaffolding (never started). Keep the import only if Gmail Phase 1 wants it within 1 month; otherwise gone. |
| **FE-01 (part)** | Delete the dead branches in `StatusBar.svelte` (the 3 message types that backend never emits) — pick one vocabulary now, fix the rest in PR 6 |
| **RG-01** | Either implement `POST /regenerate` properly **or** delete the endpoint (it currently lies) |
| **DC-02** | Remove unused legacy `generate_diff` helper |
| **JOBPILOT_LOG_LEVEL** (OBS-02) | Honor the env var in `start.py` (1 line) — defer rest of logging to PR 8 |

**Risk:** Low–Medium. Mostly deletions. The `Embedder` injection has a small risk of changing batch behaviour — must add a smoke test that batch path runs both modes.
**Dependencies:** PR 0.

---

### **PR 2 — Security hardening** (M, 2 days, ship-blocker)
**Why now:** non-negotiable for any non-local deploy.

| ID | Action |
|---|---|
| **ST-01** | CORS: replace `allow_origins=["*"]` with concrete frontend origin list, drop `allow_credentials=True` if not needed |
| **ST-02** | Type all API secrets as `SecretStr` (`GEMINI_API_KEY`, `ADZUNA_*`, etc.) so they don't render in logs/tracebacks |
| **ST-09** | Stop writing `.env` from `config.py` at import time (breaks read-only filesystems); move to explicit init script |
| **ST-03** | Extract duplicated Fernet credential logic into one helper (same files as above) |
| **EH-05** | Stop leaking raw exception text to HTTP clients (custom handler returning `{"error": "internal_error", "ref": "<uuid>"}`) |

**Risk:** Medium. `EH-05` may hide useful errors in dev — keep verbose mode behind `DEBUG=true`.
**Dependencies:** PR 0.

---

### **PR 3 — Test foundation** (M, 2–3 days)
**Why now:** so PRs 4–8 land on a trusted suite. Currently tests share the prod DB and silently skip on missing binaries.

| ID | Action |
|---|---|
| **TS-01** | Autouse fixture redirecting `jobpilot_data_dir` to `tmp_path` (single fixture, biggest confidence gain) |
| **TS-04** | Make `conftest.py` `mock_gemini` + `test_settings` fixtures actually imported and used; delete duplicated mock setup in 50+ tests |
| **TS-05** | Drop the silent `pytest.skip` when Tectonic missing — use `pytest.importorskip` with a CI marker so CI fails if Tectonic isn't installed |
| **TS-06** | Remove `TestClient(raise_server_exceptions=False)`; replace disjunctive `in (200, 502)` asserts with exact codes |
| **+** | Add `pytest-cov`, configure coverage in `pyproject.toml`, capture baseline (don't enforce gate yet) |

**Risk:** Low. Test-only changes.
**Dependencies:** PR 0.

---

### **PR 4 — DB foundations** (L, 4–6 days, highest-risk PR)
**Why now:** unlocks correctness fixes in PR 5 and removes `create_all`-vs-Alembic drift permanently.

| ID | Action |
|---|---|
| **DB-02** | Re-baseline Alembic from current models; delete `Base.metadata.create_all` from `database.py` + the ad-hoc startup `ALTER TABLE`s |
| **DB-01** | Add `index=True` / `Index(...)` to every FK, `created_at`, `JobMatch.batch_date`, `JobMatch.status`, etc. |
| **DB-09** | Declare every `ForeignKey()` + `relationship()`; set `PRAGMA foreign_keys = ON` for SQLite |
| **DB-07** | Make `JobMatch.status` an `Enum`/`Literal`; reconcile vocabularies (`engine.py` writes `"manual"` which queue API rejects — pick one) |
| **DB-18** | Unify the three `dedup_hash` formulas; add a regression test |
| **DB-03 / DB-04 / DB-17** | Fix the three N+1s (`/api/jobs`, batch insert, `_store_matches`) using the `joinedload` / batched-fetch pattern already in `applications.py:188-204` |
| **ST-05** | Add tz-aware `TimestampMixin`; migrate `datetime.utcnow()` callers in same PR (otherwise constant rebase pain) |

**Risk:** High. Touches almost every read path. Must land behind a smoke-test suite (PR 3) and on a feature branch with a fresh DB clone for verification.
**Dependencies:** PR 0, PR 3.

---

### **PR 5 — Apply-flow correctness** (L, 3–5 days, ship-blocker)
**Why now:** the hot path. Multiple silent-failure bugs and a race condition. DB foundations from PR 4 make the limit-counter fix possible.

| ID | Action |
|---|---|
| **EH-01** | Stop swallowing DB-commit failure when recording an application (re-raise + log; transactional boundary) |
| **EH-02** | Don't report success when assisted-apply agent throws (surface the error to the WS + UI) |
| **EH-03** | Log credential/profile JSON parse failures in `POST /apply` (currently silent) |
| **EH-04** | Surface auto-login failures in `SessionManager` (currently looks like "no credentials") |
| **DB-06 / PC-04** | Fix daily-limit TOCTOU race: atomic UPSERT counter or `SELECT FOR UPDATE` (now possible thanks to PR 4 schema) |
| **API-idempotency** | Add unique constraint on `(match_id, status="pending")` + Idempotency-Key support on `POST /apply` |
| **TS-17** | Add HTTP-level test for `POST /applications/{match_id}/apply` (success + race + duplicate) |
| **TS-02** | Add tests for `applier/captcha_handler.py` (364 LOC, zero coverage today) |

**Risk:** Medium-High. Behavioural changes to the most user-visible path. Tests must land in the same PR.
**Dependencies:** PR 3, PR 4.

---

### **PR 6 — Concurrency & LLM-cost unlock** (M, 2–3 days, highest dollar ROI)
**Why now:** independent of PR 4/5. Two-line fix in PC-01 unblocks the existing concurrency machinery; prompt reorder is free caching.

| ID | Action |
|---|---|
| **PC-01** | `GeminiClient._wait_for_rate_limit`: stop holding the `asyncio.Lock` across `await asyncio.sleep`. Two-line fix that unblocks `Semaphore(CONCURRENCY_GEMINI)` |
| **LLM-01** | Reorder all prompts so the invariant prefix (CV + rules + schema) is at the **front** of every call — unlocks Gemini's free implicit context caching |
| **PC-02** | `asyncio.gather` the fit-assessment loop in `morning_batch.py:328-364` |
| **PC-08** | Single shared `httpx.AsyncClient` for Adzuna instead of per-call |
| **PC-05 / PC-06** | Punt `lxml` HTML cleaning + cosine similarity to executor |
| **EH-08 / PC-12** | Sweep fire-and-forget `create_task` sites: retain reference + done-callback (or convert to TaskGroup) |
| **LLM-03** | Route Scrapling + form-filler to Flash-Lite; reserve Pro for `CVModifier` |
| **LLM-04** | Replace free-text + regex salvage in `ScraplingFetcher` + `form_filler` with `generate_json(prompt, schema)` |
| **LLM-05** | Persist embeddings in a `(text, model)`-keyed table — stop re-embedding the same CV/job skills each batch |

**Risk:** Medium. The prompt-reorder change is invisible but must be A/B verified on a few real jobs to confirm output quality didn't shift. Concurrency changes need load-style smoke tests.
**Dependencies:** PR 1 (Embedder injection needed for LLM-05 to make sense), PR 3 (tests).

---

### **PR 7 — API contract & frontend wire-up** (L, 5–7 days, joint BE/FE)
**Why now:** the linchpin. Once the API has typed responses, the frontend can codegen and the WS protocol stops drifting. Doing FE before BE = waste.

| ID | Action |
|---|---|
| **API-RM** | Add `response_model=` to the 18 routes that lack one |
| **API-error-envelope** | One error envelope across all routes (coordinate with EH-05) |
| **API-status-codes** | `201` on create, `204` on delete, `202` on async (`POST /search`, `POST /refresh`) |
| **API-pagination** | Cap unbounded `.all()` on `/queue`, `/documents`, `/analytics/trends` |
| **API-WS-types** | Make `broadcast_status` / `broadcast_job_assessment` emit a member of `ws_models.WSMessage`; remove "ghost" types not in the union |
| **API-WS-auth** | Token check on WS connect |
| **FE-02** | Generate a typed client from OpenAPI (e.g. `openapi-typescript-codegen` or `orval`); replace hand-rolled `apiFetch<T>` |
| **FE-01 (rest)** | Wire `StatusBar.svelte` to the unified WS vocabulary; delete the dead branches not removed in PR 1 |
| **FE-03** | Centralized `ApiError` class + toast primitive; remove the 18 `catch (e: any)` sites |
| **FE-04** | Split `settings/+page.svelte` (1,115 LOC, 6 tabs) into one file per tab |
| **FE-12** | Fix the CV upload — currently stores only the filename string, never sends the bytes (**real bug**, user-visible) |
| **FE-29 / FE-30** | Fix `$derived(() => …)` returning a function instead of memoized value |

**Risk:** Medium. Frontend changes touch every route, but each is mechanical once the typed client lands.
**Dependencies:** PR 2 (EH-05 error envelope), PR 5 (apply route shape settled).

---

### **PR 8 — Observability + deploy readiness** (L, 4–6 days)
**Why last:** rest of the codebase must be stable first. Logging, metrics, and tracing reflect what the code does — wire them after the code is what it should be.

| ID | Action |
|---|---|
| **OBS-01** | Structured JSON logging (`python-json-logger`); write to `data/logs/` + stdout |
| **OBS-03** | `/health` actually pings DB; add `/ready` separately |
| **OBS-04** | Request correlation ID middleware (propagate through async + LLM calls) |
| **OBS-05** | Token-usage metric from Gemini `usage_metadata`; per-user counters in DB |
| **OBS-06** | Sentry (or equivalent) for unhandled errors |
| **OBS-07** | OpenTelemetry instrumentation around LLM + Playwright spans |
| **OBS-08** | `BatchRun` table: persist morning-batch start/finish/duration/result so it survives restart |
| **OBS-14** | Rate limiting on API endpoints (`slowapi`) + outbound (Gemini quota) |
| **OBS-deploy** | Dockerfile + compose; graceful shutdown of Playwright contexts on SIGTERM |
| **OBS-cors-prod** | Verify ST-01 against a real frontend origin |

**Risk:** Low–Medium. Mostly additive. Sentry rollout needs a careful first 24h.
**Dependencies:** PR 1–7 stable.

---

### **PR 9 — Naming sweep** (M, 1–2 days, post-ship optional)
**Why deferred:** pure rename churn — adds noise to diffs in PR 1–8 and provides no behavioural value pre-launch. Owner's #1 gripe is here, so do it the week after launch.

| ID | Action |
|---|---|
| **NM-01** | Rename `morning_batch` / `MorningBatchRunner` → neutral term (e.g. `MatchPipeline` / `match_pipeline_runner`) |
| **NM-02** | Drop `jobpilot_*` brand prefix on config fields (keep `env=` + DB names as wire contracts) |
| **NM-03** | Unify "site" vs "source" terminology (internal-only rename) |
| **NM-04** | `CONCURRENCY_GEMINI` → `GEMINI_MAX_CONCURRENCY` |
| **NM-05** | Stop calling private `ScraplingFetcher._clean_html` cross-module |

**Risk:** Very low (mechanical). Make it a single mechanical PR with a rename script committed.
**Dependencies:** All of PR 1–8 merged (so nobody else's PR collides).

---

## Deferred to post-launch (not in sprint)

- **Gmail integration** — entire [`03-gmail-integration-design.md`](03-gmail-integration-design.md), all three phases. Feature, not fix.
- **TY-02 through TY-08** — typing sweep. Do incrementally as PRs touch each file; full sweep is a winter-cleanup PR.
- **DC-04** — `__init__` docstrings.
- **ST-04**, **ST-06**, **ST-07**, **ST-08** — magic-number centralization, URL registry, import hoisting, large-function refactor. Polish.
- **PC-09**, **PC-10**, **PC-11**, **PC-13**+ — medium-impact perf items beyond the top concurrency wins.
- **DB-10 through DB-24** — Postgres-native upgrades (FTS, JSONB GIN, materialised views). Post-PG-migration project.
- **FE-13 through FE-36** — frontend polish (perf, a11y minor, etc.) once the codegen layer is in.

These together are a quarter of post-launch work and intentionally not in this sprint.

---

## Suggested calendar (single engineer, no parallelization)

| Week | PRs | Outcome |
|---|---|---|
| 1 | PR 0 + PR 1 | Pyright on, dead code gone, LLM-cost down 30–50% |
| 1 | PR 2 + PR 3 | Security hardened, test suite trustworthy |
| 2 | PR 4 | DB foundations: indexed, FK'd, Alembic re-baselined |
| 3 | PR 5 + PR 6 | Apply flow correct + concurrency unlocked |
| 4 | PR 7 | API contracts typed + FE wired |
| 5 | PR 8 | Observability + deploy-ready |
| **5 weeks total** | | **Ship.** |
| post | PR 9 | Naming sweep |

With **two engineers in parallel**, PR 1+3, PR 2+4 (sequenced), PR 6+7 can overlap → about 3 weeks.

---

## Parallelization map

```
PR 0 → PR 1 ──┬──→ PR 6 ──┐
              │            │
              ├──→ PR 3 ──┴→ PR 4 → PR 5 ──┐
              │                              │
              └──→ PR 2 ─────────────────────┴→ PR 7 → PR 8
```

- **PR 0** must land first (cheap, fast).
- **PR 1, 2, 3** can land in any order after PR 0 — independent.
- **PR 4** must land before PR 5 (apply flow needs the schema fixes).
- **PR 6** depends only on PR 1 and PR 3.
- **PR 7** depends on PR 2 (error envelope) + PR 5 (apply route final shape).
- **PR 8** is best-last so it observes the final code.

---

## Critical risks & mitigations

| Risk | Where | Mitigation |
|---|---|---|
| Alembic re-baseline destroys data | PR 4 | Branch with fresh DB clone; export-import script for dev DB; manual cutover checklist |
| LLM-cost change drops output quality | PR 6 (LLM-01, LLM-03) | A/B compare 10 real jobs' tailored CVs before/after; gate per-prompt change behind a flag |
| Frontend codegen breaks every route at once | PR 7 (FE-02) | Generate client into separate file, migrate route-by-route in same PR with the OLD client still importable until the last commit |
| Apply-flow tests miss the race window | PR 5 | Add a stress test that fires 20 concurrent applies through `httpx.AsyncClient` against the same `match_id` |
| Sentry first-day floods on real errors that were silenced before | PR 8 | Roll out at 10% sample for 24h; tune from the result |

---

## What "shipped" means at the end of PR 8

- ✅ All ship-blocker findings from the standards backlog closed
- ✅ Top-12 audit issues closed
- ✅ ~70% of remaining standards-backlog items closed (rest pushed to "post-launch polish")
- ✅ Deployable via Dockerfile, observable via JSON logs + Sentry + metrics
- ✅ Frontend has a typed client; no `any` in API call paths
- ✅ DB has indexes, FKs, and a clean Alembic head
- ✅ Test suite is isolated, exercises critical paths, and has coverage tooling
- ❌ Gmail integration — explicitly post-launch
- ❌ Naming sweep — explicitly post-launch
