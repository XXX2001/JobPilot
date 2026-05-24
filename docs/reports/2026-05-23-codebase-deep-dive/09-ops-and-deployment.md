# 09 — Ops, Configuration, Packaging & Deployment

> Scope: `start.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`/`.env`, `pyproject.toml`/`uv.lock`, `alembic.ini` + `alembic/`, `scripts/`, `bin/`, `data/`, the ops surface of `backend/logging_config.py`, `README.md` setup, and `CHANGELOG.md`. Schema content is owned by the models agent; this report covers how migrations *run*, not what they declare.

---

## 1. Purpose — the dual story

JobPilot's `README.md:3` calls itself **"Your personal job-hunting assistant that runs on your own computer."** The marketing surface is unambiguously local-first: an installer, a desktop shortcut, a launcher script, a bound-to-`127.0.0.1` uvicorn process (`start.py:116`), an auto-opened browser tab (`start.py:126`), a SQLite file in `data/` (`alembic.ini:89`), a Fernet key generated on first run (`backend/config.py:115-131`).

Underneath that is a fully formed containerised path: a four-stage `Dockerfile`, a `docker-compose.yml` that mounts `./data` as a named volume and binds `0.0.0.0:8000` to the host, a CORS allow-list (`docker-compose.yml:46`), and a healthcheck wired to `/api/health` (`docker-compose.yml:50-55`). The `docker-compose.yml:8-23` commentary even sketches a deferred Postgres swap-in.

The result is two coexisting deployment models with no canonical answer to "where is JobPilot actually meant to run?" The README never mentions Docker; the changelog (`CHANGELOG.md:222-223`, PR-8) describes the Dockerfile as "first deployable artifact" — i.e. internal infra teams probably use it, but end users follow `scripts/install.sh`. This split is the single biggest source of operational drift in the codebase and is critiqued in §13.

---

## 2. Local-dev path — what `start.py` actually does

`start.py:139` is registered as the `jobpilot` console-script (`pyproject.toml:25-26`) and is the only documented user-facing entry point.

Flow:

1. **`check_prerequisites()` — [`start.py:17`](../../../start.py#L17).** Verifies three things: `data/` directory exists, `frontend/build/` exists, and a `tectonic` binary is on `PATH` or under `bin/`. Missing any → `sys.exit(1)` with a `⚠ … not found. Run the installer first.` line. This is purely a smoke check — it does not validate `.env`, the DB, or Playwright.
2. **`Path(PROJECT_ROOT).mkdir(parents=True, exist_ok=True)` — [`start.py:103`](../../../start.py#L103).** Defensive no-op (the project root already exists, otherwise we couldn't have imported `backend.config.PROJECT_ROOT`).
3. **Data sub-dir creation — [`start.py:106-114`](../../../start.py#L106-L114).** Creates `data/{cvs,letters,templates,browser_sessions,browser_profiles,logs}`. This duplicates the same loop in `backend/main.py:79-88` (the FastAPI lifespan) — neither side is authoritative.
4. **`free_port(8000)` — [`start.py:39-97`](../../../start.py#L39-L97).** A platform-conditional process-killer: opens a TCP probe, then `lsof`/`fuser` on Linux or `netstat`+`taskkill` on Windows, SIGTERM → 1.5 s wait → SIGKILL. This is opinionated behaviour — running JobPilot will *kill whatever else is on :8000* without prompting.
5. **`threading.Timer(2.0, webbrowser.open(...))` — [`start.py:124-126`](../../../start.py#L124-L126).** Fires-and-forgets a 2 s delayed browser tab.
6. **`uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)` — [`start.py:129-135`](../../../start.py#L129-L135).** Production-style uvicorn (no `--reload`), bound to loopback only.

There is **no frontend dev-server fork** in `start.py`. The launcher assumes a pre-built `frontend/build/` (the SvelteKit static adapter output) and serves it as `/` via the `SPAStaticFiles` mount in `backend/main.py:447-451`. Live frontend dev would require running `npm run dev --prefix frontend` separately (Vite on :5173, which is the default in the `JOBPILOT_ALLOWED_ORIGINS` allow-list at `.env.example:20`).

`port` and `host` in `start.py:116-117` are **hard-coded** — `start.py` ignores `JOBPILOT_HOST` / `JOBPILOT_PORT` from `.env`, even though `backend/config.py:26-27` reads them. The Docker `CMD` (`Dockerfile:98`) also bypasses `start.py` entirely, calling `uvicorn` directly with `--host 0.0.0.0`. The launcher's settings honour is therefore one-sided.

---

## 3. Container path — `Dockerfile` multi-stage breakdown

The Dockerfile (`Dockerfile:1`) declares `# syntax=docker/dockerfile:1.7` and uses four stages:

| Stage | Base | Purpose |
| --- | --- | --- |
| `python-builder` | `python:3.12-slim` | Installs `uv` (pinned `ghcr.io/astral-sh/uv:0.5.11` — `Dockerfile:18`), runs `uv sync --frozen --no-install-project --no-dev` to materialise `.venv` from `uv.lock`. |
| `frontend-builder` | `node:20-slim` | `npm ci` then `npm run build` against `frontend/`. |
| `tectonic-fetcher` | `python:3.12-slim` | `curl`s the pinned `TECTONIC_VERSION=0.15.0` musl static binary for x86_64/aarch64. Note: x86_64 and aarch64 only — no native macOS layer, but containers always run Linux. |
| `runtime` | `python:3.12-slim` | Final image: APT libs for Chromium headless, non-root `jobpilot` user (UID/GID 1000), copies `.venv`, `tectonic`, `backend/`, `alembic/`, `alembic.ini`, `start.py`, `pyproject.toml`, and `frontend/build/`. |

Key runtime decisions:

- **Env vars baked in** at `Dockerfile:57-60`: `JOBPILOT_HOST=0.0.0.0`, `JOBPILOT_PORT=8000`, `JOBPILOT_DATA_DIR=/app/data`, `JOBPILOT_SCRAPER_HEADLESS=true`. Compose overrides them again for clarity (`docker-compose.yml:38-46`).
- **Playwright Chromium** installed *per-user* at `Dockerfile:90` (`python -m playwright install chromium`) inside the `jobpilot` user's home. Wrapped in `|| true` — failure does not abort the build.
- **`EXPOSE 8000`** — `Dockerfile:92`.
- **HEALTHCHECK** — `Dockerfile:94-95` uses `wget --spider http://localhost:8000/api/health` every 30 s, 5 s timeout, 3 retries, 20 s start-period.
- **CMD** — `Dockerfile:98` runs `uvicorn backend.main:app --host 0.0.0.0 --port 8000` directly. `start.py` is copied but **never invoked** in the container.

Image size is not measured in the repo, but the layer set is reasonable for a Playwright-bearing image: slim base + APT libs + venv + Tectonic (~35 MB) + Chromium (~280 MB) + node-built static assets. Estimate: 600–800 MB final, dominated by Chromium.

Build cacheability is mostly correct — `pyproject.toml + uv.lock` are COPYd before code, so dep changes invalidate just the dep layer. Same for `package.json + package-lock.json`. But `start.py pyproject.toml ./` (`Dockerfile:81`) re-copies `pyproject.toml` in the runtime stage, which is fine because the venv was already built in stage 1.

---

## 4. `docker-compose.yml`

Single service `jobpilot` (`docker-compose.yml:27`), `restart: unless-stopped`, port-mapped `8000:8000`. Notable:

- **`env_file: .env`** (`docker-compose.yml:36-37`) — relies on the same `.env` the local-dev path uses. The `environment:` block then overrides four keys to force container-correct values.
- **CORS** — `JOBPILOT_ALLOWED_ORIGINS` defaulted to `http://localhost:8000,http://127.0.0.1:8000` (`docker-compose.yml:46`). This drops the `:5173` Vite origin that's in the bare `.env.example` — sensible for a container that serves prebuilt assets.
- **Volume** — `./data:/app/data` (`docker-compose.yml:48-49`) bind-mounts the host's `data/` into the container. This is the **single persistence boundary** — `jobpilot.db`, logs, CV templates, browser sessions all live there.
- **Healthcheck** — duplicated from `Dockerfile:94-95` with identical settings.

No `networks:` block (relies on the default bridge). No `depends_on:` (single-service). No `resources:` limits. The Postgres future-state is commented out at `docker-compose.yml:8-23`.

---

## 5. Environment

`.env.example` (lines 1-37) ships **15 documented variables**. Tabulated:

| Var | Required? | Source/Default | Purpose |
| --- | --- | --- | --- |
| `GOOGLE_API_KEY` | **Required** | none (must set) | Gemini SDK auth (`backend/config.py:17`). Asserted via `SecretStr`; `_load_settings()` emits a friendly hint on miss (`backend/config.py:79-108`). |
| `ADZUNA_APP_ID` | **Required** | none | Public Adzuna app ID (`backend/config.py:18`). |
| `ADZUNA_APP_KEY` | **Required** | none | Adzuna secret (`backend/config.py:19`). `SecretStr`. |
| `CREDENTIAL_KEY` | Optional (auto-generated) | `""` then Fernet | Fernet key for `SiteCredential` + Gmail token at-rest encryption. Auto-generated *and rewritten back to `.env`* on first launch when blank (`backend/config.py:115-131`). |
| `JOBPILOT_HOST` | Optional | `127.0.0.1` | Server bind (honored only by docker-compose / `backend/main.py`; `start.py` hard-codes `127.0.0.1`). |
| `JOBPILOT_PORT` | Optional | `8000` | Server port (same caveat). |
| `JOBPILOT_LOG_LEVEL` | Optional | `info` | Honored by `backend/logging_config.py:185-192`. |
| `JOBPILOT_DATA_DIR` | Optional | `./data` | Resolved to absolute in `backend/config.py:135-137`. |
| `JOBPILOT_SCRAPER_HEADLESS` | Optional | `true` | Playwright/browser-use headless toggle. |
| `JOBPILOT_ALLOWED_ORIGINS` | Optional | local-dev allow-list | CORS allow-list (`backend/main.py:259`). |
| `GOOGLE_MODEL` | Optional | `gemini-3-flash-preview` | Primary Gemini model id. |
| `GOOGLE_MODEL_FALLBACKS` | Optional | `""` | Comma-separated fallback model ids. |
| `SCRAPLING_ENABLED` | Optional | `true` | Feature flag — Tier-1 Scrapling fetcher. |
| `APPLY_TIER1_ENABLED` | Optional | `true` | Feature flag — Tier-1 Playwright filler. |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT_URI` | Optional (blank disables) | empty / OAuth callback URL | Gmail OAuth wiring (`backend/config.py:47-52`). |
| `GMAIL_BACKFILL_DAYS` | Optional | `30` | Initial Gmail back-fill window. |
| `GMAIL_POLL_INTERVAL_MINUTES` | Optional | `5` | APScheduler poll cadence (`backend/main.py:235`). |

One env var declared in code but **not in `.env.example`**: `SERPAPI_KEY` (`backend/config.py:22`). Defaults to empty; only referenced in trimmed-down code paths.

`.env` itself is in the repo (the file `ls` showed at root, 405 bytes) — **but it's `.gitignore`d at line 2** of `.gitignore`, so the working copy is local-only. The repo's committed `.env` shown in this audit is therefore a developer's local file, not a tracked artifact.

### CREDENTIAL_KEY rotation story

There isn't one. `backend/config.py:115-131` generates a Fernet key once on first launch, writes it to `.env`, and that key is then the *only* decryption key for every `SiteCredential` row and every Gmail refresh token. The lifecycle is:

- **Generated**: on first import of `backend.config` when blank.
- **Persisted**: rewritten into `.env` in place (`backend/config.py:122-129`).
- **Rotated**: never. There is no command, no scheduled rotation, no documentation pointing at the problem.
- **Lost**: every encrypted column becomes unrecoverable. Site credentials and Gmail refresh tokens must be re-entered / re-authorised.

This is documented neither in `.env.example` nor in `README.md`. The closest acknowledgment is the changelog's PR-2 "SecretStr migration" (`CHANGELOG.md:166-171`) and the Gmail Phase 1 entry's "Fernet-encrypted at rest with `CREDENTIAL_KEY`" line (`CHANGELOG.md:18`) — neither warns the user.

---

## 6. Migrations operationally — dual-track wiring

JobPilot has **two parallel migration mechanisms** running on top of the same SQLite DB.

### Track A — Alembic (canonical, defined but not auto-invoked)

`alembic.ini:8` sets `script_location = %(here)s/alembic`. `alembic.ini:89` hard-codes `sqlalchemy.url = sqlite+aiosqlite:///data/jobpilot.db` — note the **relative path**, which means `alembic upgrade head` must be run from the project root.

`alembic/env.py:6` imports `Base.metadata` from `backend.models`, so autogenerate picks up every model declared in that package. `alembic/env.py:28-36` runs migrations under an async engine using `NullPool` — appropriate for a one-shot CLI.

Four migrations exist in `alembic/versions/`:
- `071b973b48b2_initial_schema.py` (2026-02-28) — full initial schema.
- `df6eea4756c3_add_site_credentials_table.py` (2026-03-03) — adds `site_credentials`.
- `41441908fc29_add_initial_indexes.py` (2026-05-22) — adds 14 indexes (PR-4, see `CHANGELOG.md:181-185`).
- `e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py` (2026-05-23) — `UserProfile.last_dashboard_seen_at` column.

**No code in `backend/` ever invokes `alembic upgrade head`.** Neither `start.py`, `Dockerfile`, `docker-compose.yml`, nor `backend/main.py` runs it. Searching the codebase confirms only `_migrate_add_columns` and `init_db` references appear (see Track B). The README does not document a migration step.

### Track B — `init_db()` + `_migrate_add_columns()` (what actually runs)

This is the runtime that fires on every startup, in `backend/main.py:91-95`:

```
await init_db()
```

`backend/database.py:40-49` does two things:

1. `Base.metadata.create_all` — creates any missing tables from the SQLAlchemy declarative models. **This bypasses Alembic entirely.** If a fresh DB is brought up, SQLAlchemy creates the latest schema directly; Alembic never sees the database and `alembic_version` is never written.
2. `_migrate_add_columns()` — `backend/database.py:72-94` runs four hand-coded `PRAGMA table_info` checks + `ALTER TABLE … ADD COLUMN` statements for columns that post-date the last Alembic migration the model schema diverged from (`search_settings.cv_tailoring_enabled`, `search_settings.max_results_per_source`, `search_settings.max_job_age_days`, `applications.last_correspondence_at`). These changes are **never reflected in Alembic versions/**.

This is "feature-creep migration" — every time a column is added to a model, the developer prepends a new tuple to the `migrations` list in `database.py:76-81` rather than running `alembic revision --autogenerate`. The result:

- **Fresh-DB path**: `create_all` → schema = current models. Works.
- **Long-lived DB path**: hits `_migrate_add_columns` to patch columns. Works for additive cases.
- **Index path**: indexes from `41441908fc29` are *never* applied unless Alembic is run manually. A user who deploys via `start.py` against a fresh DB gets a schema with **all tables but no indexes** (since `create_all` doesn't apply Alembic-only DDL).
- **`alembic_version` table**: not present in DBs initialised via `init_db()`. Running `alembic upgrade head` later would attempt to re-apply all four migrations and fail on the duplicate `create_table` calls.

The dual track is itself a critical risk — see §13.

---

## 7. Scripts — `scripts/` and `bin/`

`scripts/` contains three files plus a `defaults/templates/` seed directory.

| Path | Purpose |
| --- | --- |
| [`scripts/install.sh`](../../../scripts/install.sh) (234 lines, bash) | Linux/macOS installer. 9-step idempotent flow: install `uv`, ensure Node 18+, `uv sync`, install Playwright + Patchright Chromium (with-deps on Linux, without on macOS), download Tectonic via `scripts/download_tectonic.py`, build the frontend, mkdir `data/*`, copy `.env.example → .env`, generate `start-jobpilot.sh` plus a Desktop launcher (`.command` on macOS, `.desktop` on Linux). Sets `set -euo pipefail`. Does NOT require sudo. |
| [`scripts/install.ps1`](../../../scripts/install.ps1) (281 lines, PowerShell 5.1+) | Windows installer. Mirrors `install.sh` step-for-step. Prefers `winget` for Tectonic before falling back to the binary download. Generates `start-jobpilot.ps1`, `Start JobPilot.bat`, and a Desktop shortcut. |
| [`scripts/download_tectonic.py`](../../../scripts/download_tectonic.py) (210 lines) | Stand-alone platform-detecting downloader. Hits the GitHub releases API for `tectonic-typesetting/tectonic@latest`, picks the right asset for the local arch (`x86_64`/`aarch64` × `linux-musl`/`apple-darwin`/`pc-windows-msvc`), extracts tar.gz or zip, chmod +x on Unix. Idempotent — skips if `bin/tectonic[.exe]` already runs `--version` cleanly. Note: the Dockerfile **does not use this script** — `Dockerfile:32-49` is its own download path with a pinned version. |
| `scripts/defaults/templates/example_cv.tex`, `resume.cls` | Seed CV template files copied into `data/templates/` by the installers when that directory is empty. |

`bin/` contains exactly one file: `bin/tectonic` (35.5 MB, the downloaded LaTeX binary). It is gitignored (`.gitignore:28` — `bin/`).

There are no maintenance/operational scripts (no `backup.sh`, no `migrate.sh`, no `seed.py`, no `reset_db.py`). Migration is invoked by app startup; backup is unimplemented.

---

## 8. Data directory

`data/` (`.gitignore:27`) holds:

```
data/
  jobpilot.db          # SQLite, the entire app state
  cvs/                 # generated, per-application tailored CVs (PDF/TeX)
  letters/             # generated cover letters
  templates/           # user-uploaded base CV + resume.cls + Photo
  browser_sessions/    # Playwright storage_state JSON per site
  browser_profiles/    # Playwright user-data-dir per site
  logs/                # jobpilot.log + 5 rotated backups
```

The committed snapshot shows the dir already populated with what looks like generated content (per-job subdirs under `data/cvs/`, e.g. `1_swe/`, `28_développeur_django_python_h_f/`), a 931-byte `jobpilot.log`, and a 112 KB `jobpilot.db`. This is a developer's working copy — none of it should ship.

Paths consumed by the app:
- DB URL: `sqlite+aiosqlite:///{settings.jobpilot_data_dir}/jobpilot.db` (`backend/database.py:22-23`).
- Logs: `<DATA_DIR>/logs/jobpilot.log` (`backend/logging_config.py:218-220`).
- Compose mount: `./data:/app/data` (`docker-compose.yml:48-49`).

`data/` is **the only persistence boundary in the entire system** — kill it and JobPilot is back to a fresh install.

---

## 9. Logging in production

`backend/logging_config.py` (239 lines) is the single source of truth.

- **Two handlers, both with `JSONFormatter`** (`backend/logging_config.py:63-116`): one StreamHandler on `sys.stderr` (`backend/logging_config.py:208-213`), one `RotatingFileHandler` on `<DATA_DIR>/logs/jobpilot.log` (`backend/logging_config.py:216-231`). Both sentinelled with `_jobpilot_managed` (`backend/logging_config.py:31`) so `configure_logging()` is idempotent (re-runs strip-and-replace existing handlers — `backend/logging_config.py:152-160`).
- **Rotation**: `maxBytes=10 * 1024 * 1024` (10 MiB), `backupCount=5` → 60 MiB ceiling per process (`backend/logging_config.py:223-227`).
- **Level**: `settings.jobpilot_log_level` (default `info`), parsed case-insensitive (`backend/logging_config.py:119-134`).
- **Format**: one JSON object per line — `ts`, `level`, `logger`, `msg`, `module`, `line`, optional `extra`, `exc_info`, `stack_info` (`backend/logging_config.py:80-116`). Suitable for Loki/ELK/CloudWatch.
- **Called from**: `backend/main.py:74` in the FastAPI lifespan (before any other work). Never re-called outside that path.

Uvicorn's own loggers (`uvicorn.access`, `uvicorn.error`) are not explicitly reconfigured but inherit from the root logger, which has the JSON handlers attached.

---

## 10. Backup / restore

**There is no backup mechanism.** No `pg_dump` analogue, no `sqlite3 .backup`, no rsync target, no cron job, no S3 snapshot, no `data/` archive utility. The `data/` directory and the SQLite WAL files (`*.db-wal`, `*.db-shm`) are gitignored (`.gitignore:30-32`).

A user who loses `data/jobpilot.db` loses:
- Every match, application, follow-up, event, and tailored document.
- Every encrypted credential (recoverable only by re-entering — and only if `CREDENTIAL_KEY` is still in `.env`).
- Every Gmail refresh token (recoverable by re-authorising OAuth).

In container deployments the `./data:/app/data` bind mount survives container restarts but not host loss. There is no documented backup procedure in the README, the changelog, or any script.

---

## 11. Observability

| Surface | Present? | Where |
| --- | --- | --- |
| Health endpoint | Yes | `backend/main.py:302-359` — `GET /api/health`. Real DB ping (`SELECT 1`), tectonic check, gemini-key set flag. Returns 200/`ok` or 503/`degraded` based on DB. |
| Structured logs | Yes | JSON via `backend/logging_config.py`. |
| Log rotation | Yes | 10 MiB × 5 backups. |
| Metrics endpoint | **No** | No `/metrics`, no Prometheus exporter, no statsd, no OpenTelemetry. |
| Traces | **No** | No OTel SDK in `pyproject.toml:6-23`. |
| Request access log | Implicit | Uvicorn default access log, JSON-formatted via the root handler. |
| Error reporting | **No** | No Sentry, no Bugsnag. Errors land in `jobpilot.log`. |
| Healthcheck consumer | Docker `HEALTHCHECK` | `Dockerfile:94-95`, `docker-compose.yml:50-55`. |

The health endpoint is the only structured probe. Everything else has to be derived from log scraping.

---

## 12. Release process

`CHANGELOG.md` is 260 lines, hand-written, loosely follows Keep a Changelog (`CHANGELOG.md:3`). The project explicitly states it "does not yet ship versioned releases" — entries are grouped **by sprint**, dated by merge to `main`.

Structure: each top-level `##` is a sprint, with sub-sections per PR. Most PRs link the changed files via markdown URLs. Each sprint includes:
- "Scope" — what landed
- "Outcomes" — pytest counts, pyright counts, frontend check, deploy readiness
- Per-PR breakdown
- Sometimes a "Known follow-ups" / "Not in scope" block

**Latest entry (`CHANGELOG.md:7-37`)** — **2026-05-23 Gmail Phase 1 sprint (`gm-1 .. gm-12`)**: 12 PR-style commits on the `gm-phase-1` branch (current HEAD). Outcomes: 401 passed / 0 failed / 7 skipped (was 357/0/7), one new dep `apscheduler>=3.10`. Adds OAuth 2.0 (gmail.readonly), three new tables, polling sync every 5 min via APScheduler, heuristic classifier, REST + WS endpoints, frontend Gmail Connect card + `/inbox` page. The same changelog block notes the new APScheduler is "the first real `AsyncIOScheduler.start()` in the repo" (`CHANGELOG.md:15`) — PR-1 had earlier ripped out aspirational APScheduler scaffolding (`CHANGELOG.md:160-163`).

`pyproject.toml:2` declares `version = "0.1.0"`. No git tags exist matching that version; no PyPI release; no GitHub Release artifact. The "release" is whatever sits at `main`'s HEAD.

---

## 13. Critique (severity-tagged)

### CRIT-OPS-1 — Migration story is dangerously dual-track (severity: HIGH)
Alembic is configured but never invoked by any deploy path. `init_db` calls `Base.metadata.create_all` plus a hand-rolled `_migrate_add_columns` (`backend/database.py:40-94`). A fresh DB therefore has **all tables but none of the Alembic-only DDL** — including the 14 indexes from `41441908fc29` (`alembic/versions/41441908fc29_add_initial_indexes.py:26-75`). Long-running prod DBs that *do* have those indexes (because someone ran `alembic upgrade head` manually once) have no `alembic_version` row, so the next `alembic upgrade head` would attempt to re-create the same tables and crash. The two tracks have diverged: the latest column addition (`applications.last_correspondence_at` from Gmail Phase 1) only exists in `_migrate_add_columns`, with no corresponding Alembic revision. Pick one and delete the other.

### CRIT-OPS-2 — `CREDENTIAL_KEY` loss is unrecoverable and undocumented (severity: HIGH)
`backend/config.py:115-131` generates a Fernet key once, writes it to `.env`, and that's the only copy. Losing `.env` (the file is gitignored, the deploy story is "copy `.env.example`") loses every site credential and Gmail refresh token. Nothing in `README.md`, `.env.example`, or `CHANGELOG.md` warns the user. Rotation is impossible without a re-encrypt step that doesn't exist. Document at minimum; ideally provide a `scripts/rotate_credential_key.py` that decrypts with the old key and re-encrypts with the new.

### CRIT-OPS-3 — Local-first claim vs Docker reality is unstated (severity: MEDIUM)
README sells "runs on your own computer" (`README.md:3`) and `start.py` binds loopback only (`start.py:116`), opens a browser tab, and assumes a desktop session (`scripts/install.sh:188-208` writes `.desktop`/`.command` shortcuts). The Dockerfile is production-grade (non-root user, healthcheck, multi-stage, env override) and `docker-compose.yml:6` even tells the user to "browse to http://<host>:8000" — i.e. cross-host. The README never mentions Docker; the Dockerfile never mentions the user-facing launcher. Either declare which is canonical (and demote the other to a contrib note) or document both in the README with a clear decision tree.

### CRIT-OPS-4 — No backup mechanism for SQLite (severity: MEDIUM)
A single-file SQLite DB with no `.backup` invocation, no rsync target, no documented snapshot. The bind-mount in `docker-compose.yml:48` is the only persistence boundary. Add a `scripts/backup.sh` that does `sqlite3 data/jobpilot.db ".backup data/backups/$(date +%F).db"` and a corresponding restore note.

### CRIT-OPS-5 — APScheduler will fire twice in a multi-instance deploy (severity: MEDIUM)
`backend/main.py:232-238` starts an `AsyncIOScheduler` *inside the FastAPI lifespan*, once per process. Run two uvicorn workers (or two compose replicas) and Gmail will poll twice as often. The same applies to `backend/applier/follow_up.scan_overdue` (`backend/main.py:177-184`), which runs at startup. Neither has any leader-election or DB lock. The docker-compose has a single instance so this is currently latent, but the moment someone scales the deployment it'll fire twice.

### CRIT-OPS-6 — `start.py` orphans uvicorn on Ctrl-C in some cases (severity: LOW)
`free_port(8000)` (`start.py:39-97`) will kill any prior orphan, masking the symptom rather than fixing the root cause. The browser-tab `threading.Timer` (`start.py:124-126`) is not cancelled on interrupt — minor, but signals lack of lifecycle discipline.

### CRIT-OPS-7 — `start.py` ignores its own env vars (severity: LOW)
`JOBPILOT_HOST` and `JOBPILOT_PORT` are read by `backend/config.py:26-27` but `start.py:116-117` hard-codes `127.0.0.1:8000`. A user who edits `.env` to change the port has no effect via the launcher. Either read `settings.jobpilot_host/port` or remove the unused env vars.

### CRIT-OPS-8 — Healthcheck does not validate Tectonic, Gemini, or Adzuna (severity: LOW)
`/api/health` (`backend/main.py:302-359`) returns `degraded` only when the DB ping fails. Tectonic absence yields `tectonic: false` + a hint but **does not change overall status**. A container with broken Tectonic still reports healthy, will accept apply requests, and crash inside the LaTeX pipeline. Same for `gemini_key_set: false` (which would mean every Gemini call returns auth error). Consider tying these to `degraded`.

### CRIT-OPS-9 — Log volume in a long-running deploy is unbounded across containers (severity: LOW)
60 MiB ceiling on the file handler is fine for a single host. But Docker's own json-file driver also captures stderr (`backend/logging_config.py:208-213` writes to stderr). Without `logging.driver`/`logging.options.max-size` in `docker-compose.yml`, the Docker JSON log can grow without bound. Add:
```
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```
to the compose service.

### CRIT-OPS-10 — No metrics endpoint (severity: LOW)
For a "personal tool" this is acceptable. For the multi-tenant/SaaS direction the changelog occasionally hints at (e.g. "production deployments override via `JOBPILOT_ALLOWED_ORIGINS`" — `backend/config.py:33-35`), zero Prometheus / OTel coverage is a gap. The single `/api/health` endpoint can answer "is it up" but not "is it slow" or "is Gemini rate-limiting us".

### CRIT-OPS-11 — `.env` committed at HEAD looks real (severity: HIGH if leaked)
The working copy of `.env` examined here contains what looks like real-looking values for `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, and a real Fernet `CREDENTIAL_KEY`. The file is correctly gitignored (`.gitignore:2`) and the audit confirmed it is not tracked. But it's worth saying explicitly: the comment in the file claims "Placeholder .env so imports succeed in CI" — the values do not look like placeholders. If those keys are live, they should be rotated immediately. Add a pre-commit hook that rejects any commit attempting to add `.env`.

### CRIT-OPS-12 — Docker layer caching could be tighter (severity: LOW)
`Dockerfile:80-82` copies `backend/`, `alembic/`, `alembic.ini start.py pyproject.toml` in three separate `COPY` instructions. Each invalidates the layer below it whenever anything in those paths changes — but ordering is reasonable (least → most-frequently-changing). The bigger miss is that `pyproject.toml` is COPYd twice (stage 1 and again into runtime at line 81); pyproject doesn't need to be in the runtime image at all unless something at runtime reads it (it doesn't — `backend/main.py:351` hard-codes `"0.1.0"`). Drop it.

### CRIT-OPS-13 — Image size dominated by Chromium with no slimming (severity: LOW)
Playwright Chromium is ~280 MB. For a tool that primarily scrapes Adzuna via HTTP and only falls back to Chromium for Tier-2/3 sites, consider a `JOBPILOT_BROWSER_ENABLED` flag that lets users build a `-slim` variant skipping the `playwright install` step. The Dockerfile's `|| true` on `Dockerfile:90` already tolerates failure.

### CRIT-OPS-14 — README onboarding vs reality drift (severity: MEDIUM)
- `README.md:24-31` lists "Git + Node" as the prereqs. Neither `tectonic` nor `uv` is mentioned in the prereqs table, even though they're prereqs that the installer happens to *install for you*. Worth noting "what gets installed" before "what you need installed".
- `README.md:281-286` documents the layout but never mentions `scripts/`, `alembic/`, `Dockerfile`, `docker-compose.yml`, or `bin/`.
- The README never mentions Docker or `docker compose up -d --build` (the literal one-liner in `docker-compose.yml:5`).
- The README assumes Desktop shortcuts exist (`README.md:130-134`); the installer creates them only when `$HOME/Desktop` exists (`scripts/install.sh:184-211`). Headless servers silently skip this.
- The README has no migration step. Users who upgrade across minor versions will rely entirely on `_migrate_add_columns` running on startup; index changes (Alembic-only) will silently not apply.

### CRIT-OPS-15 — Stale / broken scripts (severity: LOW)
- `scripts/install.sh:73-92` numbering: "Step 5" appears as a comment between Step 3 and what the script calls "4/9" — the comment-step numbers (`Step 1` … `Step 9`) and the printed step labels (`1/9` … `9/9`) drifted apart. Same in `install.ps1`. Cosmetic.
- `scripts/install.sh:148-151` and `install.ps1:210-216` both reference `data/templates/example_cv.tex` plus an unspecified `Photo.jpeg`, while `scripts/defaults/templates/` only contains `example_cv.tex` and `resume.cls` — no `Photo.jpeg`. The `Photo.jpeg` reference in the info line is misleading.
- `scripts/download_tectonic.py:48-54` returns the same string for `aarch64` and "else" cases on macOS — dead branch.
- The Dockerfile installs Tectonic 0.15.0 pinned (`Dockerfile:39`); `scripts/download_tectonic.py` hits "latest" via the GitHub API (`scripts/download_tectonic.py:29`). The two paths can end up with different Tectonic versions on the same project.

---

## 14. Inventory

| Path | One-line summary |
| --- | --- |
| [`start.py`](../../../start.py) | Local launcher: prereq checks, mkdir data dirs, kill anything on :8000, delayed `webbrowser.open`, `uvicorn.run` on `127.0.0.1:8000`. 140 LOC. Registered as `[project.scripts] jobpilot`. |
| [`Dockerfile`](../../../Dockerfile) | 4-stage build (python-builder via uv 0.5.11 → frontend-builder via Node 20 → tectonic-fetcher pinned 0.15.0 → runtime). Non-root `jobpilot` user, EXPOSE 8000, healthcheck on `/api/health`. CMD bypasses `start.py`. |
| [`docker-compose.yml`](../../../docker-compose.yml) | Single `jobpilot` service; `restart: unless-stopped`; bind-mounts `./data:/app/data`; forces 0.0.0.0 binding + tighter CORS allow-list; healthcheck duplicated from Dockerfile. Postgres swap-in commented out. |
| [`.dockerignore`](../../../.dockerignore) | Excludes `.venv`, `__pycache__`, `node_modules`, `frontend/build`, `data/`, `bin/tectonic[.exe]`, `.env`, `docs/`, `tests/`, IDE/OS junk. Correct for a from-scratch image build. |
| [`.env.example`](../../../.env.example) | 15 documented variables. Required: `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`. Auto-generated: `CREDENTIAL_KEY`. Rest defaulted in `backend/config.py`. |
| [`.env`](../../../.env) | Local working copy (gitignored). Currently contains what look like real keys despite a "placeholder" comment — see CRIT-OPS-11. |
| [`.gitignore`](../../../.gitignore) | Standard Python/Node/IDE/OS plus `.env*`, `data/`, `bin/`, `*.db*`, `.claude/`, `.sisyphus/`, `.worktrees/`. |
| [`pyproject.toml`](../../../pyproject.toml) | Build/deps. Python ≥3.12, 16 runtime deps (FastAPI, uvicorn, SQLAlchemy[asyncio], aiosqlite, browser-use, google-generativeai, alembic, scrapling[fetchers], apscheduler, …). Dev deps: pytest, pytest-asyncio, pytest-cov, ruff, pyright. `[project.scripts] jobpilot = "start:main"`. |
| [`uv.lock`](../../../uv.lock) | 881 KB resolved lockfile (not parsed). Authoritative dep pins. Referenced by `Dockerfile:21-22` (`uv sync --frozen`). |
| [`alembic.ini`](../../../alembic.ini) | Script location `%(here)s/alembic`; URL `sqlite+aiosqlite:///data/jobpilot.db` (relative — must run from project root); stdlib logging config; no post-write hooks. |
| [`alembic/env.py`](../../../alembic/env.py) | Async-engine wiring; imports `Base.metadata` from `backend.models`; uses `NullPool`; falls through to `run_migrations_online()`. 46 LOC. |
| [`alembic/script.py.mako`](../../../alembic/script.py.mako) | Vanilla Alembic template, unmodified. |
| [`alembic/versions/071b973b48b2_initial_schema.py`](../../../alembic/versions/071b973b48b2_initial_schema.py) | Full initial schema (9 tables: jobs, job_sources, job_matches, applications, application_events, browser_sessions, search_settings, tailored_documents, user_profile). |
| [`alembic/versions/df6eea4756c3_add_site_credentials_table.py`](../../../alembic/versions/df6eea4756c3_add_site_credentials_table.py) | Adds `site_credentials` table (encrypted email/password columns). |
| [`alembic/versions/41441908fc29_add_initial_indexes.py`](../../../alembic/versions/41441908fc29_add_initial_indexes.py) | 14 indexes (FK-like columns + filtered/sorted columns). Never auto-applied at runtime. |
| [`alembic/versions/e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py`](../../../alembic/versions/e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py) | Adds nullable `user_profile.last_dashboard_seen_at` column. |
| [`alembic/README`](../../../alembic/README) | One-liner "Generic single-database configuration." |
| [`scripts/install.sh`](../../../scripts/install.sh) | Linux/macOS installer; 9 idempotent steps; no sudo. Generates `start-jobpilot.sh` + Desktop launcher. |
| [`scripts/install.ps1`](../../../scripts/install.ps1) | Windows installer; mirrors `install.sh`; prefers winget for Tectonic. Generates `.bat`/`.ps1` launchers + Desktop shortcut. |
| [`scripts/download_tectonic.py`](../../../scripts/download_tectonic.py) | Stand-alone Tectonic downloader; queries GitHub releases for "latest"; arch/OS detection; tar.gz + zip extraction; idempotent. |
| `scripts/defaults/templates/example_cv.tex`, `resume.cls` | Seed CV template files copied to `data/templates/` on first install. |
| `bin/tectonic` | The 35.5 MB downloaded LaTeX binary. Gitignored. Discovered by `start.py:36`. |
| `data/` | The single persistence boundary: `jobpilot.db`, `logs/jobpilot.log`, `cvs/`, `letters/`, `templates/`, `browser_sessions/`, `browser_profiles/`. Gitignored. |
| [`backend/logging_config.py`](../../../backend/logging_config.py) | JSON formatter + StreamHandler(stderr) + RotatingFileHandler(`<DATA_DIR>/logs/jobpilot.log`, 10 MiB × 5). Idempotent via `_jobpilot_managed` sentinel. Wired from `backend/main.py:74`. |
| [`README.md`](../../../README.md) | User-facing onboarding: prereqs (Git + Node), installer command, API-key sourcing, launcher options. Never mentions Docker, Alembic, or backup. |
| [`CHANGELOG.md`](../../../CHANGELOG.md) | Sprint-grouped manual changelog, 260 lines. Latest entry: 2026-05-23 Gmail Phase 1 (gm-1 .. gm-12). |
