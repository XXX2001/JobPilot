# Changelog

All notable changes to JobPilot are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the project does not yet ship versioned releases, so entries are grouped by sprint with the date the merge landed on `main`.

---

## 2026-05-22 — Pre-ship hardening sprint (PRs 0–10)

**Scope.** 12 stacked PRs landed in one sprint covering type-checking, security, DB foundations, test isolation, apply-flow correctness, concurrency & LLM cost, API contract, frontend wire-up, observability, naming, and post-review cleanup.

**Outcomes.**
- **Tests:** 315 passed / 7 skipped (was 191 / 9 at sprint start)
- **Pyright (basic mode):** 40 errors / 7 warnings — all pre-existing third-party-SDK or fallback-shim artifacts; documented and triaged
- **Frontend:** 0 TS errors, 0 svelte-check errors, 1 pre-existing a11y warning at [`frontend/src/routes/settings/+page.svelte:718`](frontend/src/routes/settings/+page.svelte#L718)
- **Deploy readiness:** Dockerfile + docker-compose.yml + real `/health` (DB ping + Tectonic flag) + JSON structured logs

Full audit trail in [`docs/reports/2026-05-22-audit/POST-SPRINT-VERIFICATION.md`](docs/reports/2026-05-22-audit/POST-SPRINT-VERIFICATION.md).

### PR-0 — Enable Pyright (basic mode)

[`pyrightconfig.json`](pyrightconfig.json)
- Turn type-checking on for `backend/`. Baseline = 65 errors / 7 warnings; reduced to 40/7 by sprint end.
- Frontend already had `svelte-check` + `tsc --noEmit` enforced; backend now matches.

### PR-1 — Honesty pass: activate Embedder/FitEngine, drop dead APScheduler

[`backend/main.py`](backend/main.py), [`backend/scheduler/morning_batch.py`](backend/scheduler/morning_batch.py)
- **Activated the CV-skip path.** `Embedder` and `FitEngine` were defined but never injected. The entire gap-driven CV-modification short-circuit was dead code. One-line fix in lifespan unlocks 30–50% fewer LLM calls per batch (verified empirically in [`docs/reports/2026-05-22-audit/01-llm-token-efficiency.md`](docs/reports/2026-05-22-audit/01-llm-token-efficiency.md)).
- **Removed dead APScheduler scaffolding.** `AsyncIOScheduler` was imported but `.start()` was never called — the "morning batch" was triggered only via the API. The aspirational cron path was cut and documented in [`docs/modules/scheduler.md`](docs/modules/scheduler.md).

### PR-2 — Security hardening

[`backend/main.py`](backend/main.py), [`backend/config.py`](backend/config.py), [`backend/scraping/`](backend/scraping/), [`.env.example`](.env.example)
- **CORS lockdown.** Replaced `allow_origins=["*"]` with `JOBPILOT_ALLOWED_ORIGINS` env-driven allowlist (default = `http://localhost:5173,http://127.0.0.1:5173`).
- **SecretStr migration.** `GOOGLE_API_KEY`, `ADZUNA_APP_KEY`, and `CREDENTIAL_KEY` now use Pydantic `SecretStr` — they no longer print in `repr()` or get logged accidentally.
- **No-exception-leak middleware.** Unhandled exceptions return a sanitized `{"error": "internal_server_error"}` instead of the framework's default traceback.
- Documented `JOBPILOT_ALLOWED_ORIGINS` in [`.env.example`](.env.example), [`docker-compose.yml`](docker-compose.yml), [`README.md`](README.md).

### PR-3 — Test foundation: DB isolation + coverage tooling

[`tests/conftest.py`](tests/conftest.py), [`pyproject.toml`](pyproject.toml)
- **Tests no longer share `data/jobpilot.db` with production.** Added `autouse` fixture that redirects `jobpilot_data_dir` to `tmp_path` for every test. Two earlier sprint bugs (status drift, daily-limit race) were silently passing because the test DB was the production DB.
- **`pytest-cov` integration.** `pyproject.toml` carries `branch = true`, source = `backend`, with `pragma: no cover`, `if TYPE_CHECKING:`, `if __name__ == .__main__.:`, `raise NotImplementedError` exclusions.

### PR-4 — DB foundations: indexes, N+1 fix, daily-limit race, status drift

[`alembic/versions/41441908fc29_add_initial_indexes.py`](alembic/versions/41441908fc29_add_initial_indexes.py), [`backend/applier/daily_limit.py`](backend/applier/daily_limit.py), [`backend/api/queue.py`](backend/api/queue.py)
- **Indexes** on `JobMatch.job_id`, `Application.job_match_id`, `Application.created_at`, `JobMatch.status`. List views drop from full-scan to indexed lookup.
- **N+1 fix in `GET /api/queue`.** Joined `JobMatch + Job` in one query instead of per-row lazy load (was 21 round-trips for 20 jobs).
- **Daily-limit race fix.** Replaced read-then-write with an atomic `INSERT … ON CONFLICT DO UPDATE … RETURNING count` so two concurrent `POST /apply` calls can no longer both pass the cap. New `tests/test_daily_limit.py` includes a 50-coroutine concurrent-reservation test.
- **Status-drift fix.** `ApplicationEngine` was writing `"manual"` but `GET /api/queue` filtered for `"applied"|"assisted"`. Unified vocabulary in [`backend/applier/__init__.py`](backend/applier/__init__.py) constants.

### PR-5 — Apply-flow correctness: EH-01/02/03 + HTTP-level tests

[`backend/applier/engine.py`](backend/applier/engine.py), [`backend/api/applications.py`](backend/api/applications.py), [`tests/test_apply_http.py`](tests/test_apply_http.py)
- **EH-01** — `POST /apply` no longer races. The daily-limit reservation is now released on every exception path (was leaked in 2 of 3 failure modes).
- **EH-02** — `ApplicantInfo` validation now guards against `None` profile fields. Previously `profile.phone = None` would cause `ValidationError` mid-request.
- **EH-03** — "Remote-submitted-but-DB-write-failed" is now compensated via an explicit `ApplicationEvent(event_type="db_write_failed")` instead of silently being lost.
- **289 LOC of HTTP-level tests** (`test_apply_http.py`) — the hot path went from zero coverage to fully exercised.

### PR-6 — Concurrency + LLM cost unlock

[`backend/llm/gemini_client.py`](backend/llm/gemini_client.py), [`backend/scheduler/morning_batch.py`](backend/scheduler/morning_batch.py), [`backend/llm/prompts.py`](backend/llm/prompts.py)
- **PC-01 lock fix.** `GeminiClient._wait_for_rate_limit` held `asyncio.Lock` across `await asyncio.sleep` — serialising every LLM call and defeating the `Semaphore(CONCURRENCY_GEMINI)`. Released the lock before sleeping; concurrent fits now actually parallelise.
- **Prompt reorder for cache hits.** Moved invariant blocks (CV + rules) to the *prefix* of every Gemini prompt. Unlocks Gemini's free implicit caching with no API changes.
- **Fit-gather.** Replaced sequential `for match in matches: await assess(match)` with bounded `asyncio.gather`. Batch wall-time dropped proportionally to `CONCURRENCY_GEMINI`.
- **192 LOC of prompt tests** (`tests/test_prompts.py`) lock the cache-friendly prefix structure in place.

### PR-7a — API contract: `response_model` on every JSON route + typed WS broadcaster

[`backend/api/`](backend/api/), [`backend/api/ws_models.py`](backend/api/ws_models.py), [`backend/api/ws.py`](backend/api/ws.py)
- **18 routes gained `response_model=`**, unlocking usable OpenAPI codegen.
- **`WSMessage` discriminated union** — every WebSocket frame now validates against a single `Annotated[Union[...], Field(discriminator="type")]`. The broadcaster was previously sending raw dicts.
- **Three error envelopes collapsed into one** (`{"error": code, "detail": msg}`).
- **178 LOC of WS broadcaster tests** (`tests/test_ws_broadcaster.py`).

### PR-7b — Frontend wire-up: typed `WSMessage` union + typed `send()`

[`frontend/src/lib/types/ws.ts`](frontend/src/lib/types/ws.ts), [`frontend/src/lib/`](frontend/src/lib/)
- **`frontend/src/lib/types/ws.ts`** — discriminated TS union mirroring the backend's `WSMessage`. The websocket client's `onMessage` is now exhaustively type-checked.
- **Typed `send(msg: ClientMessage)`** — outgoing WS frames likewise checked at the call site.
- FE-side bugs documented (not fixed) — see [`docs/reports/2026-05-22-audit/04-frontend-audit.md`](docs/reports/2026-05-22-audit/04-frontend-audit.md) for the open list (CV upload doesn't post bytes; queue mode isn't persisted; etc.).

### PR-8 — Observability + deploy readiness

[`backend/main.py`](backend/main.py), [`backend/utils/logging.py`](backend/utils/logging.py), [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml)
- **JSON structured logs** with `RotatingFileHandler` at `data/logs/jobpilot.log` (10 MB × 5 backups). `JOBPILOT_LOG_LEVEL` env var is now actually honored.
- **Real `/health`** — pings the DB, reports Tectonic availability, returns `tectonic_hint` if missing.
- **Dockerfile + docker-compose.yml** — first deployable artifact. Multi-stage build; non-root user; healthcheck wired to `/api/health`.

### PR-9 — Naming sweep: `morning_batch` → `batch_runner`

[`backend/scheduler/batch_runner.py`](backend/scheduler/batch_runner.py), [`tests/test_batch_runner.py`](tests/test_batch_runner.py), [`docs/modules/scheduler.md`](docs/modules/scheduler.md)
- Renamed `morning_batch.py` → `batch_runner.py`, `MorningBatchRunner` → `BatchRunner` to reflect that the runner is triggered on-demand (not cron-scheduled).
- All tests, docs, and module map updated in lock-step.

### PR-10 — Review cleanups: 4 bugs fixed + 8 simplifications

[`backend/api/`](backend/api/), [`backend/applier/`](backend/applier/), [`backend/models/`](backend/models/)
- **Bug fix:** `PUT /api/settings/profile` silently dropped optional fields (phone, location, etc.) when creating a fresh profile row.
- **Bug fix:** `ApplicantInfo.phone: str` rejected `None` from a NULL DB column; chained `or ""` to guarantee non-None.
- **Bug fix:** `engine.py` logger used `%d` for `app.id` which is `None` under mocked DBs — the JSON formatter then raised `TypeError`, the broad `except Exception` turned it into a spurious `ApplicationRecordError`. Switched to `%s`.
- **Bug fix:** `SkillGap.criticality` was typed `str` on the frontend but `float` on the backend — broadcast contract was inconsistent. Aligned to `float`/`number`.
- **`datetime.utcnow()` migration** to `_utc_now()` helper using `datetime.now(timezone.utc).replace(tzinfo=None)` — silences the 3.12 deprecation warning while preserving naive-UTC compatibility with SQLite TIMESTAMP columns.
- **Friendly startup error.** `backend/config.py` now catches `ValidationError` at startup and prints a curated "missing env vars" list instead of a 30-line stack trace.

### Post-sprint follow-ups (not in any individual PR)

[`docs/reports/2026-05-22-audit/`](docs/reports/2026-05-22-audit/), [`docs/file-map.md`](docs/file-map.md), [`docs/architecture.md`](docs/architecture.md)
- **Documentation overhaul** — 8 deep-audit reports + post-sprint verification + standards spec + module-by-module file map.
- **Test scaffolding fixes** — `tests/test_session_manager.py` rewritten to match the dual-layout `BrowserSessionManager` API (drops stale `BrowserConfig` patches); `tests/test_scraping.py` `MockAdzuna.search()` signature extended to mirror the production kwargs. Pytest went 306/6/7 → 315/0/7.

---

## Pre-2026-05-22 (legacy history)

Earlier commits (`723a90c Deslopify` and older) predate the standards/audit sprints and are not individually documented here. Use `git log --first-parent main` for the full history.

---

## How to read this changelog

- Each entry is one merge commit on `main`. Use `git log --first-parent main` to see them in order.
- File references use `[path](relative/url)` so they're clickable in GitHub/VS Code.
- Effort tags (S/M/L) match the [audit reports](docs/reports/) convention.
- For a forward-looking improvement roadmap, see [`docs/reports/2026-05-23-improvements/INDEX.md`](docs/reports/2026-05-23-improvements/INDEX.md).
