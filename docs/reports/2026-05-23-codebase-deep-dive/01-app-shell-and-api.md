# 01 — App Shell & API Layer

_Scope: FastAPI application factory, lifespan, configuration, database engine, dependency injection, every HTTP router under `backend/api/`, the WebSocket layer, and the credential-encryption surface in `backend/security/`._

_Read-only review against the source on branch `gm-phase-1` (HEAD `983cc7b`). Line refs are clickable._

---

## 1. Purpose

The "app shell" is the front door of JobPilot: it wires up a FastAPI application, owns the SQLite engine, builds and parks all heavy singletons (Gemini client, scraping orchestrator, batch runner, Gmail token manager) on `app.state`, registers every HTTP and WebSocket route, schedules the recurring Gmail poller via APScheduler, and provides a small dependency-injection helper module so routers don't have to know about `request.app.state` directly. It also defines a single JSON-formatted logging pipeline and a handful of global exception handlers that translate domain errors (LaTeX / Gemini) into stable HTTP responses. Everything in this layer is process-local — there is no multi-tenant story; the product is a single-user desktop assistant that happens to speak HTTP.

---

## 2. Architecture overview

```
                ┌──────────────────────────────────────────────┐
                │            backend/main.py                   │
                │  ─ FastAPI app factory + lifespan()          │
                │  ─ CORS + SPA fallback + exception handlers  │
                └───────────┬────────────────────────┬─────────┘
                            │                        │
                  app.state │                        │ include_router()
                            ▼                        ▼
                ┌───────────────────────┐   ┌──────────────────┐
                │ Singletons (lifespan) │   │ backend/api/*.py │
                │  gemini, cv_pipeline, │   │  jobs/queue/...  │
                │  apply_engine,        │   │  ws.py / ws_models│
                │  batch_runner,        │   └────┬─────────────┘
                │  gmail_token_manager, │        │ Depends(get_db)
                │  scheduler            │        ▼
                └───────────┬───────────┘   ┌──────────────────┐
                            │               │  backend/api/    │
                            │               │     deps.py      │
                            │               │  DBSession alias │
                            │               └────┬─────────────┘
                            ▼                    ▼
                ┌─────────────────────────────────────────┐
                │           backend/database.py           │
                │  engine, AsyncSessionLocal, get_db,     │
                │  init_db, _migrate_add_columns          │
                └─────────────────┬───────────────────────┘
                                  ▼
                   sqlite+aiosqlite:///<DATA_DIR>/jobpilot.db
                              (WAL journal mode)
```

`backend/config.py` is imported eagerly at process start by everything; it both validates `.env` and **mutates** `.env` on first run to persist a freshly-minted `CREDENTIAL_KEY`. `backend/logging_config.py` is called once from `lifespan` and is idempotent. `backend/api/deps.py` is the only sanctioned bridge between routers and `app.state` — though several routers still read `request.app.state` directly, which is the main layering smell in this slice (see Critique).

---

## 3. App lifecycle

Defined in [`main.py:69-253`](backend/main.py#L69) as an `@asynccontextmanager` passed to `FastAPI(lifespan=…)`.

**Startup order:**

1. [`configure_logging(data_dir=DATA_DIR)`](backend/main.py#L74) — must come first so every subsequent log line is structured JSON.
2. [`data_dirs` creation](backend/main.py#L79) — six subdirectories under `DATA_DIR` (`cvs`, `letters`, `templates`, `browser_sessions`, `browser_profiles`, `logs`).
3. [`init_db()`](backend/main.py#L94) — create tables, run lightweight `_migrate_add_columns`, seed `job_sources` from `SITE_CONFIGS`. Wrapped in a broad `except` (see Critique).
4. Singleton instantiation block ([`main.py:100-173`](backend/main.py#L100)) — builds Gemini client, CV/letter pipelines, Adzuna client, scraping orchestrator, matcher, application engine, batch runner. Each is attached to `app.state.*`. Any exception here is caught and **demoted to a warning**, which is intentional for the test environment but quietly breaks production if a real import fails.
5. [`scan_overdue()`](backend/main.py#L179) — synchronous catch-up scan for follow-up events created while the server was down.
6. WS handler registration ([`main.py:188-220`](backend/main.py#L188)) — wires inbound `login_done`, `login_cancel`, `confirm_submit`, `cancel_apply` messages to `session_manager` / `apply_engine` callbacks.
7. [`GmailTokenManager`](backend/main.py#L223) — singleton parked at `app.state.gmail_token_manager`.
8. [`AsyncIOScheduler` boot](backend/main.py#L231) — adds `_run_gmail_poll` as an interval job (`max(1, GMAIL_POLL_INTERVAL_MINUTES)`), stored at `app.state.scheduler`.

**Attached to `app.state`:**

| Attribute | Source | Used by |
| --- | --- | --- |
| `gemini` | `GeminiClient()` | `queue.enrich_job_description` |
| `cv_pipeline` | `CVPipeline(...)` | (passed to `BatchRunner`, also reachable via `deps.get_cv_pipeline`) |
| `letter_pipeline` | `LetterPipeline(...)` | — (set but unused in this layer) |
| `adzuna`, `adaptive_scraper`, `session_manager`, `scraping_orchestrator`, `matcher`, `apply_engine`, `batch_runner` | local construction | `applications.apply_to_job`, `queue.refresh_queue`, `queue.get_batch_status`, WS handlers |
| `gmail_token_manager` | `GmailTokenManager()` | `gmail.sync_now`, `_run_gmail_poll` |
| `scheduler` | `AsyncIOScheduler` | shutdown only |

**Shutdown:** [`main.py:246-252`](backend/main.py#L246) only calls `scheduler.shutdown(wait=False)`. No graceful drain of the batch runner, no `await engine.dispose()` on the SQLAlchemy engine, no flush of any background tasks (see Critique).

---

## 4. Configuration

All settings come through Pydantic v2's `BaseSettings` in [`config.py:11-76`](backend/config.py#L11). The `.env` file is the source of truth.

**Required (no default):**

- `GOOGLE_API_KEY: SecretStr`
- `ADZUNA_APP_ID: str`
- `ADZUNA_APP_KEY: SecretStr`

**Optional secrets:** `SERPAPI_KEY`, `CREDENTIAL_KEY` (Fernet).

**App knobs:** `JOBPILOT_HOST`, `JOBPILOT_PORT`, `JOBPILOT_LOG_LEVEL`, `JOBPILOT_SCRAPER_HEADLESS`, `JOBPILOT_DATA_DIR`, `JOBPILOT_ALLOWED_ORIGINS` (CSV).

**LLM:** `GOOGLE_MODEL` (default `gemini-3-flash-preview`), `GOOGLE_MODEL_FALLBACKS` (CSV).

**Feature flags:** `SCRAPLING_ENABLED`, `APPLY_TIER1_ENABLED`.

**Gmail (Phase 1):** `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`, `GMAIL_BACKFILL_DAYS`, `GMAIL_POLL_INTERVAL_MINUTES`.

[`Settings.is_configured`](backend/config.py#L56) centralises the "is this credential set?" question and is critical because a `SecretStr("")` is **not** equal to `""` — naive comparisons would always return True.

[`_load_settings`](backend/config.py#L79) replaces Pydantic's ValidationError dump with a friendly "missing X" banner and `sys.exit(1)`, so the launcher and Docker healthcheck can detect a misconfigured `.env`.

**CREDENTIAL_KEY auto-generation:** [`config.py:115-131`](backend/config.py#L115). If `CREDENTIAL_KEY` is empty after loading, a fresh Fernet key is minted, assigned back to the settings object, and persisted by **rewriting the `.env` file** in-place (with a regex replace if `CREDENTIAL_KEY=` is already present, otherwise appended). This is the only place in the codebase that mutates `.env`.

`PROJECT_ROOT` is the parent of `backend/`. `DATA_DIR` resolves `jobpilot_data_dir` relative to `PROJECT_ROOT` if not absolute.

Static defaults that are *not* `.env`-overridable live in [`defaults.py`](backend/defaults.py) — Gemini fallback model name, content/field length caps, scheduler limits, ATS-gap thresholds. These are imported by name in many places.

---

## 5. Database layer

Defined in [`database.py`](backend/database.py).

- **Engine:** [`create_async_engine("sqlite+aiosqlite:///{jobpilot_data_dir}/jobpilot.db", echo=False)`](backend/database.py#L22). Note: `jobpilot_data_dir` is used **as-is** — if the user sets a relative path, the engine will use it relative to the CWD, not to `PROJECT_ROOT` (see Critique).
- **WAL mode:** [`set_wal_mode`](backend/database.py#L28) is wired as a `connect` event on the sync engine; runs `PRAGMA journal_mode=WAL` on every new connection. Failure is swallowed at DEBUG level.
- **Session factory:** [`AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)`](backend/database.py#L37).
- **FastAPI dependency:** [`get_db()`](backend/database.py#L67) is an async generator that yields a fresh `AsyncSession`. Note it does NOT commit or rollback — routers are expected to call `await db.commit()` themselves.
- **Standalone context manager:** [`db_session()`](backend/database.py#L51) wraps a session with commit-on-success / rollback-on-exception. Used by background workers (not by the FastAPI dependency).
- **Schema creation:** [`init_db()`](backend/database.py#L40) does `Base.metadata.create_all` then runs the two follow-ups below.
- **Lightweight migration:** [`_migrate_add_columns`](backend/database.py#L72) iterates a hard-coded list of `(table, column, type)` triples, inspects `PRAGMA table_info`, and issues `ALTER TABLE ADD COLUMN` for anything missing. Currently four entries — three on `search_settings`, one on `applications.last_correspondence_at`. Errors are swallowed at DEBUG.
- **Seeder:** [`_seed_default_sources`](backend/database.py#L97) populates `job_sources` from `SITE_CONFIGS` on a fresh DB.

There is no Alembic, no schema version table, no down-migrations.

---

## 6. Dependency injection

[`backend/api/deps.py`](backend/api/deps.py) is intentionally tiny.

- **`DBSession` alias** — [`deps.py:11`](backend/api/deps.py#L11): `Annotated[AsyncSession, Depends(get_db)]`. Every router function declares `db: DBSession`.
- **Singleton getters** — [`get_session_manager`](backend/api/deps.py#L25), [`get_apply_engine`](backend/api/deps.py#L29), [`get_cv_pipeline`](backend/api/deps.py#L33), [`get_scraping_orchestrator`](backend/api/deps.py#L37), [`get_batch_runner`](backend/api/deps.py#L41). Each just reads `request.app.state.<x>`.

**However**, several routers bypass these getters and pull from `request.app.state` directly:

- [`applications.apply_to_job`](backend/api/applications.py#L470) reads `request.app.state.apply_engine`.
- [`queue.get_batch_status`](backend/api/queue.py#L123) reads `request.app.state.batch_runner`.
- [`queue.refresh_queue`](backend/api/queue.py#L140) reads `request.app.state.batch_runner`.
- [`queue.enrich_job_description`](backend/api/queue.py#L242) reads `request.app.state.gemini`.
- [`gmail.sync_now`](backend/api/gmail.py#L56) reads `request.app.state.gmail_token_manager`.
- [`ws.websocket_endpoint`](backend/api/ws.py#L114) reads `websocket.app.state.batch_runner`.

The wrappers exist but are unused. This is a coverage gap — see Critique CRIT-2.

---

## 7. API surface — endpoint catalogue

All paths are mounted under `/api`. `redirect_slashes=False` is set on most routers; the WS router has no prefix.

### `backend.api.analytics` — [`analytics.py`](backend/api/analytics.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/analytics/summary` | `get_analytics_summary` | [analytics.py:46](backend/api/analytics.py#L46) | `AnalyticsSummary` (total, week, response rate, avg match) |
| GET | `/api/analytics/trends?days=N` | `get_analytics_trends` | [analytics.py:89](backend/api/analytics.py#L89) | `AnalyticsTrends` (daily counts, zero-filled) |

### `backend.api.applications` — [`applications.py`](backend/api/applications.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| POST | `/api/applications` | `create_application` | [applications.py:125](backend/api/applications.py#L125) | `ApplicationOut`, 201 |
| GET | `/api/applications` | `list_applications` | [applications.py:141](backend/api/applications.py#L141) | `ApplicationListOut` |
| GET | `/api/applications/limit-status` | `get_limit_status` | [applications.py:235](backend/api/applications.py#L235) | `LimitStatusOut` |
| GET | `/api/applications/{application_id}` | `get_application` | [applications.py:271](backend/api/applications.py#L271) | `ApplicationOut`, 404 |
| PATCH | `/api/applications/{application_id}` | `update_application` | [applications.py:306](backend/api/applications.py#L306) | `ApplicationOut`, 404 |
| POST | `/api/applications/{application_id}/events` | `add_application_event` | [applications.py:359](backend/api/applications.py#L359) | `ApplicationEventOut`, 201/404 |
| POST | `/api/applications/{match_id}/apply` | `apply_to_job` | [applications.py:454](backend/api/applications.py#L454) | `ApplicationResult`, 503/422 |

### `backend.api.applications_export` — [`applications_export.py`](backend/api/applications_export.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/applications/export?format=csv` | `export_applications` | [applications_export.py:70](backend/api/applications_export.py#L70) | `StreamingResponse` (`text/csv`), 400 on bad format |

Mounted **before** `applications.py` in [`main.py:287`](backend/main.py#L287) so `/export` is not shadowed by `/{id}`.

### `backend.api.correspondence` — [`correspondence.py`](backend/api/correspondence.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/correspondence/unlinked` | `list_unlinked` | [correspondence.py:65](backend/api/correspondence.py#L65) | `UnlinkedListOut` (≤200) |
| GET | `/api/correspondence/{application_id}` | `list_for_application` | [correspondence.py:81](backend/api/correspondence.py#L81) | `CorrespondenceThreadOut` |
| POST | `/api/correspondence/link` | `link` | [correspondence.py:97](backend/api/correspondence.py#L97) | `CorrespondenceLinkOut`, 201/404/409 |
| DELETE | `/api/correspondence/{link_id}` | `unlink` | [correspondence.py:131](backend/api/correspondence.py#L131) | 204/404 |

### `backend.api.documents` — [`documents.py`](backend/api/documents.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/documents` | `list_documents` | [documents.py:65](backend/api/documents.py#L65) | `list[DocumentOut]` |
| POST | `/api/documents/validate-template` | `validate_template` | [documents.py:74](backend/api/documents.py#L74) | `ValidateTemplateResponse` |
| GET | `/api/documents/{match_id}/cv/pdf` | `get_cv_pdf` | [documents.py:85](backend/api/documents.py#L85) | `FileResponse` (pdf), 404 |
| GET | `/api/documents/{match_id}/letter/pdf` | `get_letter_pdf` | [documents.py:121](backend/api/documents.py#L121) | `FileResponse` (pdf), 404 |
| GET | `/api/documents/{match_id}/diff` | `get_cv_diff` | [documents.py:157](backend/api/documents.py#L157) | `CVDiffResponse`, 404 |
| POST | `/api/documents/{match_id}/regenerate` | `regenerate_documents` | [documents.py:180](backend/api/documents.py#L180) | `RegenerateResponse` (always "queued" — see Critique) |

### `backend.api.gmail` — [`gmail.py`](backend/api/gmail.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/gmail/status` | `status` | [gmail.py:24](backend/api/gmail.py#L24) | `GmailStatusOut` |
| POST | `/api/gmail/sync` | `sync_now` | [gmail.py:50](backend/api/gmail.py#L50) | `SyncOut`, 404/503 |

### `backend.api.gmail_auth` — [`gmail_auth.py`](backend/api/gmail_auth.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/gmail/oauth/start` | `oauth_start` | [gmail_auth.py:68](backend/api/gmail_auth.py#L68) | 302 → Google authorize URL |
| GET | `/api/gmail/oauth/callback` | `oauth_callback` | [gmail_auth.py:84](backend/api/gmail_auth.py#L84) | 302 → `/settings`, 400 on bad state |
| POST | `/api/gmail/disconnect` | `disconnect` | [gmail_auth.py:135](backend/api/gmail_auth.py#L135) | `{"removed": bool}` |

### `backend.api.jobs` — [`jobs.py`](backend/api/jobs.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/jobs` | `list_jobs` | [jobs.py:69](backend/api/jobs.py#L69) | `JobListOut` |
| GET | `/api/jobs/{job_id}` | `get_job` | [jobs.py:128](backend/api/jobs.py#L128) | `JobOut`, 404 |
| POST | `/api/jobs/search` | `search_jobs` | [jobs.py:163](backend/api/jobs.py#L163) | `SearchResponse`, 502 |
| GET | `/api/jobs/{job_id}/score` | `get_job_score` | [jobs.py:233](backend/api/jobs.py#L233) | `JobScoreOut`, 404 |

### `backend.api.queue` — [`queue.py`](backend/api/queue.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/queue` | `get_queue` | [queue.py:78](backend/api/queue.py#L78) | `QueueOut` |
| GET | `/api/queue/status` | `get_batch_status` | [queue.py:120](backend/api/queue.py#L120) | `BatchStatusOut` |
| POST | `/api/queue/refresh` | `refresh_queue` | [queue.py:132](backend/api/queue.py#L132) | `RefreshResponse`, 503/409 |
| GET | `/api/queue/{match_id}` | `get_match` | [queue.py:157](backend/api/queue.py#L157) | `QueueMatchOut`, 404 |
| PATCH | `/api/queue/{match_id}/skip` | `skip_match` | [queue.py:189](backend/api/queue.py#L189) | `MatchStatusUpdateOut`, 404 |
| PATCH | `/api/queue/{match_id}/status` | `update_match_status` | [queue.py:205](backend/api/queue.py#L205) | `MatchStatusUpdateOut`, 404 |
| POST | `/api/queue/{match_id}/enrich-description` | `enrich_job_description` | [queue.py:223](backend/api/queue.py#L223) | `EnrichmentResponse`, 404/422/502/503 |

### `backend.api.settings` — [`settings.py`](backend/api/settings.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/settings/profile` | `get_profile` | [settings.py:212](backend/api/settings.py#L212) | `ProfileOut` (id=0 stub when missing) |
| PUT | `/api/settings/profile` | `update_profile` | [settings.py:238](backend/api/settings.py#L238) | `ProfileOut` |
| GET | `/api/settings/search` | `get_search_settings` | [settings.py:252](backend/api/settings.py#L252) | `SearchSettingsOut`, 404 |
| PUT | `/api/settings/search` | `update_search_settings` | [settings.py:267](backend/api/settings.py#L267) | `SearchSettingsOut` |
| GET | `/api/settings/sources` | `get_sources` | [settings.py:290](backend/api/settings.py#L290) | `SourcesOut` (masked) |
| PUT | `/api/settings/sources` | `update_sources` | [settings.py:310](backend/api/settings.py#L310) | placeholder — see Critique |
| GET | `/api/settings/status` | `get_setup_status` | [settings.py:323](backend/api/settings.py#L323) | `SetupStatus` |
| POST | `/api/settings/profile/cv-upload` | `upload_cv` | [settings.py:387](backend/api/settings.py#L387) | `CvUploadResponse`, 400/413/415 |
| GET | `/api/settings/sites` | `get_sites` | [settings.py:533](backend/api/settings.py#L533) | `list[SiteOut]` |
| PUT | `/api/settings/sites/{site_name}` | `toggle_site` | [settings.py:559](backend/api/settings.py#L559) | `SiteToggleResponse`, 404 |
| GET | `/api/settings/credentials` | `get_credentials` | [settings.py:590](backend/api/settings.py#L590) | `list[CredentialOut]` |
| PUT | `/api/settings/credentials/{site_name}` | `save_credential` | [settings.py:628](backend/api/settings.py#L628) | `CredentialSaveResponse`, 400/404 |
| DELETE | `/api/settings/credentials/{site_name}/session` | `clear_session` | [settings.py:670](backend/api/settings.py#L670) | `SessionClearResponse`, 404 |
| GET | `/api/settings/custom-sites` | `get_custom_sites` | [settings.py:694](backend/api/settings.py#L694) | `list[CustomSiteOut]` |
| POST | `/api/settings/custom-sites` | `add_custom_site` | [settings.py:716](backend/api/settings.py#L716) | `CustomSiteOut` |
| DELETE | `/api/settings/custom-sites/{site_id}` | `delete_custom_site` | [settings.py:738](backend/api/settings.py#L738) | `CustomSiteDeleteResponse`, 404 |

### `backend.api.today` — [`today.py`](backend/api/today.py)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/today` | `get_today` | [today.py:105](backend/api/today.py#L105) | `TodayOut` (new_matches, blocked_actions, week_stats) |

### `backend.api.ws` — [`ws.py`](backend/api/ws.py)

| METHOD | PATH | Handler | File:line | Notes |
| --- | --- | --- | --- | --- |
| WS | `/ws` | `websocket_endpoint` | [ws.py:106](backend/api/ws.py#L106) | dispatches `ping → Pong`, otherwise routes to a registered handler |

### Health (defined directly on `app`)

| METHOD | PATH | Handler | File:line | Returns |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | `health` | [main.py:303](backend/main.py#L303) | `HealthOut`, 200 OK / 503 degraded |

---

## 8. WebSocket layer

[`ConnectionManager`](backend/api/ws.py#L43) is a singleton (`manager = ConnectionManager()`) holding:

- `active_connections: Dict[str, WebSocket]` keyed by a `uuid4` client id.
- `_lock: asyncio.Lock` guarding mutations.
- `_message_handlers: Dict[str, callable]` registered at lifespan via [`register_handler`](backend/api/ws.py#L50).

**Connect / disconnect:** [`connect`](backend/api/ws.py#L54) calls `websocket.accept()` (swallowing any exception — see Critique), allocates a uuid, stores under the lock, returns the id. [`disconnect`](backend/api/ws.py#L64) just `pop`s the dict — there's no `await websocket.close()`.

**Broadcast helpers:**

- [`broadcast`](backend/api/ws.py#L81) — sends to all, removes failing clients.
- [`send_to`](backend/api/ws.py#L92) — single client.
- [`broadcast_status`](backend/api/ws.py#L147), [`broadcast_job_assessment`](backend/api/ws.py#L156), [`broadcast_gmail_sync_status`](backend/api/ws.py#L183), [`broadcast_gmail_message_received`](backend/api/ws.py#L195) — typed wrappers that build the matching `ws_models` model and broadcast.

**Wire encoding:** [`_encode`](backend/api/ws.py#L67) requires every payload to be a Pydantic model. Calling `manager.broadcast({"type": "x"})` will raise `AttributeError`. This is **intentional** ("The runtime contract is enforced by the `model_dump_json` call") — but is documented only as a comment.

**Message model union:** [`WSMessage`](backend/api/ws_models.py#L154) is an `Annotated[Union[...], Field(discriminator="type")]` covering 15 server-to-client types (Status, JobAssessment, ScrapingStatus, MatchingStatus, TailoringStatus, ApplyReview, ApplyResult, LoginRequired, LoginConfirmed, CaptchaDetected, CaptchaResolved, GmailSyncStatus, GmailMessageReceived, Pong, ErrorMessage). [`ClientMessage`](backend/api/ws_models.py#L196) is the client-to-server union: `ConfirmSubmit | CancelApply | LoginDone | LoginCancel`.

**Handler registration** happens at [`main.py:214-217`](backend/main.py#L214). Four message types (`login_done`, `login_cancel`, `confirm_submit`, `cancel_apply`) are bound to small closures that forward to `session_manager` / `apply_engine`.

**`ws_models.py` has a fallback shim** ([`ws_models.py:6-25`](backend/api/ws_models.py#L6)) that re-defines `BaseModel`, `Field`, and `confloat` if Pydantic isn't installed. The shim has no `model_validate`, no discriminator support, no field validation — it is a "boot at all costs" pattern that silently degrades the wire-format contract.

---

## 9. Error handling patterns

**Consistent:**

- 404 for missing resource (`Application {id} not found`, `Match {id} not found`, `Job {id} not found`) — every getter does this the same way.
- 422 for Pydantic validation errors (handled by FastAPI automatically) plus explicit `422` for "Invalid apply method" ([applications.py:477](backend/api/applications.py#L477)) and "Job has no URL" ([queue.py:239](backend/api/queue.py#L239)).
- 503 for "singleton not initialised" (apply_engine, batch_runner, gemini, gmail_token_manager).
- 409 for in-flight conflict ([queue.py:145](backend/api/queue.py#L145) — "A search is already in progress"; [correspondence.py:126](backend/api/correspondence.py#L126) — "link already exists").
- Global exception handlers ([main.py:362-424](backend/main.py#L362)) wrap LaTeXCompilationError → 422, GeminiJSONError → 500, GeminiRateLimitError → 429, and an `Exception` catch-all → 500 with `{"error": ..., "code": ...}`. **Exception text is never leaked.**

**Ad-hoc / inconsistent:**

- `correspondence.unlink` raises `HTTPException(404, "link not found")` with positional args, while every other 404 uses `status_code=...` kwarg. Functionally identical, stylistically off.
- Error response shape differs: global handlers return `{"error", "code"}`; route-level `HTTPException`s use `{"detail": "..."}` (FastAPI default). There is no single error envelope.
- [`queue.enrich_job_description`](backend/api/queue.py#L223) has both a 502 for "Could not fetch job page" and a 502 for "Enrichment failed" — but uses the **same** code for two very different failure modes (HTTP fetch vs Gemini text generation).
- [`gmail_auth.oauth_callback`](backend/api/gmail_auth.py#L84) calls `.raise_for_status()` on httpx calls — any 4xx/5xx from Google leaks as an unhandled `httpx.HTTPStatusError`, which then bubbles to the generic 500 handler. No explicit "Google rejected the code" path.

---

## 10. Logging

[`backend.logging_config.configure_logging`](backend/logging_config.py#L163) is called once at startup. Key facts:

- **Format:** single JSON object per log record (`ts`, `level`, `logger`, `msg`, `module`, `line`, optional `extra`, `exc_info`, `stack_info`) — see [`JSONFormatter.format`](backend/logging_config.py#L80).
- **Level:** from `settings.jobpilot_log_level` (case-insensitive). Unknown values fall back to `INFO` via [`_parse_level`](backend/logging_config.py#L119).
- **Handlers:** stderr `StreamHandler` + `RotatingFileHandler` (10 MiB × 5 backups) at `<DATA_DIR>/logs/jobpilot.log`.
- **Idempotency:** sentinel attribute `_jobpilot_managed` is set on each handler so a second call removes our own handlers before re-attaching.
- The `jobpilot` logger propagates to root so the file handler captures everything.

`logger.exception` is used inside `_run_gmail_poll` ([main.py:66](backend/main.py#L66)), `_generic_error_handler` ([main.py:419](backend/main.py#L419)), and a handful of other places — full stack traces land in the JSON `exc_info` field. The pattern is generally consistent.

---

## 11. Security surface

**Fernet credential encryption (site credentials, Gmail refresh token).**

- The key lives at `settings.CREDENTIAL_KEY`. If empty at boot, [`config.py:115`](backend/config.py#L115) generates one via `Fernet.generate_key()` and writes it back to `.env`. This is convenient but means the key lives **plain on disk** alongside the source. There is no integration with the OS keychain.
- `settings.save_credential` ([settings.py:628](backend/api/settings.py#L628)) Fernet-encrypts email + password and stores the strings.
- `settings.get_credentials` ([settings.py:590](backend/api/settings.py#L590)) decrypts only to mask (`alban***@...`) — never returns the password.
- Gmail refresh tokens are persisted by `backend.gmail.credentials.save_credential` (out of scope but referenced from `gmail_auth.py:120`).

**CSRF state for Gmail OAuth** — [`_sign_state`](backend/api/gmail_auth.py#L35) / [`_verify_state`](backend/api/gmail_auth.py#L44):

- Format `<nonce>.<ts>.<hmac-sha256(nonce+ts, CREDENTIAL_KEY)>`.
- TTL 600 s; rejects negative ages (clock skew).
- Uses `hmac.compare_digest` for constant-time comparison.
- **Reuses the credential-encryption key as the HMAC key.** Functionally fine (key is private to the process) but conflates two concerns — see Critique.

**Path-traversal defence-in-depth** in [`settings.upload_cv`](backend/api/settings.py#L387):

1. Layer 1 — string scan for `..`, `/`, `\\` on the raw filename.
2. Layer 2 — slug stem to `[a-zA-Z0-9._\-]`, reject empty slug.
3. Layer 3 — resolve final path and require `.relative_to(templates_dir)`.
4. Plus extension allowlist (`.tex`, `.cls`) and 1 MB max size.
5. Atomic write via `.tmp` → rename, with cleanup if the DB commit fails.

**LLM prompt-injection sanitiser** — [`backend.security.sanitizer`](backend/security/sanitizer.py): truncates, strips control chars / excessive whitespace, removes lines matching ~10 known injection patterns (`ignore previous instructions`, `system:`, `<|im_start|>`, etc.), and provides `wrap_untrusted()` to wrap text in XML-style structural delimiters. Also exports `sanitize_url` (http/https only, length cap).

**No authentication / authorisation on any HTTP endpoint.** This is consistent with "single-user local desktop product" but means the API must never be bound to a public interface. There is no rate-limiting at the FastAPI layer, no CORS allow-list beyond the env var, no per-route auth dependency.

---

## 12. Critique

### CRIT-1 Silent failure of singleton initialisation

[`main.py:172-173`](backend/main.py#L172) catches **any** exception from the singleton block and downgrades it to a `warning`. If `GeminiClient()` fails at startup, the server still comes up — but `app.state.gemini`, `apply_engine`, `batch_runner` etc. are never set, and every dependent endpoint returns 503 with no clue to the operator that init actually crashed. This violates "no silent failures". The test-env carve-out should be a flag (`JOBPILOT_ALLOW_PARTIAL_INIT`), not the default.

### CRIT-2 Two routes into `app.state` — `deps.py` getters are unused

[`deps.py`](backend/api/deps.py) defines five typed getters (`get_apply_engine`, `get_batch_runner`, …) that no router calls. The routers reach into `request.app.state` directly. This means:

- Refactoring a singleton (rename, swap, mock) requires touching multiple files.
- Tests must monkey-patch `app.state` rather than override a single FastAPI dependency.
- Type hints on the singletons (apply_engine, batch_runner) are lost — `getattr(..., None)` returns `Any`.

Either delete the unused getters or convert every consumer to `Depends(get_apply_engine)`.

### HIGH-1 `regenerate_documents` lies about queueing

[`documents.regenerate_documents`](backend/api/documents.py#L180) deletes existing rows (if `force=true`), logs `"Regeneration queued"`, and returns `status="queued"` — but **nothing is actually queued**. `background_tasks: BackgroundTasks` is declared and never used. A caller polling for the regenerated PDF will wait forever. This is a hard correctness bug; either wire the regeneration into BackgroundTasks / `BatchRunner`, or change the response to "stale documents purged — re-run the batch".

### HIGH-2 `update_application` accepts arbitrary status strings

[`UpdateApplicationRequest.status`](backend/api/applications.py#L74) is typed `Optional[str]` — no `Literal` constraint — even though `CreateApplicationRequest.status` is constrained ([applications.py:67-69](backend/api/applications.py#L67)). A `PATCH /applications/{id}` with `{"status": "ANYTHING"}` will succeed and corrupt the lifecycle state machine. Mirror the literal set used on create.

### HIGH-3 `PUT /api/settings/sources` is a no-op masquerading as an update

[`update_sources`](backend/api/settings.py#L310) accepts a `SourcesUpdate` body, ignores it (`del body`), and returns guidance prose. A 200 response to a write that does nothing is a UX trap — frontends will assume their keys are saved. Should return 405 / 501, or do the in-place `.env` rewrite the way `config.py` already does for `CREDENTIAL_KEY`.

### HIGH-4 `/api/queue/refresh` swallows background errors

[`queue.refresh_queue._run`](backend/api/queue.py#L147) does `try: await runner.run_batch() except Exception as exc: logger.error(...)`. The endpoint returns 200 `{"status": "started"}` then the actual batch fails silently. WS clients may eventually see no status updates. The runner *should* publish a terminal `Status` (or `ErrorMessage`) over WS on failure; right now only `logger.error` records it. Combine with CRIT-1: errors that happen out of band are invisible to the user.

### HIGH-5 Engine URL ignores resolved `DATA_DIR`

[`database.py:23`](backend/database.py#L23) interpolates `settings.jobpilot_data_dir` directly — the *raw* env string. Meanwhile [`config.py:135-137`](backend/config.py#L135) carefully resolves `DATA_DIR` relative to `PROJECT_ROOT`. Result: if you run the server with `JOBPILOT_DATA_DIR=./data` from a non-project directory, the DB ends up somewhere other than the rest of the data tree (the dir-creation code in `main.py:79` uses the resolved path). Use `DATA_DIR` for the engine URL too.

### MED-1 No transaction commit/rollback in `get_db`

[`get_db`](backend/database.py#L67) yields a fresh session with no commit/rollback wrapper. Every route must remember `await db.commit()` after writes — and several do (e.g. `correspondence.unlink`, `queue.skip_match`). Forgetting it leaves changes uncommitted. Compare to [`db_session`](backend/database.py#L51), which already encodes commit-on-success. The dependency should match the context manager.

### MED-2 No graceful shutdown for batch runner / engine

Lifespan shutdown ([main.py:246-252](backend/main.py#L246)) only stops the scheduler. A batch in flight is abandoned; the SQLAlchemy engine isn't disposed; the Gmail token manager is not asked to flush. On Ctrl-C in production, in-progress applications can be left with partial state. Add `await engine.dispose()` and a `batch_runner.cancel()` hook.

### MED-3 Inconsistent error envelopes

Global exception handlers emit `{"error", "code"}`; `HTTPException`s emit FastAPI's default `{"detail"}`. Frontends have to handle both shapes. Pick one (`{"error": str, "code": str}` is the more debuggable form) and emit it from all paths, including via a custom `HTTPException` subclass or an `http_exception_handler`.

### MED-4 `correspondence.link` hard-codes `direction="inbound"`

[`correspondence.link`](backend/api/correspondence.py#L97) writes `direction="inbound"` regardless of the actual message's `from_address`. A user manually linking their own *outgoing* email will end up with a row that says "inbound" — confusing the downstream classifier and the response-rate analytics. Either accept `direction` on the request or infer it by comparing `msg.from_address` against the connected `GmailCredential.email_address`.

### MED-5 `enrich_job_description` reaches into a private method

[`queue.enrich_job_description`](backend/api/queue.py#L255) calls `fetcher._clean_html(html)` — a name-mangled-by-convention private method. The router is the wrong layer to know how to clean HTML. Push `clean_html` into a public method, or have `ScraplingFetcher` expose a single `fetch_and_clean(url)` helper.

### MED-6 `applications.apply_to_job` is doing too much

[`apply_to_job`](backend/api/applications.py#L454) is 150 lines: merges profile fields into `additional_answers`, resolves `apply_url` from the job row, picks the latest tailored documents, falls back to base CV, falls back to *any* `.pdf` in the templates directory, then finally calls `engine.apply`. This is service logic, not transport. Move applicant assembly, document resolution, and the `_resolve_documents` helper into a `backend.applier.assembly` (or similar) module; the router should just orchestrate.

### MED-7 WS handler dispatch silently drops unknown types

[`websocket_endpoint`](backend/api/ws.py#L106) only routes `ping` and types present in `manager._message_handlers`. Anything else is **silently ignored** (or only logged at DEBUG). The frontend gets no signal that the server didn't understand. Emit an `ErrorMessage(code="unknown_type")` so contracts are debuggable.

### MED-8 `ws.connect` swallows `websocket.accept()` failures

[`ConnectionManager.connect`](backend/api/ws.py#L54) wraps `await websocket.accept()` in `except Exception: pass` then proceeds to register the client. If the handshake genuinely fails, every later `send_text` raises and the connection is immediately torn down — but a stale uuid sits in `active_connections` until then. Re-raise on accept failure or at minimum don't register the client.

### MED-9 No DB pool/health on `/api/health` for tectonic

`HealthOut` exposes `tectonic: bool` and `tectonic_hint`, but the health route does **not** flip `status` to "degraded" when tectonic is missing. A k8s probe gets 200 with `tectonic=false` and no operator action. Either separate "tectonic-present" from "liveness" (current) or report `degraded` for the missing-tool case too. The current behaviour is documented but easy to miss.

### MED-10 `gmail_auth.oauth_callback` raises bare `httpx.HTTPStatusError`

[`oauth_callback`](backend/api/gmail_auth.py#L84) calls `raise_for_status()` twice. A 401 from Google's token endpoint then bubbles to the generic 500 handler — the user is redirected nowhere, just sees `{"error":"Internal server error"}`. Wrap in `try/except httpx.HTTPStatusError` and redirect to `/settings?gmail_error=token_exchange_failed`.

### LOW-1 `ws_models.py` Pydantic fallback shim is dangerous

[`ws_models.py:6-25`](backend/api/ws_models.py#L6) defines a bare-bones `BaseModel`/`Field`/`confloat` if Pydantic isn't installed. The shim discards every type and discriminator, so a process running on the fallback would still appear to work but emit malformed JSON. Pydantic is a *required* dependency for the rest of the codebase — drop the shim and let the import error.

### LOW-2 Duplicate `_utc_now` definitions

`_utc_now()` is defined in [`analytics.py:7`](backend/api/analytics.py#L7), [`settings.py:27`](backend/api/settings.py#L27), [`today.py:55`](backend/api/today.py#L55), [`correspondence.py:18`](backend/api/correspondence.py#L18). Four copies of the same one-liner. Hoist to `backend.utils.time` (or similar) — especially since each is the project's own answer to "naive-UTC matches DB".

### LOW-3 `analytics.get_analytics_summary` swallows JobMatch import error

[`analytics.py:71-79`](backend/api/analytics.py#L71) does `try: from backend.models.job import JobMatch ... except Exception: pass`. If `JobMatch` is unavailable the response says `avg_match_score=None` — indistinguishable from "no matches yet". Either let the import error propagate (it's a static import in every other file) or differentiate "no data" from "couldn't compute".

### LOW-4 `JobOut` shape mismatch between `jobs.py` and `queue.py`

`JobOut` ([jobs.py:19](backend/api/jobs.py#L19)) and `QueueJobOut` ([queue.py:23](backend/api/queue.py#L23)) describe the same `Job` entity with subtly different fields (`country`, `apply_method` exist on queue but not jobs; `description`, `scraped_at`, `salary_text` differ in nullability). Two source-of-truth schemas for one model is fertile ground for drift. Promote to a shared `backend.schemas.job` and have the routers add their own envelope on top.

### LOW-5 `list_unlinked` hard-codes `LIMIT 200`

[`correspondence.list_unlinked`](backend/api/correspondence.py#L65) returns at most 200 unlinked messages with no pagination, no `total`, no cursor. A user with a busy inbox loses tail messages silently. Either paginate properly or return an explicit `"truncated": True` flag plus a `total_remaining`.

### LOW-6 CSV export ignores filter params

[`export_applications`](backend/api/applications_export.py#L70) takes only `format`. Users cannot export "applied this week" or "all rejected". Mirror the filters from `list_applications` (`status`, `needs_follow_up`) so the export is useful as a power-user report.

### LOW-7 `correspondence.list_unlinked` uses positional `HTTPException(404, "...")`

[correspondence.py:103](backend/api/correspondence.py#L103) and [:108](backend/api/correspondence.py#L108) and [:126](backend/api/correspondence.py#L126) and [:137](backend/api/correspondence.py#L137) all use positional args; every other router uses `status_code=...`. Pure style consistency — but the inconsistency reads as carelessness.

### LOW-8 Type-tightening opportunities

- `UpdateApplicationRequest.status: Optional[str]` (HIGH-2 above).
- `apply_engine: ApplicationEngine = getattr(request.app.state, "apply_engine", None)` ([applications.py:470](backend/api/applications.py#L470)) — the type annotation lies; the actual value can be `None`.
- WS `ConnectionManager._message_handlers: Dict[str, Any]` ([ws.py:48](backend/api/ws.py#L48)) — should be `Callable[[dict], None]`.
- `HealthOut.timestamp: datetime` is fine but the JSON serialization will be `+00:00`-suffixed naive; the rest of the codebase intentionally stores naive UTC. Pick a convention and document it.

### LOW-9 Dead code

- [`backend/api/__init__.py`](backend/api/__init__.py) lists `__all__` modules but is otherwise unused; missing `correspondence`, `applications_export`, `gmail`, `gmail_auth`.
- [`from starlette.responses import FileResponse`](backend/main.py#L16) in `main.py` is imported but never used (FileResponse is used inside `documents.py`, not main).
- `BackgroundTasks` is declared on `regenerate_documents` but never used (HIGH-1).

---

## 13. Inventory

| File | One-line role |
| --- | --- |
| [`backend/main.py`](backend/main.py) | FastAPI factory, lifespan singletons, scheduler, exception handlers, SPA fallback, `/api/health`. |
| [`backend/config.py`](backend/config.py) | Pydantic settings, `.env` validator, `CREDENTIAL_KEY` auto-mint, `PROJECT_ROOT`, resolved `DATA_DIR`. |
| [`backend/defaults.py`](backend/defaults.py) | Non-env constants: field length caps, scheduler limits, ATS-gap thresholds, embedding model name. |
| [`backend/logging_config.py`](backend/logging_config.py) | Idempotent JSON-logging setup with stderr + 10 MiB rotating file handler. |
| [`backend/database.py`](backend/database.py) | aiosqlite engine, WAL pragma, `AsyncSessionLocal`, `get_db`, `init_db`, lightweight column migrations. |
| [`backend/api/__init__.py`](backend/api/__init__.py) | Module re-export list (slightly out of date — missing new routers). |
| [`backend/api/deps.py`](backend/api/deps.py) | `DBSession` alias and five `app.state` singleton getters (currently unused). |
| [`backend/api/analytics.py`](backend/api/analytics.py) | `/api/analytics/{summary,trends}` aggregate counts. |
| [`backend/api/applications.py`](backend/api/applications.py) | CRUD + events + `/apply` orchestration for `Application` rows. |
| [`backend/api/applications_export.py`](backend/api/applications_export.py) | `/api/applications/export?format=csv` streaming export. |
| [`backend/api/correspondence.py`](backend/api/correspondence.py) | Gmail-message ↔ Application linking (`/unlinked`, `/{app_id}`, `/link`, `/{link_id}`). |
| [`backend/api/documents.py`](backend/api/documents.py) | Tailored CV/letter PDF + diff endpoints, template validator, regenerate stub. |
| [`backend/api/gmail.py`](backend/api/gmail.py) | Gmail account status + manual sync trigger. |
| [`backend/api/gmail_auth.py`](backend/api/gmail_auth.py) | Gmail OAuth start / callback / disconnect with signed CSRF state. |
| [`backend/api/jobs.py`](backend/api/jobs.py) | Job catalogue + manual Adzuna search + per-job score. |
| [`backend/api/queue.py`](backend/api/queue.py) | Match queue, batch refresh, status transitions, on-demand description enrichment. |
| [`backend/api/settings.py`](backend/api/settings.py) | User profile, search settings, sources, sites, credentials, CV upload, custom sites. |
| [`backend/api/today.py`](backend/api/today.py) | Single-call dashboard endpoint (new matches + blocked actions + week stats). |
| [`backend/api/ws.py`](backend/api/ws.py) | WebSocket endpoint, `ConnectionManager`, broadcast helpers. |
| [`backend/api/ws_models.py`](backend/api/ws_models.py) | Discriminated unions for server↔client WS messages (with Pydantic fallback shim). |
| [`backend/security/__init__.py`](backend/security/__init__.py) | Empty package marker. |
| [`backend/security/sanitizer.py`](backend/security/sanitizer.py) | LLM prompt-injection scrubber + `wrap_untrusted` + `sanitize_url`. |
