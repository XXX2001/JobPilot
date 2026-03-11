# Module: Config, Database & Application Entry Point

## Purpose

These three files form the foundational layer of the JobPilot backend. `config.py` defines all runtime configuration by loading environment variables and a `.env` file through a `pydantic-settings` `BaseSettings` subclass, exposing a single `settings` singleton consumed everywhere. `database.py` uses `settings` to construct an async SQLAlchemy engine backed by SQLite (via `aiosqlite`), provides session factories, and runs schema migrations and seed data at startup. `main.py` wires everything together: it creates the `FastAPI` application, registers all API routers and middleware, and implements the `lifespan` context manager that sequences startup tasks (data directory creation → DB init → singleton construction → WebSocket handler wiring) and graceful shutdown.

---

## Key Components

### `config.py`

Defines a single `Settings` class (inherits `pydantic_settings.BaseSettings`) that reads from environment variables and a `.env` file. Configuration is **case-insensitive** (`case_sensitive=False`). A module-level `settings = Settings()` instance is created at import time and used as a shared singleton by all other modules.

Three fields (`GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) have no defaults and will raise a `ValidationError` at startup if absent.

### `database.py`

Builds the async SQLAlchemy infrastructure on top of `settings.jobpilot_data_dir`. Key responsibilities:

- Creates the async engine with WAL-mode SQLite for better concurrent read performance.
- Exposes `AsyncSessionLocal` as the session factory used by routers and the scheduler.
- Provides `init_db()` which runs `Base.metadata.create_all` and then seeds `job_sources` from `SITE_CONFIGS` if the table is empty.
- Exposes `db_session()` (context manager) for service-layer use and `get_db()` (async generator) for FastAPI `Depends`.

### `main.py`

Creates the `FastAPI` app instance with a `lifespan` context manager. Middleware and routers are registered at module level (outside `lifespan`). The `lifespan` function handles all runtime setup and teardown. A custom `SPAStaticFiles` subclass serves the SvelteKit frontend build with correct cache-control headers.

---

## Public Interface

### `config.py`

All fields belong to the `Settings` class and are accessed via the `settings` singleton.

| Field | Type | Default | Env Var | Description |
|---|---|---|---|---|
| `GOOGLE_API_KEY` | `str` | *(required)* | `GOOGLE_API_KEY` | Google Gemini API key; no default — startup fails if missing. |
| `ADZUNA_APP_ID` | `str` | *(required)* | `ADZUNA_APP_ID` | Adzuna job-search API application ID; required. |
| `ADZUNA_APP_KEY` | `str` | *(required)* | `ADZUNA_APP_KEY` | Adzuna API key; required. |
| `SERPAPI_KEY` | `str` | `""` | `SERPAPI_KEY` | SerpAPI key; optional, not currently wired to active code paths. |
| `CREDENTIAL_KEY` | `str` | `""` | `CREDENTIAL_KEY` | Fernet symmetric key for encrypting stored site credentials. Empty string disables encryption. |
| `jobpilot_host` | `str` | `"127.0.0.1"` | `JOBPILOT_HOST` | Host address Uvicorn binds to. |
| `jobpilot_port` | `int` | `8000` | `JOBPILOT_PORT` | Port Uvicorn listens on. |
| `jobpilot_log_level` | `str` | `"info"` | `JOBPILOT_LOG_LEVEL` | Uvicorn/Python log level (`debug`, `info`, `warning`, `error`). |
| `jobpilot_scraper_headless` | `bool` | `True` | `JOBPILOT_SCRAPER_HEADLESS` | Run Playwright browsers in headless mode when `True`. |
| `jobpilot_data_dir` | `str` | `"./data"` | `JOBPILOT_DATA_DIR` | Root directory for all persistent data (DB, CVs, letters, browser sessions, logs). |
| `GOOGLE_MODEL` | `str` | `"gemini-3-flash-preview"` | `GOOGLE_MODEL` | Primary Gemini model name sent to the Google AI API. |
| `GOOGLE_MODEL_FALLBACKS` | `str` | `""` | `GOOGLE_MODEL_FALLBACKS` | Comma-separated list of fallback model names tried if the primary model fails. Empty string means no fallbacks. |
| `SCRAPLING_ENABLED` | `bool` | `True` | `SCRAPLING_ENABLED` | Feature flag: enables the Tier 1 Scrapling HTTP fetcher (faster, cheaper path before Playwright). |
| `APPLY_TIER1_ENABLED` | `bool` | `True` | `APPLY_TIER1_ENABLED` | Feature flag: enables the Tier 1 Playwright direct-fill application strategy. |

### `database.py`

| Export | Kind | Signature / Value | Description |
|---|---|---|---|
| `engine` | `AsyncEngine` | `create_async_engine("sqlite+aiosqlite:///{data_dir}/jobpilot.db", echo=False)` | Async SQLAlchemy engine. WAL journal mode is applied on every new connection via a sync event listener. |
| `AsyncSessionLocal` | `async_sessionmaker[AsyncSession]` | `async_sessionmaker(engine, expire_on_commit=False)` | Session factory. `expire_on_commit=False` keeps ORM objects usable after `commit()` without re-fetching. |
| `init_db` | `async def` | `async def init_db() -> None` | Creates all tables via `Base.metadata.create_all`, then calls `_seed_default_sources()`. Called once during lifespan startup. |
| `db_session` | `@asynccontextmanager` | `async def db_session() -> AsyncGenerator[AsyncSession, None]` | Yields a session that auto-commits on success or rolls back on exception. Intended for service/task code outside of request scope. |
| `get_db` | `async def` | `async def get_db() -> AsyncGenerator[AsyncSession, None]` | Plain async generator for use with `fastapi.Depends`. Does not auto-commit; callers manage transactions explicitly. |
| `_seed_default_sources` | `async def` (private) | `async def _seed_default_sources() -> None` | Reads `SITE_CONFIGS` from `backend.scraping.site_prompts` and inserts one `JobSource` row per site (excluding the `"lab_website"` template entry). Skips seeding if any row already exists. |

### `main.py`

**FastAPI app instance**

```python
app: FastAPI = FastAPI(lifespan=lifespan, redirect_slashes=False)
```

**Middleware**

| Middleware | Configuration |
|---|---|
| `CORSMiddleware` | `allow_origins=["*"]`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]` — fully open, intended for development. |

**Routers registered**

| Module | Router variable | Typical prefix |
|---|---|---|
| `backend.api.jobs` | `jobs.router` | `/api/jobs` |
| `backend.api.queue` | `queue.router` | `/api/queue` |
| `backend.api.applications` | `applications.router` | `/api/applications` |
| `backend.api.documents` | `documents.router` | `/api/documents` |
| `backend.api.settings` | `api_settings.router` | `/api/settings` |
| `backend.api.analytics` | `analytics.router` | `/api/analytics` |
| `backend.api.ws` | `ws.router` | `/ws` |

All router inclusions are wrapped in a `try/except` — missing modules produce a debug log rather than a crash, facilitating incremental development.

**Built-in routes**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Returns `{"status": "ok", "version": "0.1.0", "db": "connected", "tectonic": bool, "gemini_key_set": bool}`. Includes a `tectonic_hint` key when Tectonic is not found. |

**Static file serving**

`SPAStaticFiles` (subclass of `starlette.staticfiles.StaticFiles`) is mounted at `/` from `frontend/build` if that directory exists. It applies cache headers: `public, max-age=31536000, immutable` for paths under `/_app/immutable/` (content-hashed assets), and `no-cache` for everything else (including `index.html`). Any path that raises an exception falls back to `index.html` to support client-side SvelteKit routing.

**Global exception handlers**

| Exception class | HTTP status | Response body `code` |
|---|---|---|
| `LaTeXCompilationError` | 422 | `"latex_compile_error"` |
| `GeminiJSONError` | 500 | `"gemini_json_error"` |
| `GeminiRateLimitError` | 429 | `"rate_limit"` |
| `Exception` (catch-all) | 500 | `"internal_error"` |

All handlers are registered conditionally — if the respective module cannot be imported, the handler is silently skipped.

**`app.state` singletons (set during lifespan)**

| Attribute | Type | Description |
|---|---|---|
| `app.state.gemini` | `GeminiClient` | Shared LLM client. |
| `app.state.cv_pipeline` | `CVPipeline` | LaTeX CV tailoring pipeline. |
| `app.state.letter_pipeline` | `LetterPipeline` | Cover-letter generation pipeline. |
| `app.state.adzuna` | `AdzunaClient` | Adzuna REST API client. |
| `app.state.adaptive_scraper` | `AdaptiveScraper` | Playwright-based adaptive scraper. |
| `app.state.session_manager` | `BrowserSessionManager` | Manages persistent browser login sessions. |
| `app.state.scraping_orchestrator` | `ScrapingOrchestrator` | Coordinates all scraping strategies. |
| `app.state.matcher` | `JobMatcher` | Scores and filters jobs against the user profile. |
| `app.state.apply_engine` | `ApplicationEngine` | Orchestrates job application submission. |
| `app.state.morning_scheduler` | `MorningBatchScheduler` | Scheduled batch job (scrape + match + apply). |

**Lifespan WebSocket handlers registered**

| Message type | Handler | Action |
|---|---|---|
| `"login_done"` | `_handle_login_done` | Calls `session_manager.confirm_login(site)`. |
| `"login_cancel"` | `_handle_login_cancel` | Calls `session_manager.cancel_login(site)`. |
| `"confirm_submit"` | `_handle_confirm_submit` | Calls `apply_engine.signal_confirm(job_id)`. |
| `"cancel_apply"` | `_handle_cancel_apply` | Calls `apply_engine.signal_cancel(job_id)`. |

---

## Data Flow

The following sequence occurs from process start to first request being served:

1. **Config load** — `backend.config` is imported, `Settings()` is instantiated, and all environment variables / `.env` values are parsed and validated by pydantic. A `ValidationError` here is fatal.

2. **Module-level app construction** — `main.py` runs: the `FastAPI` instance is created, `CORSMiddleware` is added, and all API routers are included. This happens before `lifespan` is invoked.

3. **Lifespan startup begins** — Uvicorn calls the `lifespan` async context manager.

4. **Data directory creation** — Six subdirectories under `JOBPILOT_DATA_DIR` are created with `mkdir(parents=True, exist_ok=True)`: `cvs/`, `letters/`, `templates/`, `browser_sessions/`, `browser_profiles/`, `logs/`.

5. **Database initialization** — `init_db()` is called:
   - `create_async_engine` builds `sqlite+aiosqlite:///{data_dir}/jobpilot.db`.
   - A sync event listener fires `PRAGMA journal_mode=WAL` on every new SQLite connection.
   - `Base.metadata.create_all` creates any missing tables.
   - `_seed_default_sources()` inserts default `JobSource` rows from `SITE_CONFIGS` if the table is empty.

6. **Singleton construction** — All service objects are instantiated in dependency order. The `ScraplingFetcher` is conditionally created only if `SCRAPLING_ENABLED` is `True`. The `ApplicationEngine` is given a hardcoded `daily_limit=10`. All singletons are stored on `app.state`.

7. **WebSocket handler registration** — Four message-type handlers are registered with the WS manager to bridge browser UI events to `BrowserSessionManager` and `ApplicationEngine`.

8. **Ready to serve** — Uvicorn begins accepting HTTP/WebSocket connections.

9. **Shutdown** — On SIGTERM/SIGINT, `lifespan` resumes after `yield`, calls `scheduler.stop()`, and exits.

---

## Configuration

Master reference for all environment variables read by the application.

| Env Var | Type | Default | Required | Description |
|---|---|---|---|---|
| `GOOGLE_API_KEY` | `str` | — | **Yes** | Google AI / Gemini API key. Used by `GeminiClient` and passed directly to `AdaptiveScraper` and `ApplicationEngine`. |
| `ADZUNA_APP_ID` | `str` | — | **Yes** | Adzuna job-search API application ID. |
| `ADZUNA_APP_KEY` | `str` | — | **Yes** | Adzuna API secret key. |
| `SERPAPI_KEY` | `str` | `""` | No | SerpAPI key; accepted by config but not actively consumed in current code paths. |
| `CREDENTIAL_KEY` | `str` | `""` | No | Fernet key (base64-encoded 32-byte key) for encrypting site credentials stored in the DB. |
| `JOBPILOT_HOST` | `str` | `"127.0.0.1"` | No | Bind address passed to Uvicorn. |
| `JOBPILOT_PORT` | `int` | `8000` | No | Listen port passed to Uvicorn. |
| `JOBPILOT_LOG_LEVEL` | `str` | `"info"` | No | Log verbosity (`debug`, `info`, `warning`, `error`, `critical`). |
| `JOBPILOT_SCRAPER_HEADLESS` | `bool` | `True` | No | Whether Playwright browsers run headless. Set to `false` during development to watch browser interactions. |
| `JOBPILOT_DATA_DIR` | `str` | `"./data"` | No | Root path for all persistent data. Relative paths are resolved from the working directory at process start. |
| `GOOGLE_MODEL` | `str` | `"gemini-3-flash-preview"` | No | Gemini model name for all LLM calls. |
| `GOOGLE_MODEL_FALLBACKS` | `str` | `""` | No | Comma-separated ordered list of fallback model names. Example: `"gemini-1.5-flash,gemini-1.0-pro"`. |
| `SCRAPLING_ENABLED` | `bool` | `True` | No | Enables the Tier 1 Scrapling HTTP-first scraping path. Set to `false` to force Playwright for all scraping. |
| `APPLY_TIER1_ENABLED` | `bool` | `True` | No | Enables the Tier 1 direct Playwright form-fill application strategy. Set to `false` to always use the assisted (browser-use) strategy. |

---

## Known Limitations / TODOs

- **`daily_limit` is hardcoded** — `ApplicationEngine` is constructed with `daily_limit=10` in `main.py` (line 88). There is no corresponding `Settings` field or environment variable, making it impossible to adjust without a code change.

- **CORS is fully open** — `allow_origins=["*"]` with `allow_credentials=True` is explicitly noted as being for development. This combination is rejected by most browsers in production (credentials require explicit origins, not a wildcard). There is no mechanism to restrict origins via configuration.

- **`JOBPILOT_DATA_DIR` is relative by default** — The default `"./data"` is resolved relative to the process working directory, not the project root, which can cause the data directory to be created in unexpected locations depending on how Uvicorn is launched.

- **APScheduler auto-start was removed** — A comment in `main.py` (line 112) documents that the `MorningBatchScheduler` no longer starts automatically. Batch jobs only run via `POST /api/queue/refresh`. There is no cron-based or time-based scheduling in place.

- **Seed data hardcodes `country: "fr"`** — In `_seed_default_sources()`, all seeded `JobSource` rows receive `config={"country": "fr"}` (line 93 of `database.py`). This is not driven by any configuration value.

- **`SERPAPI_KEY` is accepted but unused** — The field exists in `Settings` and is loaded from the environment, but no current code path in the active scraping or application flows uses it.

- **Frontend build path is hardcoded** — The static files directory `frontend/build` in `main.py` (line 307) is not configurable via environment variable.

- **Version is hardcoded** — The `/api/health` endpoint returns `"version": "0.1.0"` as a string literal rather than reading from `pyproject.toml` or a package metadata attribute.

- **`engine.sync_engine` WAL listener silently swallows errors** — The `set_wal_mode` event listener catches all exceptions and logs only at `DEBUG` level. A failed `PRAGMA journal_mode=WAL` will go unnoticed in production log levels.
