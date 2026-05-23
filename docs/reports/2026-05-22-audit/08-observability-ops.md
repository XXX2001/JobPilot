# 08 — Observability & Operational Readiness Audit

**Scope:** JobPilot backend (FastAPI), batch scheduler, scrapers, applier engine, WS layer.
**Date:** 2026-05-22
**Auditor focus:** what is *missing* for production operation — metrics, structured logging, tracing, health/readiness, deploy surface, audit log. Logging *style* (lazy `%`-args, `getLogger`) is already covered in `../2026-05-22-standards/`; error-handling gaps are in `error-handling/`.

---

## TL;DR

**Ops-readiness verdict: NOT ready for unattended / multi-user production.** The project is a single-user, locally-launched desktop-style FastAPI app (`start.py` opens a webbrowser, binds 127.0.0.1:8000). For that use case, the bare-minimum logging is acceptable. For *any* shared or remote deployment — even a single VPS — five categories are entirely missing, and the launcher and `.env.example` actively hide that they're missing.

**Top 3 gaps:**

1. **No persistent log destination, no rotation, no structured format.** Logs go only to stdout via uvicorn's default handler. The `data/logs/` dir is *created* on startup (`backend/main.py:81`, `start.py:112`) but nothing ever writes to it — pure dead code. After a crash there is no recoverable forensic trail. `JOBPILOT_LOG_LEVEL` env var is defined (`backend/config.py:51`) but never read anywhere; the level is hard-coded to `"info"` in `start.py:134`.
2. **Zero metrics, zero tracing, zero error tracking.** No Prometheus / OpenTelemetry / Sentry / StatsD. The morning batch has 20+ minute multi-stage pipelines (scrape → match → fit-assess → LLM CV tailoring at Gemini's 15 RPM) and there is *no* duration histogram, no LLM-token counter, no Adzuna-call counter, no per-stage timing — making it impossible to diagnose "why was the batch slow today" or to alert before Gemini quota hits.
3. **No deployment surface and no audit log.** No Dockerfile, no compose, no Procfile, no systemd unit, no `fly.toml`/`render.yaml` anywhere in the tree. There is no `BatchRun` table — every morning-batch invocation vanishes after the WebSocket broadcast finishes (`broadcast_status` only pushes to live sockets). There is also no immutable record of user-facing actions (applied / skipped / CV regenerated) beyond the `applications` row itself.

---

## Findings table

| ID    | Title                                                                  | Severity | Location                                            |
|-------|------------------------------------------------------------------------|----------|-----------------------------------------------------|
| OBS-01 | No structured (JSON) logging — plaintext only, not aggregator-ready    | High     | `backend/main.py:50` (only `getLogger`, no config)  |
| OBS-02 | `JOBPILOT_LOG_LEVEL` env var defined but never applied                 | Medium   | `backend/config.py:51`, `start.py:134`              |
| OBS-03 | `data/logs/` dir created but no `FileHandler` / rotation ever wired    | Medium   | `backend/main.py:81`, `start.py:112`                |
| OBS-04 | No request-correlation / trace-ID middleware                            | High     | MISSING (no `@app.middleware` except CORS)          |
| OBS-05 | No `/ready` or DB-ping readiness probe; `/api/health` is shallow       | Medium   | `backend/main.py:248-271`                           |
| OBS-06 | No metrics endpoint or Prometheus / OTel instrumentation               | High     | MISSING                                             |
| OBS-07 | No error-tracking (Sentry / Rollbar / Honeybadger) integration         | High     | MISSING                                             |
| OBS-08 | No tracing around LLM / scraper / DB calls                              | High     | MISSING                                             |
| OBS-09 | No persisted batch-run history (`BatchRun` table absent)                | High     | `backend/scheduler/morning_batch.py:221-247`        |
| OBS-10 | No LLM-token / cost accounting per call                                 | High     | `backend/llm/gemini_client.py` (entire file)        |
| OBS-11 | No Adzuna API-call counter; no quota visibility                         | High     | `backend/scraping/adzuna_client.py`                 |
| OBS-12 | WebSocket: no connection-count / disconnect-reason logs                | Medium   | `backend/api/ws.py:98,142,172,189` (silent drops)    |
| OBS-13 | No audit log of user-facing actions (apply / skip / CV-regen)          | High     | MISSING (only `Application` row, no event-level)   |
| OBS-14 | No Dockerfile, no compose, no Procfile, no systemd unit, no Render/Fly | Critical | MISSING                                             |
| OBS-15 | No graceful-shutdown logic — Playwright contexts may leak on SIGTERM   | High     | `backend/main.py:209-211` (only logs a message)      |
| OBS-16 | No DB backup / snapshot story; SQLite WAL not checkpointed at shutdown | Medium   | MISSING (`backend/database.py`)                     |
| OBS-17 | No rate limiting on inbound HTTP endpoints                              | High     | MISSING (no slowapi / limits middleware)            |
| OBS-18 | CORS `allow_origins=["*"]` + `allow_credentials=True` (browser-rejected combo, but a deploy hazard) | High | `backend/main.py:217-223` |
| OBS-19 | `.env.example` missing several settings that exist in `Settings`        | Low      | `.env.example` vs `backend/config.py`               |
| OBS-20 | No `print()` leaks in backend code (good) but `start.py` uses `print()` for diagnostics that won't reach a log file | Low | `start.py:27,50,97,121` |
| OBS-21 | `CREDENTIAL_KEY` auto-written into `.env` at runtime — unsafe under any read-only filesystem deploy (Docker / Render) | High | `backend/config.py:69-85` |
| OBS-22 | No log content sanitisation; CV path, job IDs, agent results truncated to 500 chars get echoed | Medium | `backend/applier/auto_apply.py:368` |
| OBS-23 | `signal_confirm` / `signal_cancel` rely on in-memory `asyncio.Event` — no recovery after process restart, no record of timeouts in DB | High | `backend/applier/engine.py:106-118`, `auto_apply.py:435-447` |

---

## Per-finding details

### OBS-01 — No structured logging (High)
- `backend/main.py:50` sets `logger = logging.getLogger("jobpilot")` but never installs a `Formatter`, never configures a handler. Uvicorn's default formatter prints plain text like `INFO:     Morning batch started`. Without `python-json-logger` or `structlog`, lines cannot be ingested cleanly by Loki / CloudWatch / ELK / Datadog without per-line regex parsing.
- Fix: add a small `backend/logging_config.py` that calls `logging.config.dictConfig` with a JSON formatter (one library: `python-json-logger`), wire it from `backend/main.py` before any logger is used, and pass `log_config=None` to uvicorn in `start.py`.

### OBS-02 — `JOBPILOT_LOG_LEVEL` never applied (Medium)
- `Settings.jobpilot_log_level` is read from env but no code calls `logging.getLogger().setLevel(...)`. `start.py:134` hard-codes `log_level="info"` for uvicorn. Operators flipping the env var will see no behaviour change.
- Fix: in the new logging config, read `settings.jobpilot_log_level` and apply to the root logger and the `"jobpilot"` named logger; drop the literal `"info"` from `start.py`.

### OBS-03 — `data/logs/` is dead infrastructure (Medium)
- Both `backend/main.py:81` and `start.py:112` `mkdir(..., exist_ok=True)` a `data/logs/` path that contains only a `.gitkeep`. No `FileHandler` or `RotatingFileHandler` is ever attached. After a crash there is no on-disk forensic trail.
- Fix: add a `TimedRotatingFileHandler(filename=DATA_DIR / "logs" / "jobpilot.log", when="midnight", backupCount=14)` plus the JSON formatter from OBS-01.

### OBS-04 — No request-correlation middleware (High)
- Only `CORSMiddleware` is registered (`backend/main.py:217`). No `X-Request-ID` propagation, no `contextvars.ContextVar` injection, no trace-ID on async LLM/scraping spawned tasks. Concurrent `Morning batch started` and 3-way parallel CV generation (`asyncio.gather` at `morning_batch.py:442`) cannot be disambiguated from logs.
- Fix: `Starlette`-style middleware that reads or generates `X-Request-ID`, stores it on a `ContextVar`, and use a `logging.Filter` to inject it into every record.

### OBS-05 — Health endpoint is shallow (Medium)
- `/api/health` (`backend/main.py:248-271`) returns `db: "connected"` as a **literal string** — it never executes a query. It checks `tectonic` binary and `gemini_key_set` (just a non-empty check, not a real ping). There is no `/ready` to gate traffic during startup, and no check for `data/` writability, no check that the scraper session-manager initialised.
- Fix: split into `/api/health` (process-alive, cheap) and `/api/ready` (does `SELECT 1` against SQLite, verifies `app.state.gemini` exists, verifies `data/` is writable).

### OBS-06 — Zero metrics (High)
- No Prometheus exporter, no `/metrics` endpoint, no OpenTelemetry Meter, no StatsD client. The most operationally critical counters are entirely absent: jobs scraped per source, applications submitted per status, Gemini RPM in-flight, fit-engine cache hit/miss, batch duration histogram.
- Fix: `prometheus-fastapi-instrumentator` for built-in HTTP metrics + a hand-rolled `metrics.py` module with `Counter("jobpilot_jobs_scraped_total", labelnames=["source"])`, `Histogram("jobpilot_batch_duration_seconds")`, `Counter("jobpilot_gemini_tokens_total", labelnames=["model"])` — wire from `gemini_client.py`, `orchestrator.py`, `morning_batch.py`.

### OBS-07 — No error tracking (High)
- Unhandled exceptions are caught by `_generic_error_handler` (`backend/main.py:327-340`) and logged with `exc_info=True`, but they never leave the process. No Sentry / Rollbar SDK is installed (`pyproject.toml` lists no error-tracking dep). For a multi-user deploy this is a black hole.
- Fix: add `sentry-sdk[fastapi]`, init in `main.py` lifespan, set `traces_sample_rate=0.0` initially, plus `before_send` hook that strips PII (job description, CV content, credentials) from breadcrumbs.

### OBS-08 — No tracing (High)
- LLM calls go through `GeminiClient.generate_text` (`backend/llm/gemini_client.py:146`) which sleeps for rate-limit windows, fall-backs across model candidates, retries on 429 — none of this is observable beyond an INFO log per sleep. CV tailoring fan-out via `asyncio.gather` inside `morning_batch.py:442` has no span context. Diagnosing "why did batch X take 18 minutes" requires hand-correlating timestamps.
- Fix: `opentelemetry-instrumentation-fastapi` + `opentelemetry-instrumentation-sqlalchemy` + manual spans around `_wait_for_rate_limit`, the executor call, and the `generate_tailored_cv` path. Export to OTLP or to console-exporter for local debug.

### OBS-09 — No batch-run history (High)
- `MorningBatchRunner.run_batch` (`backend/scheduler/morning_batch.py:221-247`) keeps state in two attributes: `self.running: bool` and `self.last_status: dict | None`. The moment the process restarts, all evidence of yesterday's runs is gone. WebSocket clients only see live progress; if no client is connected, no record remains.
- The `Application` and `ApplicationEvent` tables capture per-job outcomes but not the batch *run* — duration, raw-jobs-scraped, jobs-discarded-below-threshold, CV-generation failures, total Gemini calls, total Adzuna calls.
- Fix: add `BatchRun(id, started_at, finished_at, status, raw_jobs_count, matches_count, cvs_generated, error_text)` table + commit one row per `run_batch` invocation. Expose `GET /api/queue/runs` for a history view.

### OBS-10 — No LLM-token / cost accounting (High)
- `backend/llm/gemini_client.py` calls `self._client.models.generate_content(...)` and returns only `response.text`. The Gemini SDK exposes `response.usage_metadata` (`prompt_token_count`, `candidates_token_count`, `total_token_count`) — *none* of which is read. There is no way to:
  - tell which user / which job consumed which tokens,
  - estimate cost ahead of a Gemini quota change,
  - detect a runaway prompt loop.
- Fix: in `generate_text` and `generate_json`, read `response.usage_metadata`, increment a `prometheus_client.Counter` labelled by `(model, route)`, log at DEBUG only (token counts can be log-spammy at scale).

### OBS-11 — No Adzuna call counter / quota visibility (High)
- `AdzunaClient` does an `httpx` call per search; there is no counter and no per-day quota log. Adzuna free tier is 1000 calls/month — a single misconfigured batch can burn through it silently.
- Fix: counter `adzuna_api_calls_total`, log a WARNING when >50% of monthly budget consumed (configurable threshold).

### OBS-12 — WebSocket observability is silent (Medium)
- `ConnectionManager.connect` (`backend/api/ws.py:83-96`) and `disconnect` (`:98`) log nothing. Failures in `broadcast()` / `send_to()` (`:120,141`) silently `disconnect` the client. The `websocket_endpoint` loop swallows arbitrary exceptions on `:172,187,189` with `except Exception: pass` and `except Exception: continue`. This explicit silence makes it impossible to diagnose "why doesn't the user see batch progress today".
- Fix: log connect / disconnect / send-failure at INFO; emit a metric for `ws_active_connections` (gauge), `ws_messages_sent_total` (counter), `ws_disconnect_reason_total{reason}` (counter).

### OBS-13 — No audit log of user actions (High)
- The `Application` table stores final state; `ApplicationEvent` exists but is only used for known stages (`submitted`, `interview`, `offer`, `rejected`, `captcha_detected`). User-facing actions like "user clicked skip", "user regenerated CV", "user changed search keywords", "user uploaded a new base CV" are not recorded anywhere. There is no immutable, time-ordered timeline a support engineer can reconstruct.
- Fix: add a generic `AuditLog(id, created_at, actor, action, target_type, target_id, payload_json)` table; emit one row from each mutating endpoint in `backend/api/*.py`.

### OBS-14 — No deployment artifacts (Critical for any deploy beyond `python start.py`)
- Repo-wide search confirms zero of: `Dockerfile`, `docker-compose*`, `Procfile`, `*.service`, `fly.toml`, `render.yaml`. The only entry point is `start.py` which assumes a fully-installed dev environment (Tectonic binary checked-for, `frontend/build/` expected to exist). The current launcher kills whatever is on port 8000 (`start.py:39-97`) — fine for desktop, dangerous on a shared host.
- Fix (minimum viable): a Dockerfile that installs Python 3.12, uv, Tectonic, Chromium for Playwright, and runs `uvicorn backend.main:app --host 0.0.0.0 --port 8000` with `--workers 1` (singletons on `app.state` won't survive multi-worker — call this out in the Dockerfile comment).

### OBS-15 — No graceful shutdown (High)
- `backend/main.py:209-211` shutdown branch only logs `"Shutting down JobPilot application"` and adds the line *"No scheduler to shut down — batch runs are on-demand only"*. But:
  - Active `MorningBatchRunner.running` runs are *not* cancelled.
  - `BrowserSessionManager` and Playwright contexts/agents started inside `auto_apply.py` are not explicitly closed on shutdown.
  - In-flight `asyncio.gather` over CV generation will be cancelled with `CancelledError` and any partially-written `.tex`/`.pdf` files left orphaned.
- Fix: in the lifespan teardown, await `app.state.session_manager.shutdown()` (a method that closes every Playwright instance), set a cancel flag on `batch_runner`, await its completion, and finally `db.engine.dispose()`.

### OBS-16 — No DB backup / WAL checkpoint (Medium)
- SQLite WAL files (`data/jobpilot.db-wal`, `data/jobpilot.db-shm`) are present and 571 KB. No `PRAGMA wal_checkpoint(TRUNCATE)` runs at shutdown. No backup script under `scripts/`. Alembic migrations exist (`alembic/versions/`) but there's no documented restore procedure.
- Fix: shutdown-time WAL checkpoint + a `scripts/backup_db.py` (uses `sqlite3.connect(...).backup()`) + a section in README on restore.

### OBS-17 — No HTTP rate limiting (High)
- No `slowapi` / `fastapi-limiter` / `limits` library installed. Every endpoint is unbounded. For a 127.0.0.1 deploy this is fine; for any internet-exposed deploy this is a denial-of-wallet vector against Gemini (an attacker pounding `POST /api/queue/refresh` would burn the Gemini quota in seconds).
- Fix: add `slowapi`, decorate at minimum `POST /api/queue/refresh`, `POST /api/applications/*/apply`, `POST /api/documents/regenerate` with conservative limits (e.g. 5/min per IP).

### OBS-18 — CORS configuration is browser-rejected and prod-unsafe (High)
- `backend/main.py:217-223` sets `allow_origins=["*"]` AND `allow_credentials=True`. Browsers reject this combination (the `Access-Control-Allow-Origin` header cannot be `*` when credentials are sent), so the credentials clause is currently a no-op. But the moment someone fixes it to a specific origin they will widen the attack surface dramatically. Document the intended deployment model first.
- Fix: read allowed origins from `settings.JOBPILOT_CORS_ORIGINS` (new env var), default to `["http://127.0.0.1:8000", "http://localhost:8000"]`, only set `allow_credentials=True` when origins is a concrete list.

### OBS-19 — `.env.example` is incomplete (Low)
- Missing from `.env.example` but accepted by `Settings`: `SERPAPI_KEY` (line 45 of config). No comment indicating which keys are safe to commit publicly. No mention of `JOBPILOT_CORS_ORIGINS` (which doesn't exist yet but should — see OBS-18).
- Fix: refresh `.env.example` to mirror `Settings` exactly, with comments marking secrets.

### OBS-20 — `print()` in `start.py` (Low)
- `start.py` uses `print(...)` for startup diagnostics (lines 27, 50, 97, 121). These don't reach any logger and are invisible under systemd/docker if stdout is captured by JournalD with a different formatter than the JSON logger we'd install. Acceptable for now but flag for migration.

### OBS-21 — `CREDENTIAL_KEY` runtime-write to `.env` (High, deploy blocker)
- `backend/config.py:69-85` auto-generates a Fernet key and **writes it back into `.env`** the first time the app boots without one. Under any read-only-filesystem deploy (Docker without a writable mount, Render, Fly default) this will:
  - either silently fail (file write rejected) and the key won't persist across restarts → every restart loses all stored site credentials,
  - or succeed but mutate the image / unmounted filesystem in a way that's lost on next container respawn.
- Fix: move the key to a dedicated location (`DATA_DIR / ".credential_key"`) and require it to be set explicitly via env in non-dev environments. Add a `JOBPILOT_ALLOW_KEY_BOOTSTRAP` flag (default false) — only the local launcher sets it true.

### OBS-22 — Log content includes potentially sensitive fragments (Medium)
- `backend/applier/auto_apply.py:368` logs `(raw or "")[:500]` of the browser-use Agent result — which can contain form-field values the user just typed (cover-letter text, salary expectations).
- `backend/scraping/scrapling_fetcher.py:397` logs prompt length only (good), but other prompts may be DEBUG-logged elsewhere — audit before enabling DEBUG in any shared deploy.
- Credential decryption errors (`backend/scraping/session_manager.py:333,348`) correctly log only `type(exc).__name__`, not the exception message — good pattern, propagate it.
- Fix: a `safe_log` helper in `backend/security/` that redacts emails / API-key-looking strings before logging, and use it for any payload-derived content.

### OBS-23 — In-process apply-confirmation state (High)
- `ApplicationEngine.signal_confirm` / `signal_cancel` (`backend/applier/engine.py:109-118`) flip an in-memory `asyncio.Event`. If the process restarts while a user has a pending "review-and-confirm" prompt open in the browser, the WebSocket reconnects but the Event has been recreated — the user's click will hang forever (silently timing out at `auto_apply.py:435`).
- Fix: persist pending-confirmation state to a `PendingApply(application_id, started_at, expires_at)` table; on startup, fail-over any rows older than the timeout to `status='error'` and emit a WS event so the UI can repaint.

---

## Minimum ops checklist before any non-local deploy

These are the concrete items that *must* be in place before exposing JobPilot beyond `127.0.0.1`. Tick order is from least-to-most invasive.

- [ ] **Logging**
  - [ ] Install `python-json-logger`; add `backend/logging_config.py` with `dictConfig`.
  - [ ] Wire `JOBPILOT_LOG_LEVEL` (OBS-02).
  - [ ] Attach a `TimedRotatingFileHandler` to `data/logs/jobpilot.log` (OBS-03).
  - [ ] Add `X-Request-ID` middleware + `ContextVar` filter (OBS-04).

- [ ] **Health / readiness**
  - [ ] Split `/api/health` (cheap liveness) and `/api/ready` (DB ping, gemini-key, writable `data/`) (OBS-05).

- [ ] **Metrics**
  - [ ] Install `prometheus-fastapi-instrumentator`; expose `/metrics`.
  - [ ] Hand-roll counters for: jobs scraped (by source), applications by status, Gemini calls (by model), Adzuna calls, batch duration histogram (OBS-06, OBS-10, OBS-11).

- [ ] **Error tracking**
  - [ ] Install `sentry-sdk[fastapi]`; init in lifespan with `before_send` PII scrubber (OBS-07).

- [ ] **Batch-run history**
  - [ ] Add `BatchRun` table + alembic migration; commit one row per `run_batch` (OBS-09).
  - [ ] Expose `GET /api/queue/runs?limit=20` for a history view.

- [ ] **Audit log**
  - [ ] Add `AuditLog` table; emit from every mutating endpoint (OBS-13).

- [ ] **Deploy artifacts**
  - [ ] Dockerfile that installs Tectonic, Playwright/Chromium, frontend build.
  - [ ] `docker-compose.yml` with a `volumes: ./data:/app/data` mount for the SQLite DB.
  - [ ] Document `--workers 1` constraint (singletons on `app.state` won't survive multi-worker) (OBS-14).

- [ ] **Shutdown / data safety**
  - [ ] Graceful shutdown that cancels in-flight batches, closes Playwright, dispose-s SQLAlchemy engine (OBS-15).
  - [ ] WAL checkpoint at shutdown + `scripts/backup_db.py` (OBS-16).

- [ ] **Inbound protection**
  - [ ] `slowapi` on `/api/queue/refresh`, `/api/applications/*/apply`, `/api/documents/regenerate` (OBS-17).
  - [ ] Tighten CORS, configurable origins (OBS-18).

- [ ] **Secrets**
  - [ ] Remove runtime `.env` mutation; require `CREDENTIAL_KEY` to be explicitly set in non-dev (OBS-21).
  - [ ] Refresh `.env.example` to mirror `Settings` (OBS-19).

- [ ] **Apply-pipeline resilience**
  - [ ] Persist pending-confirmation state to DB; fail-over on restart (OBS-23).

---

## Already good

These observations are the *positives* — patterns the project already has right and that should be preserved during the refactor above.

- **Logger module hygiene.** Every module uses `logger = logging.getLogger(__name__)` (verified across 30+ files) and lazy `%`-arg formatting. This makes module-level filtering trivial once a real config is in place. (Already covered in `../2026-05-22-standards/`.)
- **No stray `print()` calls in `backend/`.** The one match in `backend/utils/browser_path.py:60` is inside an `f""`-string passed to a Playwright subprocess — not a real `print`.
- **Exception-handlers chain.** `backend/main.py:274-340` correctly differentiates `LaTeXCompilationError` (422), `GeminiJSONError` (500), `GeminiRateLimitError` (429), and catch-all (500) with `exc_info=True`. The 429 path is the closest thing to a quota signal the project has today.
- **Credential-decryption logs only `type(exc).__name__`** (`backend/scraping/session_manager.py:333,348`). This is the right pattern for any sensitive-data path — copy it elsewhere.
- **Reconnecting WS clients receive the last status** (`backend/api/ws.py:158-165`). Good UX touch; document it as the design intent so OBS-12 doesn't accidentally regress it.
- **WAL mode on SQLite** is enabled (the `.db-wal` and `.db-shm` files confirm it). This already gives crash-resilience; the missing piece is just checkpoint + backup (OBS-16).
- **Scraping politeness:** `orchestrator.py:309-325` already adds randomised inter-site delays (1-3s) — that's a real anti-abuse measure, not just for ops appearance.
- **Rate limiter on Gemini** (`gemini_client.py:126-136`): in-process 15 RPM sliding window with a 2-minute sleep cap. Replace with metric-emitting version (OBS-10) rather than rewrite.
- **No use of `logger.exception` without context** — every `exc_info=True` call has a meaningful message string. Good practice.
- **Lifespan rather than `@app.on_event`** — modern FastAPI idiom, sets up the right shape for OBS-15's graceful-shutdown additions.

---

*End of audit.*
