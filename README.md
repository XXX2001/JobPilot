# JobPilot

<!-- Repo slug detected from the git remote (github.com/XXX2001/JobPilot). Update OWNER/REPO if the repository moves. -->
[![CI](https://github.com/XXX2001/JobPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/XXX2001/JobPilot/actions/workflows/ci.yml)

**A self-hostable, provider-agnostic AI assistant for the entire job-application cycle — discover, score, tailor, and apply, all on your own machine.**

JobPilot is a single-user web app that discovers job listings from the sources you enable, scores how well each one fits your profile, tailors your LaTeX CV and cover letter for every match using the AI provider *you* choose (any OpenAI-compatible endpoint, a local model, or Anthropic), and walks you through applying — manually, semi-automatically, or fully automatically. It runs as one process: a FastAPI backend serves both the REST/WebSocket API and the compiled SvelteKit frontend, backed by a single SQLite database and the Tectonic LaTeX compiler. No authentication layer, no mandatory cloud — the only data that leaves your computer goes to the AI and job-search APIs you configure yourself.

---

## What it does

- **First-run onboarding wizard** (`/onboarding`) — guides you through API keys, CV upload, keywords, and your first batch.
- **Job discovery** from API sources (Adzuna) and browser-scraped boards (LinkedIn, Indeed, Glassdoor, Welcome to the Jungle, Google Jobs) plus custom "lab" URLs, via a cheap-first tiered scraper.
- **Relevance scoring** with a weighted keyword + recency model, and a **"Why this score"** breakdown on each job's detail page.
- **Semantic fit scoring** — parses your CV and each job into embedded skill profiles, compares them with criticality-weighted cosine similarity, and decides whether a CV even needs tailoring (skipping unnecessary LLM calls).
- **AI CV tailoring** — surgical, safety-gated LaTeX replacements compiled to PDF via Tectonic.
- **AI cover letters** — marker-delimited paragraph editing, viewable and regenerable.
- **On-demand batch runs** with a **dry-run preview** (scrape + match only, nothing written to the database).
- **Queue review** with three apply modes — **auto**, **assisted**, **manual** — driven by an explicit application state machine, with pre-submit editing of mis-filled form fields and a human confirm step over WebSocket.
- **CV editor** (`/cv`) and **Letters view** (`/letters`) with one-click regeneration, plus a **template compile-test** button in Settings → Profile.
- **Application tracker** (`/tracker`) for following each application's status, with CSV export and follow-up reminders.
- **Gmail integration** (optional) — connect a mailbox read-only to surface and classify application-related correspondence.
- **Provider-agnostic AI** — generation, embeddings, and the browser agent each select a provider independently in `.env`; switch any role without touching code.

Everything stays on your machine. The only data leaving your computer goes to the AI and job-search APIs you configure.

---

## Architecture overview

JobPilot is one FastAPI process. The browser talks to it over REST and a single WebSocket; the backend orchestrates a set of subsystems that share one async SQLite database. Every AI-touching subsystem injects a provider-neutral client from the LLM factory rather than hard-binding to a vendor.

```text
                          ┌──────────────────────────────────────────────┐
                          │           SvelteKit SPA (frontend/)            │
                          │  Today · Queue · Tracker · Inbox · CV ·        │
                          │  Letters · Settings · Analytics · Onboarding   │
                          └───────────────┬───────────────┬──────────────┘
                                REST (apiFetch)        /ws (WebSocket)
                          ┌───────────────┴───────────────┴──────────────┐
                          │        FastAPI app  (backend/main.py)         │
                          │  api/ routers (jobs, queue, today, apps,      │
                          │  documents, settings, analytics, gmail, …)    │
                          │  ConnectionManager · global exception handlers│
                          └───┬───────┬───────┬───────┬───────┬───────┬───┘
                              │       │       │       │       │       │
        ┌──────────┐  ┌───────┴──┐ ┌──┴────┐ ┌┴──────┐ ┌┴─────────┐ ┌┴──────┐ ┌──────────┐
        │ scraping │  │ matching │ │applier│ │ latex │ │scheduler │ │ gmail │ │ analytics│
        │ (sources │  │ (rank +  │ │ (FSM, │ │ (CV / │ │  (Batch  │ │(OAuth,│ │          │
        │  + fit)  │  │  fit)    │ │ apply)│ │letter)│ │ Runner)  │ │ sync) │ │          │
        └────┬─────┘  └────┬─────┘ └───┬───┘ └───┬───┘ └────┬─────┘ └───┬───┘ └────┬─────┘
             │             │           │         │          │           │          │
             └─────────────┴───────────┴────┬────┴──────────┴───────────┴──────────┘
                                            │
                          ┌─────────────────┴──────────────────┐
                          │   LLM provider factory  (backend/llm/factory.py)   │
                          │   make_llm_client · make_embedding_client ·        │
                          │   make_browser_llm  → LLMClient / EmbeddingClient  │
                          │   Protocols (base.py) · OpenAI-compat /            │
                          │   Anthropic adapters, chosen per *_PROVIDER env    │
                          └─────────────────┬──────────────────┘
                                            │
                          ┌─────────────────┴──────────────────┐
                          │  SQLite (async aiosqlite, WAL)      │
                          │  backend/database.py + Alembic      │
                          │  jobs · matches · documents · apps  │
                          │  · events · profile · gmail tables  │
                          └────────────────────────────────────┘
```

- **Frontend → API/WS.** The SvelteKit SPA is built to static assets and served by FastAPI itself; it calls REST endpoints through one `apiFetch` helper and subscribes to a typed, discriminated-union WebSocket protocol for live updates (apply progress, sync status, new matches).
- **Subsystems.** `scraping` discovers and dedupes jobs; `matching` ranks them and computes semantic fit; `scheduler`'s `BatchRunner` orchestrates an end-to-end run; `latex` turns a base template plus a posting into a tailored PDF; `applier` drives a Reserved→…→Recording state machine across auto/assisted/manual strategies; `gmail` ingests and classifies correspondence read-only.
- **Provider factory.** `make_llm_client`, `make_embedding_client`, and `make_browser_llm` each read a matching `*_PROVIDER` env var and return a provider-neutral `LLMClient`/`EmbeddingClient`. Every consumer (CV editor/modifier, job analyzer, embedder, form filler, both appliers, scrapling fetcher, adaptive scraper) injects a Protocol, so the three AI roles — generation, embeddings, browser agent — can use different providers at once.
- **Persistence.** A single async SQLite database (WAL + enforced foreign keys), migrated by a linear Alembic chain on startup.

For the deep dive, see [Architecture](docs/architecture.md) and the per-subsystem docs in [Documentation](#documentation).

---

## Quickstart

Two steps for everyone: **pick an AI provider** (the setup script writes your `.env`), then **start the app**. A local/self-hosted model needs no cloud keys at all. Once running, open **http://localhost:8000** and complete the in-app onboarding wizard.

### Path A — Docker (recommended, incl. Windows & macOS via Docker Desktop)

**Requirements:** Docker Engine 20.10+ **and the Docker Compose v2 plugin** (the `docker compose` subcommand, not the legacy `docker-compose` v1 binary). Docker Desktop bundles it. On a Linux server that only has v1, install the plugin once:

```bash
sudo apt-get install docker-compose-plugin        # Debian/Ubuntu, OR:
mkdir -p ~/.docker/cli-plugins && curl -fsSL \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o ~/.docker/cli-plugins/docker-compose && chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version    # verify
```

Then:

```bash
# Linux / macOS
bash scripts/setup.sh
docker compose up -d --build
```

```powershell
# Windows (PowerShell)
.\scripts\setup.ps1
docker compose up -d --build
```

`setup.{sh,ps1}` asks which provider you want, writes a valid `.env`, and generates a persistent credential-encryption key. Then open **http://localhost:8000**. Your data persists in `./data` across container restarts.

> **Using a model on your own machine** (Ollama, llama.cpp, LM Studio…): from inside Docker it's reachable as `http://host.docker.internal:<port>/v1`, **not** `http://localhost`. The setup script defaults to this, and `docker-compose.yml` wires `host.docker.internal` so it resolves on Linux too.

### Path B — Local dev (uv)

**Prerequisites:** Python 3.12, Node.js 20.

```bash
uv sync                                         # install Python dependencies
uv run python scripts/download_tectonic.py      # download the Tectonic LaTeX compiler
cd frontend && npm ci && npm run build && cd ..  # build the web interface
bash scripts/setup.sh                           # pick a provider, writes .env
uv run python start.py                          # launch (opens http://localhost:8000)
```

`start.py` checks that the data directory, frontend build, and Tectonic binary are present, frees the port if needed, then starts the backend on `JOBPILOT_HOST:JOBPILOT_PORT` (default `127.0.0.1:8000`) and opens your browser.

### Choosing an AI provider

JobPilot is provider-agnostic — generation, embeddings, and the browser agent each pick a provider in `.env` (the setup script does this for you). On startup the app validates that the chosen provider has the credentials it needs (`Settings.validate_runtime_config`), and refuses to boot with a clear message if not.

| Provider | What you need | Notes |
| --- | --- | --- |
| **Local / self-hosted** (Ollama, llama.cpp, LM Studio, vLLM) | just an `LLM_BASE_URL` | No cloud key. Many local builds don't serve embeddings — fit-scoring then needs an OpenAI (or OpenAI-compatible) embedding key, or degrades gracefully. |
| **OpenAI** (default) | `OPENAI_API_KEY` (or `LLM_API_KEY`) | Covers all three roles — generation, embeddings, and the browser agent. Any OpenAI-compatible endpoint works too: set `LLM_BASE_URL` (e.g. DeepSeek, or any other vendor exposing an OpenAI-compatible endpoint). |
| **Anthropic** | `ANTHROPIC_API_KEY` | Generation only — no embeddings API, and browser-use ships no Anthropic client, so embeddings and the browser agent use OpenAI. |

The **Adzuna** job-search source is optional but recommended (`ADZUNA_APP_ID` / `ADZUNA_APP_KEY`, free at the [Adzuna developer portal](https://developer.adzuna.com/)). Without it, other sources and manual entry still work. Other optional integrations (SerpAPI fallback, Gmail) have their own keys — see `.env.example` and the [Configuration reference](docs/configuration.md).

---

## Documentation

Start here for whole-system context, then dive into the subsystem you care about.

- **[User guide](docs/user-guide.md)** — an end-to-end walkthrough of every feature, from onboarding to Gmail.
- **[Architecture](docs/architecture.md)** — system overview, component diagram, request lifecycles, database schema, and the [credentials & encryption](docs/architecture.md#credentials--encryption) reference.
- **[Custom CV templates](docs/custom-templates.md)** — how to bring your own LaTeX CV template.
- **[Contributing](CONTRIBUTING.md)** — dev setup and quality gates.

### Subsystem & reference docs

- [Multi-provider LLM](docs/llm-providers.md) — Documents JobPilot's provider-agnostic LLM layer: the LLMClient/EmbeddingClient Protocols and neutral exceptions in backend/llm/base.py, the three factory functions (make_llm_client / make_embedding_client / make_browser_llm) in backend/llm/factory.py, and the OpenAI-compat/Anthropic adapters. It explains how each of the three roles (generation, embeddings, browser agent) selects a provider via its *_PROVIDER env var, shows that all consumers (cv_editor, cv_modifier, job_analyzer, embedder, form_filler, both appliers, scrapling_fetcher, adaptive_scraper) inject a Protocol rather than hard-binding to a vendor, and covers provider-aware startup validation (Settings.validate_runtime_config) plus the cosine_similarity dimension guard. Includes a provider×role support matrix noting Anthropic has no embeddings API and browser-use ships no ChatAnthropic.
- [Configuration reference](docs/configuration.md) — Complete reference for every JobPilot setting in backend/config.py and .env.example: the three independent LLM roles (generation LLM_*, embeddings EMBEDDING_*, browser-agent BROWSER_LLM_*), all credentials (LLM_API_KEY/OPENAI/ANTHROPIC/ADZUNA/SERPAPI/CREDENTIAL_KEY/GMAIL_*), JOBPILOT_* app/server settings, model names, feature flags (SCRAPLING_ENABLED, APPLY_TIER1_ENABLED), and timeouts — each with env-var name, default, and required-vs-optional status. Explains CREDENTIAL_KEY Fernet auto-generation and the Docker non-persistence caveat, and documents the provider-aware startup rules in validate_runtime_config() that decide which credentials each chosen provider actually needs.
- [Deployment & install](docs/deployment.md) — Self-hosting and production guide for JobPilot covering the interactive setup scripts (setup.sh/setup.ps1), the optional full installers (install.sh/install.ps1), the four-stage Docker image (uv Python deps, Node frontend build, pinned Tectonic, slim non-root Playwright runtime), docker-compose hardening (1gb shm_size for Chromium, host.docker.internal mapping, json-file log rotation, healthcheck with 40s start_period), the Docker Compose v2 plugin requirement plus v1 fallback, the local-dev path (uv + Node + Tectonic + start.py), data persistence via ./data, and the CREDENTIAL_KEY-must-persist gotcha. Build + boot verified healthy.
- [Job scraping & sources](docs/scraping.md) — Documents JobPilot's scraping subsystem in backend/scraping/. The ScrapingOrchestrator runs a 3-phase batch pipeline (Phase 1 Adzuna API parallel, Phase 2 browser sources sequential with human-like delay, Phase 3 lab URLs parallel) then date-filters and dedupes. Phase 2 uses a cheap-first tiered strategy: Tier 1 ScraplingFetcher does an HTTP fetch (StealthyFetcher/Fetcher) plus a single LLM extraction call over cleaned-and-truncated HTML, falling back to Tier 2 AdaptiveScraper (a browser-use Agent, max_steps capped, 2 retries) for the five TIER1_SITES or any site Tier 1 can't handle. Both tiers are now provider-agnostic via backend/llm/factory.py (make_llm_client for Tier 1, make_browser_llm for Tier 2), and Tier 1 is gated by the SCRAPLING_ENABLED flag wired in main.py. Supporting pieces cover BrowserSessionManager (persistent per-site login state + manual/auto login), JobDeduplicator (md5 of normalized company|title|location), in-memory SourceHealthTracker (ok/empty/error → healthy/degraded/down pills), shared json_utils parsing/sanitization, and site_prompts (prompts, selectors, domain maps, SITE_CONFIGS).
- [Matching & fit scoring](docs/matching.md) — Documents backend/matching/: two scoring layers in JobPilot. JobMatcher (matcher.py) produces a 0-100 heuristic relevance score from JobDetails + JobFilters (keyword/location/experience/salary/recency weights, exclusion knockouts) to rank scraped jobs. The semantic fit layer parses a LaTeX CV into a CVProfile (cv_parser.py, context-weighted SkillEntry) and a job description into a JobProfile (job_skill_extractor.py, criticality-scored JobSkill + knockouts), embeds both via the provider-agnostic Embedder over an EmbeddingClient, then FitEngine.assess (fit_engine.py) compares them with criticality-weighted cosine_similarity to yield a FitAssessment (severity, simulated ATS score, should_modify, gaps). Covers the cosine_similarity dimension guard, how embedding-provider choice (OpenAI 1536/3072, Anthropic rejected) sets vector dimensions and why CV and job must use the same provider, and the end-to-end scheduler orchestration in batch_runner.py.
- [Auto / assisted / manual apply](docs/applying.md) — Documents JobPilot's application engine in backend/applier/: how ApplicationEngine.apply routes requests to AUTO/ASSISTED/MANUAL strategies, drives a hand-rolled Statechart FSM through the Reserved→CaptchaCheck→Filling→AwaitingConfirm→Submitting→Recording lifecycle with four terminal/compensation states, enforces an atomic daily limit via DailyLimitGuard.reserve_slot, and pauses for a human apply_review confirm/cancel/patch step over WebSocket. Covers the two-tier auto/assisted strategies (Tier-1 PlaywrightFormFiller single-LLM-call vs Tier-2 provider-agnostic browser-use agent via make_browser_llm), captcha_handler block detection and session persistence, ApplicationRecorder placeholder-update semantics, and the lazy follow_up scanner.
- [CV & cover-letter generation](docs/documents.md) — Documents JobPilot's LLM document pipeline that turns a base LaTeX CV or cover-letter template plus a job posting into a tailored, Tectonic-compiled PDF. Covers the two pipelines in backend/latex/pipeline.py — CVPipeline (JobAnalyzer to CVModifier to CVApplicator, with an assessment-driven fast path and JobContext caching) and LetterPipeline (CVEditor to LaTeXInjector via JOBPILOT markers) — the structured-output validators, prompt design (prefix caching and injection defense), graceful degradation to the base document on LLM failure, Tectonic compilation with TECTONIC_TIMEOUT_SECONDS, and template resolution for bringing your own custom templates.
- [Gmail integration](docs/gmail.md) — Documents JobPilot's Phase 1 Gmail integration: the read-only OAuth flow (start/callback/disconnect with an HMAC-signed CSRF state keyed on CREDENTIAL_KEY), Fernet-encrypted refresh-token storage in the GmailCredential model, the in-memory GmailTokenManager access-token cache, the GmailSyncWorker (history_id-driven backfill vs delta sync with per-account locking and unique-constraint dedup), the deterministic heuristic classifier (noise/rejection/offer/interview_invite/ats_ack/unknown with domain-only ATS vendor matching), and the APScheduler poll cron (_run_gmail_poll) wired in main.py's lifespan. Covers all GMAIL_* settings and the HTTP API surface with real file:line citations.
- [HTTP & WebSocket API](docs/api-reference.md) — Reference for JobPilot's FastAPI HTTP surface and WebSocket protocol, derived from backend/api/ and backend/main.py. Enumerates all 11 router modules (jobs, queue, today, applications + applications_export, documents, settings, analytics, gmail_auth, gmail, correspondence) with per-module endpoint tables (method, path, response model, purpose), documents the minimal DI model (deps.py DBSession + app.state singletons read off request.app.state), the inline /api/health HealthOut schema with its 503-on-DB-failure behavior, the global exception handlers, and the /ws protocol (ConnectionManager dispatch, resume replay, and the full WSMessage server→client and ClientMessage client→server discriminated-union message types from ws_models.py).
- [Data model & migrations](docs/data-model.md) — Documents JobPilot's persistence layer: the SQLAlchemy 2.0 entities under backend/models/ (jobs, job_matches, tailored_documents, applications, application_events, user_profile/search_settings, gmail_credentials/messages/correspondence, site_credentials, browser_sessions), their foreign keys and CASCADE/SET-NULL semantics, plus CHECK enums, unique natural keys, and covering indexes. Covers backend/database.py (async aiosqlite engine, WAL + foreign_keys PRAGMAs, AsyncSessionLocal, and init_db running alembic upgrade head with a three-case legacy-stamp reconciliation) and the linear nine-revision Alembic chain ending at t2b4_indexes. Notes SQLite is the only runtime database and that Postgres exists solely as a commented-out future path in docker-compose.yml.
- [Frontend (Svelte)](docs/frontend.md) — Documents JobPilot's frontend/ SvelteKit 2 + Svelte 5 SPA: how it's built (npm ci && npm run build with adapter-static into frontend/build/) and served by FastAPI via the SPAStaticFiles mount in backend/main.py, the file-based routes (Today, Queue, Tracker, Inbox, CV, Letters, Settings, Analytics, Onboarding, jobs/[id]), the layout shell and key components, how it talks to the backend over REST (the single apiFetch helper) and the /ws WebSocket (singleton store with jittered reconnect and a typed discriminated-union protocol mirrored from backend/api/ws_models.py), and the four-step first-run onboarding wizard with its sessionStorage-guarded redirect gate.
- [Development & contributing](docs/development.md) — Developer workflow guide for JobPilot: top-level repo layout (backend/, frontend/, tests/, alembic/, scripts/, docs/), local setup via uv sync + download_tectonic + npm build, and running the app through start.py. Documents the pytest suite (asyncio_mode=auto, conftest.py per-worker SQLite isolation keyed off PYTEST_XDIST_WORKER, the integration marker with auto-skip smoke tests), the quality gates and baselines (pytest, pyright 19/8 ceiling, ruff, svelte-check 0/0, vitest) enforced by .github/workflows/ci.yml, and quality tooling (ruff E/F/I, ad-hoc vulture, the .codegraph index, the local pre-commit credential guard). Links CONTRIBUTING.md and the other docs.

---

## Project layout

```text
backend/
  api/         FastAPI routers, dependency injection, WebSocket (ConnectionManager + ws_models)
  applier/     Application engine: auto / assisted / manual strategies, Statechart FSM, daily-limit guard, recorder
  gmail/       Read-only Gmail OAuth, sync worker, heuristic classifier
  latex/       CV & cover-letter pipelines → Tectonic-compiled PDFs
  llm/         Provider-agnostic LLM layer: base Protocols, factory, OpenAI-compat/Anthropic adapters
  matching/    Heuristic relevance scoring + semantic fit (CV/job profiles, Embedder, FitEngine)
  models/      SQLAlchemy 2.0 entities
  scheduler/   BatchRunner — on-demand end-to-end batch pipeline
  scraping/    Job sources: orchestrator, tiered scraper, session manager, dedup, source health
  config.py    Settings + provider-aware startup validation
  database.py  Async aiosqlite engine, WAL/FK PRAGMAs, init_db → alembic upgrade head
  main.py      App factory, lifespan singletons, static SPA mount, health, exception handlers
frontend/      SvelteKit 2 + Svelte 5 SPA (built to static assets, served by FastAPI)
alembic/       Migration chain
scripts/       setup.{sh,ps1}, install.{sh,ps1}, download_tectonic.py, backup_db.py, utilities
tests/         pytest suite (per-worker SQLite isolation)
docs/          Architecture, subsystem, and reference docs
data/          Your data — SQLite DB, logs, templates, sessions (ignored by Git)
bin/           Downloaded tools, e.g. Tectonic (ignored by Git)
start.py       App launcher
```

---

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + aiosqlite, Alembic, APScheduler, Pydantic v2.
- **Frontend:** SvelteKit 2 + Svelte 5, `adapter-static`, TypeScript, Vite.
- **AI:** provider-agnostic via `backend/llm/` — any OpenAI-compatible endpoint (incl. DeepSeek, Ollama, LM Studio, vLLM) and Anthropic; browser automation via `browser-use` + Playwright/Chromium.
- **Documents:** LaTeX compiled to PDF by Tectonic.
- **Scraping:** Scrapling (Tier 1 HTTP fetch) + browser-use agent (Tier 2), Adzuna API, optional SerpAPI.
- **Runtime:** single process, SQLite (WAL), Docker (four-stage image) + docker-compose.

---

## Alternative install (guided installer scripts)

The repository also ships convenience installer scripts that bundle the local-dev steps above (install `uv` + Python, build the frontend, download Tectonic, create `.env`, and add a desktop shortcut). They are optional; the [Quickstart](#quickstart) paths above are the canonical setup. See [Deployment & install](docs/deployment.md) for the full breakdown.

<details>
<summary><strong>Linux / macOS</strong></summary>

```bash
bash scripts/install.sh
```

On macOS, if a security warning about Tectonic appears, run once: `xattr -d com.apple.quarantine ./bin/tectonic`.

</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
.\scripts\install.ps1
```

If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process Bypass` first.

</details>

---

## First-time setup (inside the app)

1. The onboarding wizard (`/onboarding`) will guide you through the basics
2. Upload your CV template (a `.tex` file)
3. Set your job search preferences (location, keywords, etc.)
4. Click **Refresh queue** to run your first job-discovery batch

For a full walkthrough of every feature, see the [user guide](docs/user-guide.md).

---

## Troubleshooting

### "Invalid LLM provider configuration" on startup

The app validates that your chosen provider has the credentials it needs and refuses to boot otherwise — the log lists exactly what's missing. Re-run `scripts/setup.sh` / `scripts/setup.ps1`, or fix the named keys in `.env` (see `.env.example` and the [Configuration reference](docs/configuration.md)).

### "docker: 'compose' is not a docker command"

Your Docker has only the legacy v1 binary. Either install the Compose v2 plugin (see [Path A requirements](#path-a--docker-recommended-incl-windows--macos-via-docker-desktop)), or use the v1 syntax as a fallback — the compose file is compatible: `docker-compose up -d --build` (note the hyphen).

### Docker can't reach my local model (Ollama / llama.cpp / LM Studio)

Inside a container, `localhost` is the container itself. Point `LLM_BASE_URL` (and `BROWSER_LLM_BASE_URL`) at `http://host.docker.internal:<port>/v1`. `docker-compose.yml` already maps `host.docker.internal`. If the model runs on another machine, use that machine's LAN IP, e.g. `http://192.168.1.50:8080/v1`.

### Stored credentials stop working after a Docker restart

`CREDENTIAL_KEY` must be set in `.env` (the setup script generates it). In a container the app can't persist a generated key, so without it each restart gets a new key and can't decrypt previously-saved credentials.

### "Node not found"

Make sure Node.js 20+ is installed and that its installer added it to your system PATH. Close and reopen your terminal after installing, then run the installer again. (Python is installed automatically by the installer -- you do not need to install it yourself.)

### The installer finished but something seems wrong

The installer is safe to run again -- it will skip anything already set up and only fix what is missing.

### macOS blocks Tectonic (security warning)

Run this command once from the project folder:

```bash
xattr -d com.apple.quarantine ./bin/tectonic
```

### Windows blocks the PowerShell script

Run this before the installer:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### PDF generation is not working

The PDF engine (Tectonic) may not have downloaded correctly. Re-run the installer, or visit [tectonic-typesetting.github.io](https://tectonic-typesetting.github.io/) to install it manually.

### The web interface does not load

Make sure the frontend was built. Run these two commands from the project folder:

```bash
npm install --prefix frontend
npm run build --prefix frontend
```

Then restart JobPilot.

### The app starts but nothing works properly

- Check that your `.env` file has valid API keys (no extra spaces around the `=` sign)
- Check that your CV template file exists and is accessible
- Try stopping and restarting the app

---

## Advanced configuration

The settings below are the most common; the [Configuration reference](docs/configuration.md) documents every key (env-var name, default, required-vs-optional) including all three LLM roles.

<details>
<summary><strong>Common .env settings</strong></summary>

```dotenv
# Required API keys
# Your LLM provider key. Use OPENAI_API_KEY / ANTHROPIC_API_KEY for those
# providers, or LLM_API_KEY generically. For a local/self-hosted model set
# LLM_BASE_URL instead (e.g. http://localhost:11434/v1) and no key is needed.
LLM_API_KEY=your_llm_api_key
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key

# Credential encryption (the installer generates this for you)
CREDENTIAL_KEY=

# App settings (defaults work for most users)
JOBPILOT_HOST=127.0.0.1
JOBPILOT_PORT=8000
JOBPILOT_LOG_LEVEL=info
JOBPILOT_DATA_DIR=./data
JOBPILOT_SCRAPER_HEADLESS=true
# Comma-separated CORS allow-list. Set this if you serve the UI from a host
# other than the local dev defaults (e.g. behind a reverse proxy).
JOBPILOT_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000

# AI model settings (optional — sensible defaults per provider)
LLM_MODEL=
LLM_TIMEOUT_SECONDS=60

# Feature flags
SCRAPLING_ENABLED=true
APPLY_TIER1_ENABLED=true
```

</details>

<details>
<summary><strong>Platform support</strong></summary>

| Platform | Status | Notes |
| --- | --- | --- |
| Linux | Fully supported | Primary development platform |
| macOS | Supported | Tectonic may need a one-time security approval |
| Windows | Supported | PowerShell execution policy may need adjustment |

</details>

<details>
<summary><strong>Development commands</strong></summary>

```bash
uv run pytest tests/ -q            # Run tests
uv run pyright backend/            # Type-check the backend
uv run ruff check backend/ tests/  # Lint the backend
npm run check --prefix frontend    # Check the frontend
npm run build --prefix frontend    # Rebuild the frontend
```

See [Development & contributing](docs/development.md) for the full workflow and quality gates.

</details>

<details>
<summary><strong>Backup & restore</strong></summary>

JobPilot keeps everything in a single SQLite database at
`data/jobpilot.db`. To take a hot, online snapshot while the app is
running:

```bash
uv run python scripts/backup_db.py
# → /…/data/backups/jobpilot-20260524T143000Z.db
```

The script uses SQLite's `VACUUM INTO` so it is safe to run while the
server is live (no shutdown required). By default the snapshot lands in
`data/backups/`; override with `--out /elsewhere` if you prefer.

To restore: stop the app, replace `data/jobpilot.db` with the snapshot
(and delete any `jobpilot.db-wal` / `jobpilot.db-shm` siblings), then
restart.

Your encryption key (`CREDENTIAL_KEY` in `.env`) is **not** part of the
DB backup — see [`docs/architecture.md` → "Credentials & encryption"](docs/architecture.md#credentials--encryption)
for how to back it up separately.

</details>

---

## License

MIT -- see [LICENSE](LICENSE)
