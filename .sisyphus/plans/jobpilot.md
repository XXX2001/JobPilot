# JobPilot — Full Implementation Work Plan

## TL;DR

> **Quick Summary**: Build JobPilot — a local AI-powered job application assistant that scrapes jobs from 8+ sources using browser-use + Gemini, tailors LaTeX CVs surgically, and applies hybrid auto-fill or manual open. Runs on Windows + Linux with a SvelteKit dashboard.
>
> **Deliverables**:
> - Full-stack Python (FastAPI) + SvelteKit web app running on localhost:8000
> - Adaptive scraping engine (Adzuna API + browser-use Gemini agents)
> - LaTeX CV/Letter tailoring pipeline (TexSoup + Gemini Flash + Tectonic)
> - Hybrid application engine (auto/assisted/manual via browser-use)
> - Morning batch scheduler with 10-app daily limit
> - Kanban application tracker + settings + analytics dashboard
> - Cross-platform installer (uv + install.sh/ps1 + Tectonic auto-download)
>
> **Estimated Effort**: XL (~155h original estimate + Phase 0 plumbing)
> **Parallel Execution**: YES — 5 waves, max 7 concurrent tasks
> **Critical Path**: Task 1 (config/scaffold) → Task 6 (DB models) → Task 8 (Gemini client) → Task 12 (LaTeX pipeline) → Task 16 (morning batch) → Task 22 (apply engine) → Final Verification

---

## Context

### Original Request
Build JobPilot from the JOBPILOT_PLAN.md specification — a full local AI-powered job application assistant with adaptive scraping, LaTeX CV tailoring, hybrid auto-apply, and a Notion/Linear-aesthetic dashboard. Cross-platform (Windows + Linux), zero-cost (free API tiers), local-first (SQLite, no cloud).

### Key Decisions from JOBPILOT_PLAN.md
- **LLM**: Google Gemini 2.0 Flash (free tier: 15 RPM / 1500 RPD)
- **Browser automation**: browser-use + Playwright (LLM-driven, no hardcoded selectors)
- **LaTeX**: TexSoup for parsing, Tectonic for compilation (bundled/auto-downloaded binary)
- **Frontend**: SvelteKit + shadcn-svelte + TailwindCSS (adapter-static, served by FastAPI)
- **DB**: SQLite + SQLAlchemy async (aiosqlite) + **Alembic migrations** (added by Metis)
- **Packaging**: uv (Astral), cross-platform install scripts
- **Job API**: Adzuna (250 free calls/day), browser scraping for other sites
- **Apply modes**: Auto (Easy Apply), Assisted (pre-fill + hand off), Manual (open URL)

### Metis Review — Gaps Addressed
- **Added Phase 0**: Project plumbing, config, DB setup, Alembic, test infrastructure, health endpoint
- **First-run setup moved to Phase 1**: API key input wizard before any feature works
- **chktex dropped**: Use Tectonic compilation itself as LaTeX validator (chktex is not cross-platform)
- **Gemini rate limiter added**: Hard 15 RPM token bucket in client, before any other work
- **max_steps added**: All browser-use Agents capped (15 for scraping, 25 for applying)
- **WebSocket protocol**: Typed Pydantic discriminated union defined before WS work starts
- **Alembic from Day 1**: Every schema change gets a migration, not ad-hoc DDL
- **Dependency versions pinned**: browser-use, google-generativeai, texsoup all pinned
- **adapter-static fallback**: `fallback: 'index.html'` + FastAPI catch-all route
- **LLM response validation**: All Gemini outputs validated against Pydantic schemas
- **Tectonic pre-warm**: Install script compiles a test doc to pre-cache LaTeX packages
- **SQLite WAL mode**: `PRAGMA journal_mode=WAL` on startup

---

## Work Objectives

### Core Objective
Implement JobPilot end-to-end: from project scaffolding to a working, installable local web app that scrapes jobs daily, tailors documents per application, and provides a polished review-and-apply workflow.

### Concrete Deliverables
- `backend/` — FastAPI app with all modules (scraping, matching, LaTeX, applier, scheduler, API routes, WebSocket)
- `frontend/` — SvelteKit app with 6 pages (Queue, Job Detail, Tracker, CV Manager, Settings, Analytics)
- `data/` — Runtime data directory (SQLite DB, PDFs, LaTeX, browser sessions)
- `bin/` — Tectonic binaries (auto-downloaded by install script)
- `scripts/install.sh` + `scripts/install.ps1` — One-command installers
- `start.py` + `pyproject.toml` — Cross-platform launcher
- `tests/` — pytest suite (backend unit + integration tests)

### Definition of Done
- [ ] `uv run python start.py` launches the app on localhost:8000 without errors on Linux
- [ ] `curl -s localhost:8000/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'"` passes
- [ ] Adzuna search returns jobs: `curl -s "localhost:8000/api/jobs/search?keywords=python" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d['jobs']) > 0"`
- [ ] LaTeX CV is compiled to PDF: test compile endpoint returns valid `.pdf` path
- [ ] Morning batch can be triggered manually via `POST /api/queue/refresh`
- [ ] `uv run pytest tests/ -v` shows ≥80% pass rate
- [ ] All 6 frontend pages render without console errors

### Must Have
- Adzuna API integration (structured job search)
- Gemini rate limiter (15 RPM hard cap)
- LaTeX pipeline with comment markers (extract → Gemini JSON diff → inject → Tectonic compile)
- Morning batch scheduler (APScheduler cron)
- Morning Queue UI with match scores + apply actions
- Hybrid apply engine (auto / assisted / manual)
- Application Kanban tracker
- Settings UI (keywords, filters, sources, API keys)
- SQLite DB with Alembic migrations
- Cross-platform installer (Linux + Windows)
- Tectonic auto-download on install
- WebSocket live updates (typed message protocol)
- First-run setup wizard (API key input)

### Must NOT Have (Guardrails)

**Functional Prohibitions**:
- NO chktex (not cross-platform — use Tectonic compilation as validator)
- NO raw LaTeX rewriting by LLM (JSON diff only; inject via TexSoup/markers)
- NO browser-use Agent running without `max_steps` (15 scraping, 25 applying)
- NO API keys stored in SQLite database (`.env` file only)
- NO absolute paths stored in the database (always relative `pathlib.Path`)
- NO parallel browser-use agents (sequential only to respect 15 RPM)
- NO LinkedIn automation in Phase 0 or 1 (Adzuna API only until core pipeline solid)
- NO Docker support before Phase 4 tasks are complete
- NO implementation of future features: analytics A/B testing, email tracking, multi-profile, Chrome extension, collaborative mode

**Code Quality Prohibitions (AI slop)**:
- NO `as any` or `@ts-ignore` in TypeScript/Svelte
- NO empty `except: pass` blocks in Python
- NO `console.log` in production Svelte/TS code
- NO `print()` in Python modules (use `logging` only)
- NO over-abstraction: don't create base classes for single implementations
- NO hardcoded CSS selectors in scraping (that defeats the entire architecture)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure**: pytest + pytest-asyncio (configured in pyproject.toml from Task 1)
- **Automated tests**: YES (Tests alongside implementation — not strict TDD but each module task includes its tests)
- **Framework**: pytest 8.0 + pytest-asyncio 0.24

### QA Policy
Every task has agent-executed QA scenarios:
- **API/Backend**: `curl` commands or `pytest` assertions
- **Frontend/UI**: Playwright browser automation (playwright skill)
- **CLI/install**: bash commands with exit code checks
- **LaTeX pipeline**: File existence + size assertions, PDF validation

Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Foundation — must complete before everything):
├── Task 1:  Project scaffold, pyproject.toml, pyproject config, env       [quick]
├── Task 2:  SQLAlchemy models + Alembic init + initial migration            [quick]
├── Task 3:  FastAPI skeleton (CORS, lifespan, health endpoint)              [quick]
├── Task 4:  SvelteKit init + shadcn-svelte + Tailwind + adapter-static      [visual-engineering]
├── Task 5:  Typed WebSocket message protocol (Pydantic models)              [quick]
└── Task 6:  pytest infrastructure + conftest.py + first smoke test          [quick]

Wave 1 (Core Services — parallelize after Wave 0):
├── Task 7:  Gemini client + rate limiter (15 RPM token bucket)              [quick]
├── Task 8:  Adzuna REST API client + RawJob schema                          [quick]
├── Task 9:  LaTeX section parser (TexSoup + comment markers)                [unspecified-high]
├── Task 10: SvelteKit app shell + dark mode + navigation layout             [visual-engineering]
└── Task 11: Database API layer (CRUD helpers, async session factory)        [quick]

Wave 2 (Pipelines — after Wave 1):
├── Task 12: LaTeX injector + Tectonic compiler + pipeline                   [unspecified-high]
├── Task 13: Gemini LaTeX editors (CV summary + experience + letter prompts) [unspecified-high]
├── Task 14: Job matching & scoring engine + deduplicator                    [unspecified-high]
├── Task 15: FastAPI routes: /api/jobs, /api/queue, /api/documents           [unspecified-high]
└── Task 16: WebSocket manager + FastAPI WS route (/ws)                      [quick]

Wave 3 (Scraping + Apply + Morning Batch — after Wave 2):
├── Task 17: Adaptive browser-use scraper (generic + Adzuna orchestrator)    [deep]
├── Task 18: Browser session manager (persistent login + UI flow)            [unspecified-high]
├── Task 19: Morning batch scheduler (APScheduler + orchestration)           [unspecified-high]
├── Task 20: Application engine (auto/assisted/manual + daily limit guard)   [deep]
└── Task 21: FastAPI routes: /api/applications, /api/settings, /api/analytics [unspecified-high]

Wave 4 (Frontend Pages — after Wave 3):
├── Task 22: Morning Queue page (job cards, score badges, action buttons)    [visual-engineering]
├── Task 23: Job Detail page (full description + CV diff + apply buttons)    [visual-engineering]
├── Task 24: Application Tracker page (Kanban drag-and-drop)                 [visual-engineering]
├── Task 25: Settings page (keywords, filters, sources, API keys, profile)   [visual-engineering]
├── Task 26: CV Manager page (upload LaTeX, preview PDF, edit history)       [visual-engineering]
└── Task 27: Analytics page + first-run setup wizard component               [visual-engineering]

Wave 5 (Packaging + Polish — after Wave 4):
├── Task 28: Cross-platform installer scripts (install.sh + install.ps1)     [unspecified-high]
├── Task 29: Tectonic auto-download script + bin/ directory setup            [quick]
├── Task 30: Site-specific browser-use prompts (LinkedIn, Indeed, Google)    [unspecified-high]
├── Task 31: Error handling, retry logic, graceful degradation throughout    [deep]
└── Task 32: Integration tests suite (backend API + pipeline end-to-end)     [deep]

Wave FINAL (Independent Review — all 4 parallel after Wave 5):
├── Task F1: Plan compliance audit                                            [oracle]
├── Task F2: Code quality review (ruff + pyright + TypeScript checks)        [unspecified-high]
├── Task F3: Full QA smoke test (all 6 frontend pages + all API endpoints)   [unspecified-high]
└── Task F4: Scope fidelity + security check                                  [deep]

Critical Path: T1 → T2 → T7 → T9 → T12 → T13 → T17 → T19 → T22 → T32 → F1-F4
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 6 (Wave 1)
```

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|-----------|
| Wave 0 | 6 | T1-T3,T5,T6 → `quick`; T4 → `visual-engineering` |
| Wave 1 | 5 | T7,T8,T11 → `quick`; T9 → `unspecified-high`; T10 → `visual-engineering` |
| Wave 2 | 5 | T12,T13,T14,T15 → `unspecified-high`; T16 → `quick` |
| Wave 3 | 5 | T17,T20 → `deep`; T18,T19,T21 → `unspecified-high` |
| Wave 4 | 6 | T22-T27 → `visual-engineering` |
| Wave 5 | 5 | T28,T30,T31,T32 → `unspecified-high`/`deep`; T29 → `quick` |
| Final | 4 | F1 → `oracle`; F2,F3 → `unspecified-high`; F4 → `deep` |

---

## TODOs

---

<!-- WAVE 0: Foundation -->

- [ ] 1. Project Scaffold — pyproject.toml, .env, config, directory structure

  **What to do**:
  - Create `pyproject.toml` with all dependencies from the JOBPILOT_PLAN.md tech stack section. Pin versions: `browser-use==0.2.*`, `google-generativeai==0.8.*`, `texsoup==0.3.*`. Include dev deps: `pytest>=8.0`, `pytest-asyncio>=0.24`, `ruff>=0.6`, `pyright>=1.1`.
  - Create `.env.example` with all required keys: `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOBPILOT_HOST=127.0.0.1`, `JOBPILOT_PORT=8000`, `JOBPILOT_LOG_LEVEL=info`, `JOBPILOT_DATA_DIR=./data`
  - Create `backend/config.py` using `pydantic-settings` to load from `.env` — settings class with all keys, with validation (raises on missing required keys)
  - Create `backend/__init__.py` and `backend/main.py` minimal FastAPI app (just startup + CORS + `/api/health` → `{"status": "ok", "version": "0.1.0"}`)
  - Create `start.py` launcher as documented in the plan (check prerequisites, ensure data dirs, open browser after 2s delay, uvicorn run)
  - Create `.gitignore` with: `.env`, `data/`, `__pycache__/`, `.venv/`, `frontend/build/`, `frontend/.svelte-kit/`, `bin/*.exe`, `*.pyc`
  - Create top-level directory structure: `backend/`, `frontend/`, `data/`, `bin/`, `scripts/`, `tests/`
  - Create `backend/models/__init__.py`, `backend/api/__init__.py`, `backend/scraping/__init__.py`, `backend/matching/__init__.py`, `backend/llm/__init__.py`, `backend/latex/__init__.py`, `backend/applier/__init__.py`, `backend/scheduler/__init__.py` (all empty `__init__.py`)

  **Must NOT do**:
  - Do NOT implement any feature logic yet — this is scaffolding only
  - Do NOT store any secrets in code or committed files
  - Do NOT create `alembic.ini` yet (that's Task 2)

  **Recommended Agent Profile**:
  > Category: `quick` — pure file/config creation, no complex logic
  - **Category**: `quick`
    - Reason: Scaffolding and configuration files require precision but no domain complexity
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `frontend-design`: Not needed — no UI in this task

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0 — start immediately)
  - **Parallel Group**: Wave 0 (with Tasks 2, 3, 4, 5, 6 — BUT Task 2 needs Task 1's file structure)
  - **Blocks**: Tasks 2, 3, 6, 7, 8, 9, 10, 11 (all backend tasks need config.py)
  - **Blocked By**: None (can start immediately)

  **References**:
  - `JOBPILOT_PLAN.md` lines 140-155 — full tech stack with versions
  - `JOBPILOT_PLAN.md` lines 1563-1595 — pyproject.toml template to follow exactly
  - `JOBPILOT_PLAN.md` lines 1497-1561 — start.py implementation to follow
  - `JOBPILOT_PLAN.md` lines 1801-1820 — .env variables list

  **Acceptance Criteria**:
  - [ ] `uv sync` completes without errors
  - [ ] `uv run python -c "from backend.config import settings; print(settings.jobpilot_host)"` prints `127.0.0.1`
  - [ ] `uv run uvicorn backend.main:app --host 127.0.0.1 --port 8001` starts without import errors
  - [ ] `curl -s http://localhost:8001/api/health` returns `{"status": "ok", "version": "0.1.0"}`
  - [ ] `.gitignore` blocks `.env` and `data/` from tracking

  **QA Scenarios**:
  ```
  Scenario: Health endpoint returns OK
    Tool: Bash (curl)
    Preconditions: uvicorn running on port 8001 (test port)
    Steps:
      1. curl -s http://localhost:8001/api/health
      2. Parse JSON response
      3. Assert response['status'] == 'ok'
    Expected Result: {"status": "ok", "version": "0.1.0"}
    Evidence: .sisyphus/evidence/task-1-health-check.json

  Scenario: Config validation fails on missing required key
    Tool: Bash
    Preconditions: .env file missing GOOGLE_API_KEY
    Steps:
      1. Temporarily rename .env to .env.bak
      2. Run: python -c "from backend.config import settings" 2>&1
      3. Check exit code != 0 and stderr contains 'GOOGLE_API_KEY'
    Expected Result: ValidationError raised with field name mentioned
    Evidence: .sisyphus/evidence/task-1-config-validation-error.txt
  ```

  **Commit**: YES (group with Wave 0 completion)
  - Message: `feat(scaffold): initialize jobpilot project structure and dependencies`
  - Pre-commit: `uv run ruff check backend/`

- [ ] 2. SQLAlchemy Models + Alembic Init + Initial Migration

  **What to do**:
  - Create `backend/database.py`: async SQLAlchemy engine using `aiosqlite`, session factory, `PRAGMA journal_mode=WAL` executed on connect, `async_session` context manager
  - Create `backend/models/user.py`: `UserProfile` and `SearchSettings` ORM models matching the SQL schema in JOBPILOT_PLAN.md section 6 exactly
  - Create `backend/models/job.py`: `Job`, `JobSource`, `JobMatch` ORM models with all columns from the plan including `dedup_hash UNIQUE` index
  - Create `backend/models/document.py`: `TailoredDocument` ORM model
  - Create `backend/models/application.py`: `Application` and `ApplicationEvent` ORM models
  - Create `backend/models/session.py`: `BrowserSession` ORM model
  - Create `backend/models/__init__.py` that imports all models (so Alembic can discover them)
  - Initialize Alembic: `alembic init alembic` — configure `alembic.ini` to use the async engine from `backend/database.py`, `env.py` to import all models and use `target_metadata = Base.metadata`
  - Create initial migration: `alembic revision --autogenerate -m 'initial schema'`, then verify the generated migration looks correct
  - Add `async def create_tables()` in `database.py` as fallback (called in app startup if Alembic is not run)

  **Must NOT do**:
  - Do NOT use synchronous SQLAlchemy sessions anywhere — async only
  - Do NOT skip the WAL mode pragma
  - Do NOT store absolute file paths as column values (use relative paths)
  - Do NOT add any columns beyond what's in JOBPILOT_PLAN.md section 6

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical translation of the SQL schema from the plan into SQLAlchemy ORM models
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0 — can start after Task 1 creates directory structure)
  - **Parallel Group**: Wave 0 (depends on Task 1 for directory structure)
  - **Blocks**: Tasks 11, 15, 21 (DB layer needed for API routes)
  - **Blocked By**: Task 1 (needs `backend/` directory)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1131-1263 — full SQL schema to implement as SQLAlchemy models
  - SQLAlchemy async docs: `https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html`
  - Alembic async guide: `https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic`

  **Acceptance Criteria**:
  - [ ] `alembic upgrade head` runs without errors on a fresh database
  - [ ] `python3 -c "import sqlite3; c=sqlite3.connect('data/jobpilot.db'); tables=[t[0] for t in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]; assert 'jobs' in tables and 'applications' in tables"` passes
  - [ ] `alembic downgrade -1 && alembic upgrade head` completes without errors (migration is reversible)

  **QA Scenarios**:
  ```
  Scenario: Initial schema creation from scratch
    Tool: Bash
    Preconditions: No data/jobpilot.db exists yet
    Steps:
      1. mkdir -p data && rm -f data/jobpilot.db
      2. alembic upgrade head
      3. python3 -c "import sqlite3; c=sqlite3.connect('data/jobpilot.db'); tables=[t[0] for t in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]; print(tables)"
      4. Assert output contains: jobs, job_matches, applications, application_events, user_profile, search_settings, job_sources, tailored_documents, browser_sessions
    Expected Result: All 9 tables created
    Evidence: .sisyphus/evidence/task-2-schema-creation.txt

  Scenario: WAL mode is enabled
    Tool: Bash
    Preconditions: DB exists after alembic upgrade head
    Steps:
      1. python3 -c "import sqlite3; c=sqlite3.connect('data/jobpilot.db'); print(c.execute('PRAGMA journal_mode').fetchone())"
    Expected Result: ('wal',)
    Evidence: .sisyphus/evidence/task-2-wal-mode.txt
  ```

  **Commit**: YES (group with Wave 0)

- [ ] 3. FastAPI Skeleton — CORS, Lifespan, Static Serving, API Router

  **What to do**:
  - Complete `backend/main.py`: add `lifespan` context manager that (1) runs `alembic upgrade head` on startup, (2) creates `data/` subdirectories, (3) initializes the DB connection pool. Add `CORSMiddleware` for dev (allow all origins). Add `APIRouter` and include routers for all modules (even if empty stubs). Mount `frontend/build` as static files at `/` with `html=True`. Add catch-all route that serves `frontend/build/index.html` for any unmatched path (SPA routing).
  - Create stub router files (empty routers) for: `backend/api/jobs.py`, `backend/api/queue.py`, `backend/api/applications.py`, `backend/api/documents.py`, `backend/api/settings.py`, `backend/api/analytics.py`, `backend/api/ws.py` — each with `router = APIRouter()` and a stub health GET endpoint
  - Implement `/api/health` to return: `{"status": "ok", "version": "0.1.0", "db": "connected", "tectonic": true/false, "gemini_key_set": true/false}`

  **Must NOT do**:
  - Do NOT implement actual route logic (only stubs) — that's Tasks 15, 21
  - Do NOT hardcode CORS origins — read from settings

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0)
  - **Parallel Group**: Wave 0 (with Tasks 1, 2, 4, 5, 6)
  - **Blocks**: Tasks 15, 21 (route implementations)
  - **Blocked By**: Task 1 (needs config.py)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1267-1401 — project structure with all route files listed
  - `JOBPILOT_PLAN.md` lines 1497-1561 — start.py showing uvicorn setup
  - FastAPI static files mount pattern for SvelteKit: `app.mount('/', StaticFiles(directory='frontend/build', html=True))`

  **Acceptance Criteria**:
  - [ ] `curl -s http://localhost:8000/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'"` passes
  - [ ] `curl -s http://localhost:8000/api/jobs` returns 200 (stub)
  - [ ] `curl -s http://localhost:8000/nonexistent-route` returns the SvelteKit index.html (once frontend is built)

  **QA Scenarios**:
  ```
  Scenario: All API stub routes return 200
    Tool: Bash (curl)
    Steps:
      1. for route in /api/jobs /api/queue /api/applications /api/settings /api/analytics; do curl -s -o /dev/null -w "%{http_code} $route\n" http://localhost:8000$route; done
    Expected Result: All return 200 or documented status
    Evidence: .sisyphus/evidence/task-3-stub-routes.txt
  ```

  **Commit**: YES (group with Wave 0)

- [ ] 4. SvelteKit Init + shadcn-svelte + TailwindCSS + adapter-static

  **What to do**:
  - Scaffold SvelteKit in `frontend/` using `npm create svelte@latest frontend` — choose: TypeScript, ESLint + Prettier
  - Install and configure TailwindCSS: `npm install -D tailwindcss postcss autoprefixer && npx tailwindcss init`. Create `tailwind.config.js` with custom theme variables matching Notion/Linear aesthetic (Inter font, tight spacing, neutral color palette)
  - Install shadcn-svelte and initialize: `npx shadcn-svelte@latest init`. Configure with dark mode as default (class-based dark mode), slate base color
  - Configure `svelte.config.js` to use `adapter-static` with `fallback: 'index.html'` for SPA routing
  - Install `mode-watcher` for dark mode persistence: `npm install mode-watcher`
  - Create `src/app.css` with Tailwind directives + CSS custom properties for the Notion/Linear color system (bg, text, border, accent colors in dark/light variants)
  - Create `src/app.html` with dark mode class on `<html>`, Inter font link, pdf.js CDN link
  - Create `src/lib/api.ts` — typed fetch wrapper with base URL from env, error handling, JSON parsing. Export typed API client functions for each endpoint group.
  - Create `src/lib/stores/websocket.ts` — WebSocket store that connects to `ws://localhost:8000/ws`, auto-reconnects on disconnect, exposes `messages` readable and `send()` function
  - Verify build: `npm run build` produces `build/` directory with `index.html`

  **Must NOT do**:
  - Do NOT build any page components yet — that's Tasks 22-27
  - Do NOT use `adapter-node` — must use `adapter-static`
  - Do NOT use light mode as default — dark mode is default

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Frontend framework setup with design system configuration requires UI expertise
  - **Skills**: [`frontend-design`]
    - `frontend-design`: Needed for Notion/Linear aesthetic token setup and TailwindCSS theme config

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0 — completely independent of backend tasks)
  - **Parallel Group**: Wave 0 (with Tasks 1, 2, 3, 5, 6)
  - **Blocks**: Tasks 10, 22-27 (all frontend UI tasks)
  - **Blocked By**: None (can start immediately)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1046-1053 — UI tech details (shadcn-svelte, TailwindCSS, pdf.js, WebSocket)
  - `JOBPILOT_PLAN.md` lines 1268-1401 — frontend directory structure to follow
  - shadcn-svelte docs: `https://www.shadcn-svelte.com/docs/installation`
  - `JOBPILOT_PLAN.md` lines 1369-1376 — config files to create (svelte.config.js, tailwind.config.js, vite.config.js, package.json)

  **Acceptance Criteria**:
  - [ ] `cd frontend && npm install && npm run build` completes without errors
  - [ ] `frontend/build/index.html` exists and contains `JobPilot` in title
  - [ ] Dark mode class is on `<html>` by default when serving the app
  - [ ] `cd frontend && npm run check` passes TypeScript type checking

  **QA Scenarios**:
  ```
  Scenario: Frontend builds successfully
    Tool: Bash
    Steps:
      1. cd frontend && npm install 2>&1 | tail -5
      2. npm run build 2>&1 | tail -10
      3. ls -la build/index.html
    Expected Result: build/index.html exists, npm run build exits 0
    Evidence: .sisyphus/evidence/task-4-frontend-build.txt

  Scenario: Dark mode is default
    Tool: Playwright
    Preconditions: App running on localhost:8000 with frontend served
    Steps:
      1. Navigate to http://localhost:8000
      2. Evaluate: document.documentElement.classList.contains('dark')
    Expected Result: true
    Evidence: .sisyphus/evidence/task-4-dark-mode.png
  ```

  **Commit**: YES (group with Wave 0)

- [ ] 5. Typed WebSocket Message Protocol (Pydantic Models)

  **What to do**:
  - Create `backend/api/ws_models.py` — define ALL WebSocket message types as Pydantic models with a `type` discriminator field. Required message types (from JOBPILOT_PLAN.md): `scraping_status` (type, message, source, progress), `matching_status` (type, count), `tailoring_status` (type, job_id, progress), `apply_review` (type, job_id, filled_fields dict, screenshot_base64, action_required), `apply_result` (type, job_id, status, method), `login_required` (type, site, browser_window_title), `login_confirmed` (type, site), `error` (type, message, code). All wrapped in a `WSMessage` discriminated union.
  - Create `backend/api/ws.py` — `ConnectionManager` class with: `connect(websocket)`, `disconnect(websocket)`, `broadcast(message: WSMessage)`, `send_to(client_id, message)`. Use `asyncio.Queue` per client for backpressure.
  - Create FastAPI WebSocket route `/ws` that accepts connections, registers them with `ConnectionManager`, and loops receiving messages. Incoming messages: `confirm_submit` (user approves auto-apply), `cancel_apply` (user cancels), `login_done` (user finished logging in).
  - Create `frontend/src/lib/stores/websocket.ts` updated to use the typed protocol — parse incoming `type` field and route to appropriate Svelte stores. Export `wsStatus` (connected/disconnected/reconnecting) store.

  **Must NOT do**:
  - Do NOT send raw untyped dict messages over WebSocket — always use the Pydantic models
  - Do NOT implement any business logic here — only the protocol and connection management

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Protocol definition is mechanical schema design
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0)
  - **Parallel Group**: Wave 0 (with Tasks 1-4, 6)
  - **Blocks**: Tasks 16, 17, 19, 20 (all tasks that emit WS messages)
  - **Blocked By**: Task 1 (needs FastAPI app structure)

  **References**:
  - `JOBPILOT_PLAN.md` lines 914-921 — the `apply_review` message fields (model this exactly)
  - `JOBPILOT_PLAN.md` lines 924-933 — the confirmation flow (model `confirm_submit` message)
  - `JOBPILOT_PLAN.md` lines 418-420 — login_required flow needs its own message type

  **Acceptance Criteria**:
  - [ ] `python3 -c "from backend.api.ws_models import WSMessage; print('ok')"` succeeds
  - [ ] WebSocket connects: `python3 -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:8000/ws'))"` succeeds (with app running)
  - [ ] All 8 message types are importable and validate correctly

  **QA Scenarios**:
  ```
  Scenario: WebSocket handshake succeeds
    Tool: Bash
    Preconditions: App running on localhost:8000
    Steps:
      1. python3 -c "import asyncio, websockets, json; async def test(): async with websockets.connect('ws://localhost:8000/ws') as ws: print('connected'); await ws.close(); asyncio.run(test())"
    Expected Result: prints 'connected' without error
    Evidence: .sisyphus/evidence/task-5-ws-connect.txt
  ```

  **Commit**: YES (group with Wave 0)

- [ ] 6. pytest Infrastructure + conftest.py + Smoke Tests

  **What to do**:
  - Create `tests/` directory with `__init__.py`
  - Create `tests/conftest.py`: async test fixtures using `pytest-asyncio` — `event_loop` fixture (session scope), `test_db` fixture (in-memory SQLite session), `test_app` fixture (TestClient wrapping FastAPI app), `mock_gemini` fixture (mocks the Gemini client to return canned responses), `test_settings` fixture (loads test `.env`)
  - Configure `pytest.ini` or `[tool.pytest.ini_options]` in `pyproject.toml`: `asyncio_mode = 'auto'`, `testpaths = ['tests']`
  - Create `tests/test_smoke.py`: 3 smoke tests: (1) app starts and health endpoint returns ok, (2) DB can be created and a table queried, (3) config loads without errors from `.env.example`
  - Create `tests/test_models.py`: test that each ORM model can be instantiated and that Alembic migration applies cleanly

  **Must NOT do**:
  - Do NOT write tests for features not yet implemented — only smoke tests
  - Do NOT use real external API keys in tests — always mock Gemini/Adzuna

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`run-tests`]
    - `run-tests`: Needed for correct pytest setup and fixture patterns

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 0 — can start alongside Tasks 1-5)
  - **Parallel Group**: Wave 0
  - **Blocks**: Tasks 9, 12, 13, 14, 17, 20, 32 (all have test components)
  - **Blocked By**: Task 1 (needs backend/ structure), Task 2 (needs models for model tests)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1590-1594 — dev dependencies (pytest>=8.0, pytest-asyncio>=0.24)
  - pytest-asyncio docs pattern for async fixtures

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/ -v` runs without collection errors
  - [ ] At least 3 smoke tests pass
  - [ ] `uv run pytest tests/test_smoke.py::test_health_endpoint -v` passes

  **QA Scenarios**:
  ```
  Scenario: Smoke tests all pass
    Tool: Bash
    Steps:
      1. uv run pytest tests/ -v --tb=short 2>&1 | tail -20
    Expected Result: ≥3 tests passing, 0 errors
    Evidence: .sisyphus/evidence/task-6-pytest-smoke.txt
  ```

  **Commit**: YES (group with Wave 0)

<!-- WAVE 1: Core Services -->

- [ ] 7. Gemini Client + Rate Limiter (15 RPM Token Bucket)

  **What to do**:
  - Create `backend/llm/gemini_client.py`: async Gemini client wrapping `google-generativeai`. Initialize `genai.configure(api_key=settings.google_api_key)`. Implement `GeminiClient` class with:
    - `_rate_limiter`: `asyncio.Semaphore` + token bucket that hard-enforces 15 RPM with exponential backoff on `429` errors (up to 3 retries)
    - `async def generate_json(prompt: str, schema: type[T]) -> T` — sends prompt, parses JSON response, validates against Pydantic schema `T`, raises `GeminiJSONError` if invalid
    - `async def generate_text(prompt: str) -> str` — plain text generation
    - Rate limit tracking: `_call_times: deque` (maxlen=15) + sliding window check before each call
  - Create `backend/llm/prompts.py`: copy ALL prompt templates from JOBPILOT_PLAN.md sections 5.3 (CV_SUMMARY_PROMPT, CV_EXPERIENCE_PROMPT, MOTIVATION_LETTER_PROMPT) verbatim
  - Create `backend/llm/validators.py`: Pydantic response schemas: `CVSummaryEdit` (edited_summary: str | None, changes_made: list[str]), `CVExperienceEdit` (edits: list[BulletEdit]), `BulletEdit` (index: int, original: str, edited: str, reason: str), `LetterEdit` (edited_paragraph: str, company_name: str)
  - Add `GeminiJSONError`, `GeminiRateLimitError` custom exceptions
  - Write `tests/test_gemini_client.py`: test rate limiter (mock time to simulate 15+ calls), test JSON parsing with valid response, test JSON parsing with invalid response raises GeminiJSONError

  **Must NOT do**:
  - Do NOT call Gemini without going through the rate limiter
  - Do NOT trust raw LLM string output — always validate via Pydantic schemas
  - Do NOT use synchronous `google-generativeai` calls — use async generation

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Rate limiter + API wrapper — mechanical implementation with clear requirements
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1 — after Wave 0 complete)
  - **Parallel Group**: Wave 1 (with Tasks 8, 9, 10, 11)
  - **Blocks**: Tasks 12, 13, 17, 20 (all use Gemini for LLM calls)
  - **Blocked By**: Tasks 1, 6 (needs config.py and test infrastructure)

  **References**:
  - `JOBPILOT_PLAN.md` lines 669-742 — all three prompt templates to copy verbatim
  - `JOBPILOT_PLAN.md` lines 174-176 — `ChatGoogle` usage pattern (note: use `google-generativeai` directly, NOT `ChatGoogle` from browser-use for this client)
  - google-generativeai async docs: `https://ai.google.dev/api/python/google/generativeai`
  - Rate limit: 15 RPM free tier, implement sliding window counter

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_gemini_client.py -v` — all tests pass (mocked, no real API calls)
  - [ ] Rate limiter blocks the 16th call within a 60s window
  - [ ] Invalid JSON from LLM raises `GeminiJSONError` not a generic exception

  **QA Scenarios**:
  ```
  Scenario: Rate limiter enforces 15 RPM
    Tool: Bash (pytest)
    Preconditions: Mocked time module
    Steps:
      1. uv run pytest tests/test_gemini_client.py::test_rate_limiter_blocks_at_15rpm -v
    Expected Result: Test passes — 16th call is delayed/blocked
    Evidence: .sisyphus/evidence/task-7-rate-limiter.txt

  Scenario: Invalid LLM JSON raises typed exception
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_gemini_client.py::test_invalid_json_raises -v
    Expected Result: GeminiJSONError raised, not json.JSONDecodeError
    Evidence: .sisyphus/evidence/task-7-json-error.txt
  ```

  **Commit**: NO (group with Wave 1 completion)

- [ ] 8. Adzuna REST API Client + RawJob Schema

  **What to do**:
  - Create `backend/scraping/adzuna_client.py`: implement `AdzunaClient` class exactly as designed in JOBPILOT_PLAN.md section 5.1. Async `httpx` calls. Parameters: `app_id`, `app_key`, `base_url='https://api.adzuna.com/v1/api/jobs'`. Method `async def search(keywords, filters, country='gb', page=1, results_per_page=20) -> list[RawJob]`. Parse Adzuna JSON response into `RawJob` objects.
  - Create `backend/scraping/deduplicator.py`: `JobDeduplicator` exactly as designed in section 5.2 (MD5 hash of normalized company|title|location). Method `deduplicate(jobs: list[RawJob]) -> list[RawJob]`.
  - Create `backend/matching/filters.py`: `JobFilters` dataclass exactly as in section 5.2.
  - Create `backend/models/schemas.py`: Pydantic schemas (separate from ORM models) — `RawJob`, `JobDetails`, `JobMatch` response schemas for API serialization
  - Write `tests/test_adzuna_client.py`: mock `httpx.AsyncClient.get` to return a canned Adzuna JSON response. Test: (1) valid search returns list of RawJob, (2) API error raises appropriate exception, (3) empty results returns empty list

  **Must NOT do**:
  - Do NOT make real Adzuna API calls in tests — mock httpx
  - Do NOT add fields to RawJob beyond what Adzuna actually returns

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1 — with Tasks 7, 9, 10, 11)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 14, 17, 19 (matching and scraping orchestration)
  - **Blocked By**: Tasks 1, 2, 6 (needs config, models structure, test infra)

  **References**:
  - `JOBPILOT_PLAN.md` lines 222-253 — AdzunaClient implementation to follow exactly
  - `JOBPILOT_PLAN.md` lines 560-581 — JobDeduplicator to implement
  - `JOBPILOT_PLAN.md` lines 492-501 — JobFilters dataclass
  - Adzuna API docs: `https://developer.adzuna.com/docs/search`

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_adzuna_client.py -v` — all tests pass
  - [ ] `RawJob` schema has all fields from Adzuna response (title, company, location, salary_text, description, url, redirect_url)

  **QA Scenarios**:
  ```
  Scenario: Adzuna search returns parsed RawJob list (mocked)
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_adzuna_client.py::test_search_returns_raw_jobs -v
    Expected Result: Returns list[RawJob] with expected field values from mocked response
    Evidence: .sisyphus/evidence/task-8-adzuna-parse.txt
  ```

  **Commit**: NO (group with Wave 1)

- [ ] 9. LaTeX Section Parser (TexSoup + Comment Markers)

  **What to do**:
  - Create `backend/latex/parser.py`: `LaTeXParser` class with:
    - `extract_sections(tex_path: Path) -> LaTeXSections` — finds comment markers (`% --- JOBPILOT:SUMMARY:START ---` / `END`, `% --- JOBPILOT:EXPERIENCE:START ---` / `END`, `% --- JOBPILOT:LETTER:PARA:START ---` / `END`) using regex-based extraction as primary method (NOT TexSoup for marker detection). Falls back to TexSoup `\section{}` detection if no markers found.
    - `LaTeXSections` dataclass: `summary: str | None`, `experience_bullets: list[str]`, `customizable_paragraph: str | None`, `has_markers: bool`
    - `extract_bullets(experience_block: str) -> list[str]` — parse `\item` lines from a LaTeX `itemize` block
    - `validate_markers(tex_content: str) -> list[str]` — returns list of warnings if markers are mismatched or missing
  - Create `backend/latex/injector.py`: `LaTeXInjector` class:
    - `inject_summary_edit(original_tex: str, new_summary: str) -> str` — replaces content between JOBPILOT:SUMMARY markers
    - `inject_experience_edits(original_tex: str, edits: list[BulletEdit]) -> str` — replaces only the specific \item lines that were edited, keeping all others unchanged
    - `inject_letter_edit(original_tex: str, new_paragraph: str, company_name: str) -> str` — swaps the customizable paragraph and any `{company_name}` placeholders
    - All injection methods: create a copy (never modify original), validate markers exist before injecting
  - Write `tests/test_latex_parser.py`: create a minimal sample .tex fixture with markers, test: (1) markers detected correctly, (2) summary extracted, (3) bullet points parsed, (4) no markers → fallback gracefully

  **Must NOT do**:
  - Do NOT modify the original `.tex` file — always work on a copy
  - Do NOT use TexSoup for marker detection (it's unreliable for this) — use regex
  - Do NOT let the LLM see raw LaTeX — the parser extracts TEXT only

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: LaTeX parsing has tricky edge cases (nested environments, escaped characters, varied template styles)
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1 — with Tasks 7, 8, 10, 11)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 12, 13 (pipeline and Gemini editor need sections to work on)
  - **Blocked By**: Tasks 1, 6 (needs project structure and test infra)

  **References**:
  - `JOBPILOT_PLAN.md` lines 641-665 — LaTeX template markers format (exactly what to implement)
  - `JOBPILOT_PLAN.md` lines 585-639 — LaTeX pipeline flow diagram showing what parser produces
  - TexSoup API: `https://texsoup.alvinwan.com/`

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_latex_parser.py -v` — all tests pass
  - [ ] Parser correctly extracts summary text from fixture .tex file with markers
  - [ ] Parser gracefully handles .tex file with NO markers (no crash, `has_markers=False`)
  - [ ] Injector round-trip: extract summary → modify text → inject back → original markers preserved

  **QA Scenarios**:
  ```
  Scenario: Parser extracts sections from marked template
    Tool: Bash (pytest)
    Steps:
      1. Create tests/fixtures/sample_cv.tex with JOBPILOT markers
      2. uv run pytest tests/test_latex_parser.py::test_extract_with_markers -v
    Expected Result: sections.summary is not None, sections.has_markers is True
    Evidence: .sisyphus/evidence/task-9-parser-markers.txt

  Scenario: Injector preserves unchanged lines
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_latex_parser.py::test_inject_preserves_unchanged -v
    Expected Result: All lines outside markers are byte-for-byte identical after injection
    Evidence: .sisyphus/evidence/task-9-injector-preserve.txt
  ```

  **Commit**: NO (group with Wave 1)

- [ ] 10. SvelteKit App Shell + Dark Mode + Navigation Layout

  **What to do**:
  - Create `frontend/src/routes/+layout.svelte`: app shell with: sidebar navigation (icons + labels for all 6 pages: Queue, Tracker, CV Manager, Settings, Analytics, and a hamburger on mobile), top bar with app name `JobPilot`, dark/light mode toggle using `mode-watcher`, WebSocket status indicator dot (green=connected, yellow=reconnecting, red=disconnected from `wsStatus` store), and a `StatusBar` component at the bottom for scraping/apply progress messages.
  - Create `frontend/src/lib/components/StatusBar.svelte`: bottom status bar that subscribes to WebSocket messages of type `scraping_status`, `matching_status`, `tailoring_status` and shows a compact progress message with spinner animation when a batch is running.
  - Create `frontend/src/routes/+page.svelte` stub (Morning Queue — just a placeholder 'Loading...' for now, implemented in Task 22)
  - Implement the Notion/Linear aesthetic: sidebar width 220px, monochrome icons (lucide-svelte), neutral gray palette, compact 14px body text, slightly larger headings, subtle borders, no drop shadows except on modals.
  - Install `lucide-svelte` for icons: `npm install lucide-svelte`

  **Must NOT do**:
  - Do NOT implement page content yet — only the shell/layout
  - Do NOT use colorful/vibrant color scheme — strictly Notion/Linear monochrome
  - Do NOT add complex animations — subtle transitions only (opacity, translate-x for sidebar)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]
    - `frontend-design`: Critical for getting the Notion/Linear aesthetic right with proper spacing and typography

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1 — after Wave 0 SvelteKit setup in Task 4)
  - **Parallel Group**: Wave 1 (with Tasks 7, 8, 9, 11)
  - **Blocks**: Tasks 22-27 (all page implementations need the layout)
  - **Blocked By**: Task 4 (needs SvelteKit + shadcn-svelte + Tailwind setup)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1053 — all 6 pages with routes and purposes
  - `JOBPILOT_PLAN.md` lines 1022-1044 — Morning Queue mockup (for sidebar/nav context)
  - `JOBPILOT_PLAN.md` lines 1338-1401 — frontend file structure to follow
  - Lucide Svelte: `https://lucide.dev/guide/packages/lucide-svelte`
  - shadcn-svelte dark mode: use `mode-watcher` ModeWatcher component in layout

  **Acceptance Criteria**:
  - [ ] `npm run build` succeeds with layout
  - [ ] All 6 navigation links are present in the sidebar
  - [ ] Dark mode toggle switches between dark/light and persists in localStorage
  - [ ] WebSocket status dot is visible (disconnected state on initial load)

  **QA Scenarios**:
  ```
  Scenario: Navigation renders with all 6 links
    Tool: Playwright
    Preconditions: App running on localhost:8000
    Steps:
      1. page.goto('http://localhost:8000')
      2. Evaluate: document.querySelectorAll('nav a').length >= 6
      3. Check text content of nav links includes 'Settings', 'Tracker', 'Analytics'
    Expected Result: 6 navigation links visible
    Evidence: .sisyphus/evidence/task-10-navigation.png

  Scenario: Dark mode toggle works
    Tool: Playwright
    Steps:
      1. page.goto('http://localhost:8000')
      2. Click dark/light toggle button (selector: [data-testid='theme-toggle'])
      3. Evaluate: document.documentElement.classList.contains('dark')
    Expected Result: Theme class toggles on <html>
    Evidence: .sisyphus/evidence/task-10-theme-toggle.png
  ```

  **Commit**: NO (group with Wave 1)

- [ ] 11. Database API Layer (CRUD helpers, async session factory)

  **What to do**:
  - Create `backend/database.py` complete version: `get_db()` async generator (dependency injection for FastAPI routes), `init_db()` async function called at startup, `AsyncSessionLocal` factory. Add convenience context manager `db_session()` for non-route code.
  - Create `backend/api/deps.py`: FastAPI dependency functions — `Annotated[AsyncSession, Depends(get_db)]` shorthand `DBSession`, similar shorthand for `Settings`.
  - Create `backend/scraping/orchestrator.py` skeleton: `ScrapingOrchestrator` class with empty `run_morning_batch()` stub (implemented in Task 17). Constructor takes `adzuna_client`, `adaptive_scraper`, `session_mgr`, `deduplicator`.
  - Create `backend/matching/matcher.py`: complete `JobMatcher.score()` implementation from JOBPILOT_PLAN.md section 5.2 (keyword matching 40%, location 20%, experience 15%, salary 10%, recency 10%, exclusion penalty). Use simple term overlap for keyword scoring (no external NLP library).
  - Write `tests/test_matcher.py`: test scoring: (1) perfect keyword match scores near 80+, (2) excluded term returns 0.0, (3) blacklisted company returns 0.0, (4) `rank_and_filter` returns sorted list above min_score threshold

  **Must NOT do**:
  - Do NOT use synchronous DB operations anywhere
  - Do NOT implement business logic in this task — only DB infrastructure and job matcher

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 1 — with Tasks 7, 8, 9, 10)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 14, 15, 17, 19, 20, 21 (all route handlers and batch need DB layer)
  - **Blocked By**: Tasks 2, 3 (needs models and FastAPI app)

  **References**:
  - `JOBPILOT_PLAN.md` lines 503-556 — full JobMatcher scoring logic
  - `JOBPILOT_PLAN.md` lines 1276-1284 — database.py purpose in architecture
  - SQLAlchemy async session docs: `https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html`

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_matcher.py -v` — all tests pass
  - [ ] `get_db()` is importable and usable as FastAPI dependency
  - [ ] `JobMatcher().score(job, filters)` returns float between 0.0 and 100.0

  **QA Scenarios**:
  ```
  Scenario: Excluded term scores zero
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_matcher.py::test_excluded_term_zero_score -v
    Expected Result: score == 0.0 when job contains excluded keyword
    Evidence: .sisyphus/evidence/task-11-matcher-exclusion.txt
  ```

  **Commit**: YES (commit Wave 1 after Tasks 7-11 all complete)
  - Message: `feat(core): add Gemini client, Adzuna client, LaTeX parser, DB layer, frontend shell`

<!-- WAVE 2: Pipelines -->

- [ ] 12. LaTeX Compiler + Tectonic Integration + Full CV Pipeline

  **What to do**:
  - Create `backend/latex/compiler.py`: `LaTeXCompiler` class from JOBPILOT_PLAN.md section 5.3 — `_find_tectonic()` (check PATH, then `bin/tectonic[.exe]`), `async def compile(tex_path: Path, output_dir: Path) -> Path` using `asyncio.create_subprocess_exec`. Raise `LaTeXCompilationError` on non-zero exit.
  - Create `backend/latex/validator.py`: `LaTeXValidator` class — validate by attempting a dry-run compilation (`tectonic --dry-run`) OR by checking for known LaTeX syntax errors using regex patterns (since chktex is excluded). Method: `async def validate(tex_path: Path) -> list[str]` returns list of warning strings.
  - Create `backend/latex/pipeline.py`: `CVPipeline` class exactly as designed in section 5.3 — `generate_tailored_cv(base_cv_path, job, output_dir) -> TailoredCV`. `LetterPipeline` class: `generate_tailored_letter(base_letter_path, job, output_dir) -> TailoredLetter`. Both save the modified `.tex` and compiled `.pdf` to `data/cvs/{job_id}/` and `data/letters/{job_id}/`.
  - Create `backend/latex/diff.py`: `generate_diff(original_sections, edits) -> list[DiffEntry]` — produces a structured diff (for the frontend diff viewer). `DiffEntry`: `section`, `original_text`, `edited_text`, `change_description`.
  - Write `tests/test_latex_pipeline.py`: use a sample CV fixture. Test: (1) pipeline produces a PDF file, (2) PDF is > 1000 bytes, (3) diff is returned, (4) pipeline handles missing Tectonic gracefully (raises clear error)

  **Must NOT do**:
  - Do NOT call chktex anywhere — use Tectonic compilation as validator
  - Do NOT modify the original base CV .tex file ever
  - Do NOT let Gemini return raw LaTeX — it returns JSON diff only

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: LaTeX compilation pipeline has complex async subprocess handling and multiple failure modes
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 — after Wave 1 complete)
  - **Parallel Group**: Wave 2 (with Tasks 13, 14, 15, 16)
  - **Blocks**: Tasks 19, 20 (morning batch and apply engine need CV PDFs)
  - **Blocked By**: Tasks 9, 7 (needs parser from Task 9, Gemini client from Task 7)

  **References**:
  - `JOBPILOT_PLAN.md` lines 745-851 — full pipeline implementation to follow
  - `JOBPILOT_PLAN.md` lines 770-773 — Tectonic binary name logic (platform-specific)
  - `JOBPILOT_PLAN.md` lines 775-793 — async compile() implementation
  - Tectonic docs: `https://tectonic-typesetting.github.io/en-US/`

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_latex_pipeline.py -v` — all tests pass (Tectonic must be installed)
  - [ ] `tests/fixtures/sample_cv.tex` compiled to `tests/fixtures/sample_cv.pdf` (> 1000 bytes)
  - [ ] `LaTeXCompilationError` raised with helpful message when Tectonic not found

  **QA Scenarios**:
  ```
  Scenario: CV pipeline produces valid PDF
    Tool: Bash (pytest)
    Preconditions: Tectonic installed, sample CV fixture with markers
    Steps:
      1. uv run pytest tests/test_latex_pipeline.py::test_pipeline_produces_pdf -v
    Expected Result: PDF path returned, file exists, size > 1000 bytes
    Evidence: .sisyphus/evidence/task-12-pdf-output.txt

  Scenario: Missing Tectonic raises clear error
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_latex_pipeline.py::test_missing_tectonic_error -v
    Expected Result: LaTeXCompilationError raised with 'Run jobpilot setup' message
    Evidence: .sisyphus/evidence/task-12-missing-tectonic.txt
  ```

  **Commit**: NO (group with Wave 2)

- [ ] 13. Gemini LaTeX Editors (CV Summary + Experience + Letter Prompts)

  **What to do**:
  - Create `backend/llm/gemini_client.py` additions (edit existing file): add `edit_cv_summary(sections: LaTeXSections, job: JobDetails) -> CVSummaryEdit`, `edit_cv_experience(sections: LaTeXSections, job: JobDetails) -> CVExperienceEdit`, `edit_letter(sections: LaTeXSections, job: JobDetails) -> LetterEdit` — all three use the prompts from `prompts.py`, call `generate_json()`, validate against Pydantic schemas.
  - The methods must: (1) truncate job description to 500 chars before putting in prompt, (2) pass `null` gracefully if sections not available, (3) return `None` edits if LLM says no changes needed
  - Add `tests/test_gemini_editors.py`: mock GeminiClient.generate_json to return canned edit responses. Test: (1) edit_cv_summary returns CVSummaryEdit, (2) experience edits only contain changed bullets, (3) letter edit contains company_name replacement, (4) invalid LLM response raises GeminiJSONError

  **Must NOT do**:
  - Do NOT pass full job descriptions > 1000 chars to LLM in prompts (truncate)
  - Do NOT accept LLM edits that change LaTeX commands (`\textbf`, `\item`, etc.) — validate that no LaTeX commands were added/changed in the edited text

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Parallel Group**: Wave 2 (with Tasks 12, 14, 15, 16)
  - **Blocks**: Task 19 (morning batch calls these editors)
  - **Blocked By**: Tasks 7, 9 (Gemini client and LaTeX sections needed)

  **References**:
  - `JOBPILOT_PLAN.md` lines 669-742 — all three LLM prompts to use
  - `JOBPILOT_PLAN.md` lines 817-851 — CVPipeline showing how edit methods are called
  - `JOBPILOT_PLAN.md` lines 694-718 — experience edit JSON schema (follow exactly)

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_gemini_editors.py -v` — all tests pass
  - [ ] `edit_cv_summary()` returns `CVSummaryEdit` with `edited_summary` field
  - [ ] LLM response validation rejects edits that alter LaTeX commands

  **QA Scenarios**:
  ```
  Scenario: CV summary edit validated against Pydantic schema
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_gemini_editors.py::test_summary_edit_validates -v
    Expected Result: Returns CVSummaryEdit, not raw dict
    Evidence: .sisyphus/evidence/task-13-summary-edit.txt
  ```

  **Commit**: NO (group with Wave 2)

- [ ] 14. FastAPI Routes: /api/jobs, /api/queue, /api/documents

  **What to do**:
  - Implement `backend/api/jobs.py`: `GET /api/jobs` (list jobs with pagination + filters), `GET /api/jobs/{id}` (single job details), `POST /api/jobs/search` (manual trigger Adzuna search — body: keywords+filters), `GET /api/jobs/{id}/score` (recalculate score for a job)
  - Implement `backend/api/queue.py`: `GET /api/queue` (today's morning queue — job_matches with status=new, sorted by score desc), `POST /api/queue/refresh` (trigger a new morning batch run in background), `PATCH /api/queue/{match_id}/skip` (mark as skipped), `PATCH /api/queue/{match_id}/status` (update status)
  - Implement `backend/api/documents.py`: `GET /api/documents/{match_id}/cv/pdf` (stream PDF file), `GET /api/documents/{match_id}/letter/pdf` (stream PDF), `GET /api/documents/{match_id}/diff` (return JSON diff of CV changes), `POST /api/documents/{match_id}/regenerate` (trigger re-generation)
  - All responses use Pydantic response schemas from `backend/models/schemas.py`. All DB queries through `get_db()` dependency. Proper HTTP status codes (404 if not found, 422 for validation errors).

  **Must NOT do**:
  - Do NOT return raw SQLAlchemy ORM objects — always serialize to Pydantic schemas
  - Do NOT stream files synchronously — use `FileResponse` or async streaming

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 — alongside Tasks 12, 13, 15, 16)
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 22, 23 (frontend Queue and Job Detail pages call these routes)
  - **Blocked By**: Tasks 2, 3, 8, 11 (needs models, router structure, Adzuna client, DB layer)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1286-1294 — route files and their responsibilities
  - `JOBPILOT_PLAN.md` lines 1006-1019 — pages table showing what data each route must serve
  - FastAPI FileResponse docs for PDF streaming

  **Acceptance Criteria**:
  - [ ] `curl -s http://localhost:8000/api/queue` returns `{"matches": [], "total": 0}` on empty DB
  - [ ] `curl -s http://localhost:8000/api/jobs/9999` returns 404
  - [ ] `curl -X POST http://localhost:8000/api/queue/refresh` returns `{"status": "started"}` or `{"status": "completed"}`

  **QA Scenarios**:
  ```
  Scenario: Queue endpoint returns correct structure
    Tool: Bash (curl)
    Preconditions: App running, DB initialized (empty)
    Steps:
      1. curl -s http://localhost:8000/api/queue | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'matches' in d and isinstance(d['matches'], list)"
    Expected Result: Passes assert
    Evidence: .sisyphus/evidence/task-14-queue-endpoint.json

  Scenario: Non-existent job returns 404
    Tool: Bash (curl)
    Steps:
      1. STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/jobs/99999)
      2. assert $STATUS == '404'
    Expected Result: 404 status code
    Evidence: .sisyphus/evidence/task-14-404-job.txt
  ```

  **Commit**: NO (group with Wave 2)

- [ ] 15. FastAPI Routes: /api/applications, /api/settings, /api/analytics

  **What to do**:
  - Implement `backend/api/applications.py`: `POST /api/applications` (create application record — body: job_match_id, method), `GET /api/applications` (list with pagination + status filter), `GET /api/applications/{id}` (single application with events), `PATCH /api/applications/{id}` (update status, add notes), `POST /api/applications/{id}/events` (add lifecycle event — heard_back, interview, offer, rejection)
  - Implement `backend/api/settings.py`: `GET /api/settings/profile` / `PUT /api/settings/profile`, `GET /api/settings/search` / `PUT /api/settings/search`, `GET /api/settings/sources` / `PUT /api/settings/sources` (enable/disable + add/remove), `GET /api/settings/status` (returns: Gemini key set yes/no, Adzuna key set yes/no, Tectonic found yes/no, base CV uploaded yes/no)
  - Implement `backend/api/analytics.py`: `GET /api/analytics/summary` (total_apps, apps_this_week, response_rate, avg_match_score — computed from DB), `GET /api/analytics/trends` (apps per day for last 30 days as time series)
  - Implement first-run setup logic: if `GET /api/settings/status` returns any `false`, the frontend should redirect to `/settings`. Add a flag `setup_complete` to the status response.

  **Must NOT do**:
  - Do NOT expose API keys in any GET response — only return `gemini_key_set: true/false`
  - Do NOT implement analytics beyond what DB queries can provide (no ML, no predictions)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 — alongside Tasks 12, 13, 14, 16)
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 24, 25, 27 (frontend Tracker, Settings, Analytics pages)
  - **Blocked By**: Tasks 2, 3, 11 (needs models, router, DB layer)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1286-1294 — route files list
  - `JOBPILOT_PLAN.md` lines 1006-1019 — what each page needs (tracker = Kanban data, settings = all config)
  - `JOBPILOT_PLAN.md` lines 1130-1262 — data model for analytics queries

  **Acceptance Criteria**:
  - [ ] `curl -s http://localhost:8000/api/settings/status` returns JSON with `gemini_key_set`, `adzuna_key_set`, `tectonic_found`, `base_cv_uploaded`, `setup_complete` fields
  - [ ] `curl -s http://localhost:8000/api/analytics/summary` returns JSON with `total_apps`, `response_rate`
  - [ ] PATCH to update application status is reflected in GET

  **QA Scenarios**:
  ```
  Scenario: Settings status returns expected structure
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8000/api/settings/status | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'gemini_key_set' in d and 'setup_complete' in d"
    Expected Result: Passes assert
    Evidence: .sisyphus/evidence/task-15-settings-status.json
  ```

  **Commit**: NO (group with Wave 2)

- [ ] 16. WebSocket Manager + FastAPI WS Route (/ws)

  **What to do**:
  - Complete `backend/api/ws.py`: `ConnectionManager` class with active connections dict. Implement `/ws` WebSocket endpoint: accept → register → loop receive → route incoming messages to handlers → on disconnect → cleanup.
  - Incoming message handlers: `confirm_submit` → signal the `ApplicationEngine` waiting coroutine; `cancel_apply` → signal cancel; `login_done` → signal `BrowserSessionManager` waiting coroutine. Use `asyncio.Event` objects for signaling across coroutines.
  - Add `broadcast_status` helper function used by scraper/batch: `await ws_manager.broadcast(WSMessage(type='scraping_status', message='...', progress=0.5))`. This function must be importable from a singleton manager instance.
  - Write a minimal integration test: connect a test WebSocket client, send a `ping` message, verify `pong` response within 1s.

  **Must NOT do**:
  - Do NOT block the main event loop in WebSocket handlers
  - Do NOT store sensitive data in WebSocket messages

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2)
  - **Parallel Group**: Wave 2 (with Tasks 12, 13, 14, 15)
  - **Blocks**: Tasks 17, 19, 20 (all emit WS status messages)
  - **Blocked By**: Task 5 (needs WS message protocol)

  **References**:
  - `JOBPILOT_PLAN.md` lines 914-936 — WebSocket confirmation flow for apply_review
  - Task 5 output — WS message Pydantic models

  **Acceptance Criteria**:
  - [ ] WebSocket connects without error
  - [ ] Broadcaster can be called from background coroutines
  - [ ] `uv run pytest tests/test_websocket.py -v` passes

  **QA Scenarios**:
  ```
  Scenario: WebSocket ping-pong roundtrip
    Tool: Bash
    Preconditions: App running
    Steps:
      1. python3 -c "import asyncio,websockets,json; async def t(): async with websockets.connect('ws://localhost:8000/ws') as ws: await ws.send(json.dumps({'type':'ping'})); msg=json.loads(await asyncio.wait_for(ws.recv(), timeout=2)); assert msg['type']=='pong'; asyncio.run(t())"
    Expected Result: pong received within 2s
    Evidence: .sisyphus/evidence/task-16-ws-pingpong.txt
  ```

  **Commit**: YES (commit Wave 2 after Tasks 12-16 all complete)
  - Message: `feat(pipeline): LaTeX CV pipeline, job matching, FastAPI routes, WebSocket`

<!-- WAVE 3: Scraping + Apply + Morning Batch -->

- [ ] 17. Adaptive browser-use Scraper (Generic + Adzuna Orchestrator)

  **What to do**:
  - Create `backend/scraping/adaptive_scraper.py`: `AdaptiveScraper` class exactly as in JOBPILOT_PLAN.md section 5.1 — `scrape_job_listings(url, keywords, max_jobs=20) -> list[RawJob]` and `scrape_job_details(job_url) -> JobDetails`. Use `ChatGoogle` from `browser_use`. Set `max_steps=15` on all listing agents, `max_steps=10` on detail agents. Parse agent result with robust JSON extraction (handle extra text around JSON block).
  - Create `backend/scraping/site_prompts.py`: all site-specific prompt templates from JOBPILOT_PLAN.md section 5.1 — `linkedin`, `indeed`, `google_jobs`, `lab_website`, `generic`. Add `format_prompt(site: str, **kwargs) -> str` helper.
  - Complete `backend/scraping/orchestrator.py`: `ScrapingOrchestrator.run_morning_batch()` exactly as in section 5.1 — Phase 1 (API sources parallel), Phase 2 (browser sources sequential with 3-8s delay), Phase 3 (lab URLs parallel). Emit WebSocket status messages at each phase.
  - Create `tests/test_scraping.py`: mock browser-use Agent to return canned results. Test: (1) orchestrator returns merged+deduped list, (2) one source failure doesn't abort others, (3) lab URLs scraped in parallel, (4) _parse_agent_result handles malformed JSON gracefully (falls back to empty list)

  **Must NOT do**:
  - Do NOT run browser-use Agents without `max_steps` parameter
  - Do NOT run parallel browser-use agents for login-required sites
  - Do NOT scrape LinkedIn in this task — use generic prompts only (LinkedIn-specific flow is Task 30)
  - Do NOT hardcode CSS selectors anywhere in this module

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: browser-use integration with proper error handling, async orchestration, and robust JSON extraction from LLM output is complex
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 — after Wave 2)
  - **Parallel Group**: Wave 3 (with Tasks 18, 19, 20, 21)
  - **Blocks**: Task 19 (morning batch calls the orchestrator)
  - **Blocked By**: Tasks 7, 8, 11, 16 (needs Gemini client, Adzuna client, DB layer, WS manager)

  **References**:
  - `JOBPILOT_PLAN.md` lines 258-484 — full AdaptiveScraper + ScrapingOrchestrator implementation
  - `JOBPILOT_PLAN.md` lines 351-387 — site prompt templates to copy
  - browser-use GitHub for current API: `https://github.com/browser-use/browser-use` (check current `Agent` constructor — `max_steps` parameter)
  - `JOBPILOT_PLAN.md` lines 174-209 — browser-use + ChatGoogle usage examples

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_scraping.py -v` — all tests pass
  - [ ] One source failure returns partial results (not an exception)
  - [ ] `_parse_agent_result` never throws on malformed agent output (graceful empty list)

  **QA Scenarios**:
  ```
  Scenario: Scraping orchestrator gracefully skips failing source
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_scraping.py::test_one_source_failure_continues -v
    Expected Result: Returns partial results from other sources, logs warning
    Evidence: .sisyphus/evidence/task-17-graceful-skip.txt

  Scenario: Malformed agent JSON handled gracefully
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_scraping.py::test_malformed_agent_json -v
    Expected Result: Returns empty list, no exception raised
    Evidence: .sisyphus/evidence/task-17-malformed-json.txt
  ```

  **Commit**: NO (group with Wave 3)

- [ ] 18. Browser Session Manager (Persistent Login + UI Flow)

  **What to do**:
  - Create `backend/scraping/session_manager.py`: `BrowserSessionManager` class from JOBPILOT_PLAN.md section 5.1. `SESSIONS_DIR = Path('data/browser_sessions')`. `get_or_create_session(site: str) -> Browser` — check for saved `{site}_state.json`, return Browser with `storage_state` if exists, otherwise trigger login flow.
  - Login flow coordination: when no session exists, `get_or_create_session()` emits a `login_required` WebSocket message (site name, instructions), then awaits an `asyncio.Event` that gets set when the frontend sends `login_done` message. After event fires, save the browser state via `await browser.context.storage_state(path=str(storage_path))`. Timeout the wait at 10 minutes.
  - Add `list_sessions() -> list[SessionInfo]` and `clear_session(site: str)` (for Settings page to show login status and allow clearing sessions)
  - Write `tests/test_session_manager.py`: test session file loading (mock file exists), test session file saving, test login flow emits WS message (mock WS manager)

  **Must NOT do**:
  - Do NOT store passwords or raw credentials anywhere
  - Do NOT time out the login wait before 10 minutes (user needs time to complete 2FA)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3)
  - **Parallel Group**: Wave 3 (with Tasks 17, 19, 20, 21)
  - **Blocks**: Tasks 19, 30 (morning batch and LinkedIn prompts need session manager)
  - **Blocked By**: Tasks 5, 16 (needs WS protocol and WS manager)

  **References**:
  - `JOBPILOT_PLAN.md` lines 392-423 — BrowserSessionManager implementation
  - `JOBPILOT_PLAN.md` lines 406-410 — storage_state usage pattern
  - Playwright storage_state docs: `https://playwright.dev/python/docs/auth`

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_session_manager.py -v` — all tests pass
  - [ ] `get_or_create_session('test')` with existing session file returns Browser without emitting WS message
  - [ ] `get_or_create_session('test')` without session file emits `login_required` WS message

  **QA Scenarios**:
  ```
  Scenario: Existing session file used without triggering login flow
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_session_manager.py::test_existing_session_no_login_flow -v
    Expected Result: No WS message emitted, Browser returned with storage_state
    Evidence: .sisyphus/evidence/task-18-existing-session.txt
  ```

  **Commit**: NO (group with Wave 3)

- [ ] 19. Morning Batch Scheduler (APScheduler + Full Pipeline Orchestration)

  **What to do**:
  - Create `backend/scheduler/morning_batch.py`: `MorningBatchScheduler` class exactly from JOBPILOT_PLAN.md section 5.6. Use `AsyncIOScheduler` but trigger the batch via `asyncio.create_task()` to avoid blocking the FastAPI event loop. Cron trigger from `settings.batch_time` (default `'08:00'`).
  - Complete `run_batch()` method with all 5 steps: scrape → match/rank → store new matches → pre-generate CVs for top N (where N = remaining daily limit) → emit ready status. Use `await ws_manager.broadcast()` at each step.
  - Add `DailyLimitGuard` class from section 5.4 to `backend/applier/daily_limit.py`.
  - Wire scheduler into FastAPI `lifespan` in `main.py`: start scheduler on startup, stop on shutdown.
  - Create `POST /api/queue/refresh` endpoint to manually trigger `run_batch()` immediately (for development and manual re-runs).
  - Write `tests/test_morning_batch.py`: mock all dependencies. Test: (1) batch runs all 5 steps in order, (2) stops after daily limit reached, (3) CV generation failure for one job doesn't abort others, (4) status messages emitted at each step

  **Must NOT do**:
  - Do NOT run batch synchronously in APScheduler thread — use `asyncio.create_task`
  - Do NOT pre-generate CVs beyond the daily limit
  - Do NOT silently swallow CV generation errors — log them

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3)
  - **Parallel Group**: Wave 3 (with Tasks 17, 18, 20, 21)
  - **Blocks**: Task 22 (Morning Queue frontend needs this to populate data)
  - **Blocked By**: Tasks 12, 13, 14, 17 (needs pipeline, editors, routes, scraper)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1057-1125 — MorningBatchScheduler implementation to follow
  - `JOBPILOT_PLAN.md` lines 983-1001 — DailyLimitGuard implementation
  - APScheduler AsyncIOScheduler docs with FastAPI lifespan pattern

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_morning_batch.py -v` — all tests pass
  - [ ] `POST /api/queue/refresh` returns 200 and batch completes without unhandled exception
  - [ ] After running batch with Adzuna (real API key required for this test), job_matches table has entries

  **QA Scenarios**:
  ```
  Scenario: Manual queue refresh emits WS status messages
    Tool: Bash (pytest + mock WS)
    Steps:
      1. uv run pytest tests/test_morning_batch.py::test_batch_emits_status_messages -v
    Expected Result: At minimum 3 distinct WS message types emitted (scraping, matching, ready)
    Evidence: .sisyphus/evidence/task-19-ws-messages.txt

  Scenario: CV generation failure doesn't abort batch
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_morning_batch.py::test_cv_error_continues_batch -v
    Expected Result: Batch completes, failed job logged but others processed
    Evidence: .sisyphus/evidence/task-19-cv-error-recovery.txt
  ```

  **Commit**: NO (group with Wave 3)

- [ ] 20. Application Engine (Auto/Assisted/Manual + Daily Limit)

  **What to do**:
  - Create `backend/applier/engine.py`: `ApplicationEngine` class from JOBPILOT_PLAN.md section 5.4. Route to `_auto_apply()`, `_assisted_apply()`, `_manual_apply()` based on `ApplyMode` enum.
  - Create `backend/applier/auto_apply.py`: `_auto_apply()` — browser-use Agent fills the form (`max_steps=25`), emits `apply_review` WS message with screenshot + filled fields, awaits user `confirm_submit` or `cancel_apply` event (timeout 5 minutes), then either submits or cancels.
  - Create `backend/applier/assisted_apply.py`: `_assisted_apply()` — browser-use Agent pre-fills basic fields, opens browser in headful mode, notifies user to take over, returns `ApplicationResult(status='assisted')`.
  - Create `backend/applier/manual_apply.py`: `_manual_apply()` — opens URL in user's default browser via `webbrowser.open()`, returns `ApplicationResult(status='manual', message=f'Opened {url}. Docs saved to {docs_dir}')`.
  - Implement `ApplicantInfo` Pydantic model: `full_name`, `email`, `phone`, `location`, `additional_answers` (JSON dict for common Q&A).
  - Write `tests/test_apply_engine.py`: mock browser-use Agent. Test: (1) auto_apply sends WS message with apply_review type, (2) cancel_apply cancels the application, (3) manual_apply calls webbrowser.open with correct URL, (4) daily limit exceeded → raises DailyLimitExceeded

  **Must NOT do**:
  - Do NOT auto-submit without user confirmation — always pause and wait for `confirm_submit`
  - Do NOT run apply agent without `max_steps=25`
  - Do NOT use old `UploadFileAction` pattern — use current browser-use Tools decorator API

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Application engine has complex async coordination (WS events + browser-use + user confirmation flow) with multiple failure modes
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3)
  - **Parallel Group**: Wave 3 (with Tasks 17, 18, 19, 21)
  - **Blocks**: Task 23 (Job Detail page apply buttons trigger this)
  - **Blocked By**: Tasks 5, 12, 16 (needs WS protocol, CV PDFs, WS manager)

  **References**:
  - `JOBPILOT_PLAN.md` lines 857-977 — full ApplicationEngine implementation
  - `JOBPILOT_PLAN.md` lines 913-937 — auto_apply WS confirmation flow
  - browser-use current Tools API (check GitHub README — file upload changed from UploadFileAction to Tools decorator)

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_apply_engine.py -v` — all tests pass
  - [ ] `cancel_apply` event cancels the application without submitting the form
  - [ ] Manual apply opens `webbrowser.open()` with correct URL
  - [ ] DailyLimitGuard prevents application when limit reached

  **QA Scenarios**:
  ```
  Scenario: Auto-apply sends review message before submit
    Tool: Bash (pytest + mock WS + mock browser-use)
    Steps:
      1. uv run pytest tests/test_apply_engine.py::test_auto_apply_sends_review_message -v
    Expected Result: apply_review WS message sent, form NOT submitted yet
    Evidence: .sisyphus/evidence/task-20-apply-review-message.txt

  Scenario: Daily limit blocks application
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_apply_engine.py::test_daily_limit_exceeded -v
    Expected Result: DailyLimitExceeded exception or 429 API response
    Evidence: .sisyphus/evidence/task-20-daily-limit.txt
  ```

  **Commit**: NO (group with Wave 3)

- [ ] 21. FastAPI Routes: /api/applications finalization + Wire All Modules

  **What to do**:
  - Complete `backend/api/applications.py`: Add `POST /api/applications/{id}/apply` endpoint that receives `{method: 'auto'|'assisted'|'manual'}`, retrieves job_match and tailored documents from DB, calls `ApplicationEngine.apply()`, stores Application record, returns `ApplicationResult`.
  - Wire all module singletons in `backend/main.py` lifespan: instantiate `GeminiClient`, `AdzunaClient`, `AdaptiveScraper`, `BrowserSessionManager`, `ScrapingOrchestrator`, `JobMatcher`, `CVPipeline`, `LetterPipeline`, `ApplicationEngine`, `MorningBatchScheduler`. Store as app state. Inject via FastAPI dependencies.
  - Add `backend/api/deps.py` additions: dependency functions for each singleton (e.g., `def get_cv_pipeline(request: Request) -> CVPipeline`)
  - Smoke test: boot the full app with all modules wired, verify health endpoint returns `{"db": "connected", "tectonic": true, "gemini_key_set": true}`

  **Must NOT do**:
  - Do NOT instantiate multiple instances of Gemini client — singleton only
  - Do NOT start the morning batch scheduler in tests — mock it

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 — alongside 17, 18, 19, 20)
  - **Parallel Group**: Wave 3
  - **Blocks**: All Wave 4 tasks (frontend pages need all routes to be real)
  - **Blocked By**: Tasks 12, 13, 14, 15, 17, 18, 19, 20 (all modules needed for wiring)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1267-1401 — full project structure showing what main.py should orchestrate
  - FastAPI app state pattern for singletons

  **Acceptance Criteria**:
  - [ ] `uv run python start.py` launches without import errors
  - [ ] `curl -s http://localhost:8000/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok' and d.get('db')=='connected'"` passes
  - [ ] `POST /api/applications/1/apply` with mock data returns `{status: 'manual', method: 'manual'}` or appropriate response

  **QA Scenarios**:
  ```
  Scenario: Full app starts with all modules
    Tool: Bash
    Steps:
      1. uv run python start.py &; sleep 5
      2. curl -s http://localhost:8000/api/health
      3. kill %1
    Expected Result: health returns {status: 'ok', db: 'connected', tectonic: true/false, gemini_key_set: true}
    Evidence: .sisyphus/evidence/task-21-full-startup.json
  ```

  **Commit**: YES (commit Wave 3 after Tasks 17-21 all complete)
  - Message: `feat(scraping): adaptive scraper, session manager, morning batch, apply engine`

<!-- WAVE 4: Frontend Pages -->

- [ ] 22. Morning Queue Page (Job Cards, Score Badges, Action Buttons)

  **What to do**:
  - Implement `frontend/src/routes/+page.svelte`: Morning Queue page. On load, fetch `GET /api/queue` and display all `job_matches` sorted by score.
  - Create `frontend/src/lib/components/JobCard.svelte`: card component showing: match score badge (color-coded: green 80+, yellow 60-80, red <60), job title + company + location, salary if available, time since posted, source badge, four action buttons: `[Preview CV]`, `[Preview Letter]`, `[Apply]` (with method selector dropdown — Auto/Assisted/Manual), `[Skip]`.
  - Create `frontend/src/lib/components/ScoreIndicator.svelte`: circular badge with score 0-100, color gradient.
  - Implement the 'Apply' flow in the queue: clicking Apply → method selector → fires `POST /api/applications/{id}/apply`, listens on WebSocket for `apply_review` message → shows confirmation modal with filled fields + screenshot, has 'Confirm Submit' and 'Cancel' buttons that send WS messages.
  - Show batch status header: `"Tuesday, Feb 28 · 7 remaining today · 23 matches found"` using data from `GET /api/analytics/summary`.
  - `[Preview CV]` and `[Preview Letter]` open a modal with the PDF viewer (use `<iframe src='/api/documents/{id}/cv/pdf'>` with fallback to download link).
  - Empty state: show 'No matches today. Trigger a search?' with a 'Refresh Queue' button that calls `POST /api/queue/refresh` and shows progress via WebSocket.
  - Replicate the Morning Queue mockup from JOBPILOT_PLAN.md lines 1023-1044 as closely as possible.

  **Must NOT do**:
  - Do NOT show analytics charts on this page (those are on /analytics)
  - Do NOT implement the Kanban tracker here (that's Task 24)
  - Do NOT auto-submit without the confirmation modal

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]
    - `frontend-design`: The Morning Queue is the primary daily-use screen — it must be polished and functional

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4 — after Wave 3)
  - **Parallel Group**: Wave 4 (with Tasks 23, 24, 25, 26, 27)
  - **Blocks**: Task F3 (final QA needs this page to work)
  - **Blocked By**: Tasks 10, 14, 20 (needs layout, /api/queue route, apply engine)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1023-1044 — Morning Queue mockup to replicate
  - `JOBPILOT_PLAN.md` lines 1006-1019 — page purpose and route
  - `JOBPILOT_PLAN.md` lines 1354-1360 — component files (JobCard.svelte, ScoreIndicator.svelte)

  **Acceptance Criteria**:
  - [ ] Page renders without JavaScript errors
  - [ ] At least one job card renders when queue has data (verified via Playwright)
  - [ ] 'Skip' button updates job_match status to 'skipped' (verify via `GET /api/queue` — skipped job not in list)
  - [ ] Empty state shows 'No matches today' message

  **QA Scenarios**:
  ```
  Scenario: Morning Queue renders job cards from API
    Tool: Playwright
    Preconditions: App running, at least one job_match in DB (seeded for test)
    Steps:
      1. page.goto('http://localhost:8000')
      2. page.wait_for_selector('.job-card', timeout=5000)
      3. assert page.locator('.job-card').count() >= 1
      4. assert page.locator('.score-badge').first.is_visible()
    Expected Result: Job cards visible with score badges
    Evidence: .sisyphus/evidence/task-22-morning-queue.png

  Scenario: Empty queue shows appropriate empty state
    Tool: Playwright
    Preconditions: Empty job_matches table
    Steps:
      1. page.goto('http://localhost:8000')
      2. page.wait_for_selector('[data-testid="empty-queue"]', timeout=3000)
      3. assert 'No matches today' in page.locator('[data-testid="empty-queue"]').inner_text()
    Expected Result: Empty state message visible
    Evidence: .sisyphus/evidence/task-22-empty-queue.png
  ```

  **Commit**: NO (group with Wave 4)

- [ ] 23. Job Detail Page (Full Description + CV Diff + Apply Buttons)

  **What to do**:
  - Implement `frontend/src/routes/jobs/[id]/+page.svelte`: full job detail view. Load data: `GET /api/jobs/{id}` (full description, requirements, benefits, apply_url, apply_method) + `GET /api/documents/{match_id}/diff` (what LLM changed in CV).
  - Create `frontend/src/lib/components/CVPreview.svelte`: two-pane component — left: PDF iframe preview of the tailored CV, right: diff view showing original vs edited text for each changed section (green = added/changed, crossed-out = removed). Fallback: download link if iframe fails.
  - Show job metadata prominently: company, location, salary, posted date, source, match score, apply method (auto/assisted/manual detected from job details).
  - Three apply action buttons: `[Auto Apply]` (only for Easy Apply jobs), `[Assisted Apply]`, `[Open & Apply]` (manual). Each button triggers `POST /api/applications/{id}/apply` with appropriate method.
  - Show breadcrumb: `Morning Queue > ML Engineer @ DeepMind`.
  - Back button returns to Morning Queue.

  **Must NOT do**:
  - Do NOT build a PDF editor — read-only PDF preview only
  - Do NOT implement CV editing from this page (that's the CV Manager, Task 26)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4)
  - **Parallel Group**: Wave 4 (with Tasks 22, 24, 25, 26, 27)
  - **Blocks**: Task F3 (final QA navigates to job detail)
  - **Blocked By**: Tasks 10, 14, 15, 20 (needs layout, job routes, documents routes, apply engine)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1019 — Job Detail page description
  - `JOBPILOT_PLAN.md` lines 1047-1053 — tech details: pdf.js, WebSocket
  - `JOBPILOT_PLAN.md` lines 1355-1356 — CVPreview.svelte component

  **Acceptance Criteria**:
  - [ ] Page renders for a valid job ID without JS errors
  - [ ] PDF preview component renders (even if empty/placeholder PDF)
  - [ ] Diff section shows at least 'No changes' state if no diff data
  - [ ] All three apply buttons are present (auto/assisted/manual)

  **QA Scenarios**:
  ```
  Scenario: Job detail page renders with full data
    Tool: Playwright
    Preconditions: DB has at least 1 job_match with ID 1
    Steps:
      1. page.goto('http://localhost:8000/jobs/1')
      2. page.wait_for_load_state('networkidle')
      3. assert page.locator('h1.job-title').is_visible()
      4. assert page.locator('.cv-preview').is_visible()
      5. assert page.locator('[data-testid="btn-manual-apply"]').is_visible()
    Expected Result: All major sections visible
    Evidence: .sisyphus/evidence/task-23-job-detail.png
  ```

  **Commit**: NO (group with Wave 4)

- [ ] 24. Application Tracker Page (Kanban Drag-and-Drop)

  **What to do**:
  - Implement `frontend/src/routes/tracker/+page.svelte`: Kanban board with 5 columns: Applied, Heard Back, Interview, Offer, Rejected. Load data from `GET /api/applications`.
  - Create `frontend/src/lib/components/KanbanBoard.svelte`: drag-and-drop between columns using the HTML5 drag API (no heavy DnD library needed). Each card shows: company, job title, date applied, apply method badge (auto/assisted/manual). Dragging a card to a new column fires `PATCH /api/applications/{id}` to update status.
  - Each application card has a 'Add Note' button that opens an inline input to `POST /api/applications/{id}/events` with event_type=`note`.
  - A small 'Add Event' button on each card opens a dropdown: Heard Back / Interview / Offer / Rejection — fires corresponding event.
  - Column headers show count badge (e.g., `Applied (5)`).

  **Must NOT do**:
  - Do NOT use a third-party DnD library (keep bundle size lean)
  - Do NOT implement sorting or filtering in MVP

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4)
  - **Parallel Group**: Wave 4
  - **Blocks**: Task F3 (final QA checks tracker)
  - **Blocked By**: Tasks 10, 15 (needs layout and applications routes)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1019 — tracker page: `Kanban board: Applied → Heard Back → Interview → Offer → Rejected`
  - `JOBPILOT_PLAN.md` lines 1356 — KanbanBoard.svelte component file

  **Acceptance Criteria**:
  - [ ] All 5 Kanban columns render
  - [ ] Column headers show correct count badges
  - [ ] Dragging a card between columns fires PATCH API and updates column counts

  **QA Scenarios**:
  ```
  Scenario: Kanban columns render with application cards
    Tool: Playwright
    Preconditions: At least 2 applications in DB (seeded)
    Steps:
      1. page.goto('http://localhost:8000/tracker')
      2. page.wait_for_load_state('networkidle')
      3. assert page.locator('.kanban-column').count() == 5
      4. assert page.locator('[data-column="applied"] .kanban-card').count() >= 0
    Expected Result: 5 columns visible, cards shown
    Evidence: .sisyphus/evidence/task-24-kanban.png
  ```

  **Commit**: NO (group with Wave 4)

- [ ] 25. Settings Page (Keywords, Filters, Sources, API Keys, Profile)

  **What to do**:
  - Implement `frontend/src/routes/settings/+page.svelte`: comprehensive settings page with tabs: Profile, Search, Sources, System.
  - **Profile tab**: form fields for full_name, email, phone, location, additional_answers (JSON textarea). Loads from `GET /api/settings/profile`, saves with `PUT /api/settings/profile`.
  - **Search tab**: multi-input for keywords (chip input — add/remove keywords), multi-select for locations, salary_min slider, experience range slider, remote_only toggle, job_types checkboxes, languages checkboxes, excluded_keywords chip input, excluded_companies chip input. Loads from `GET /api/settings/search`, saves with `PUT /api/settings/search`.
  - **Sources tab**: list of job sources with toggle (enable/disable), current login status for browser sources (green = logged in, red = not). 'Login' button for each browser source triggers `GET /api/settings/sources/{name}/login` which starts the session manager login flow. Custom lab URL input (paste one URL, add to list).
  - **System tab**: shows API key status badges (set/not set). Link to `.env` file location for manual editing. Daily limit number input. Batch time picker. 'Download Tectonic' button if not found. CV template upload (file input for .tex file).
  - First-run wizard: if `setup_complete == false` from `GET /api/settings/status`, show an onboarding overlay that guides through: (1) Set API keys in .env, (2) Upload CV template, (3) Set keywords. Show progress steps.

  **Must NOT do**:
  - Do NOT display actual API key values anywhere on the page
  - Do NOT implement analytics or job search from this page

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4)
  - **Parallel Group**: Wave 4
  - **Blocked By**: Tasks 10, 15 (needs layout and settings routes)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1019 — Settings page purpose
  - `JOBPILOT_PLAN.md` lines 1057-1060 — MorningBatchScheduler config (batch_time)
  - `JOBPILOT_PLAN.md` lines 1147-1176 — `search_settings` + `job_sources` DB schema for form mapping

  **Acceptance Criteria**:
  - [ ] All 4 tabs render without errors
  - [ ] Saving search settings calls PUT /api/settings/search and shows success toast
  - [ ] First-run overlay shown when setup_complete is false
  - [ ] System tab shows correct API key status (set/not set based on actual .env)

  **QA Scenarios**:
  ```
  Scenario: Settings page renders all 4 tabs
    Tool: Playwright
    Preconditions: App running
    Steps:
      1. page.goto('http://localhost:8000/settings')
      2. page.wait_for_load_state('networkidle')
      3. for tab in ['Profile', 'Search', 'Sources', 'System']: assert page.locator(f'[data-tab="{tab.lower()}"]').is_visible()
    Expected Result: All 4 tab buttons visible
    Evidence: .sisyphus/evidence/task-25-settings-tabs.png

  Scenario: First-run wizard shown on fresh install
    Tool: Playwright
    Preconditions: .env without GOOGLE_API_KEY set
    Steps:
      1. page.goto('http://localhost:8000/settings')
      2. page.wait_for_selector('[data-testid="setup-wizard"]', timeout=3000)
    Expected Result: Setup wizard overlay visible
    Evidence: .sisyphus/evidence/task-25-setup-wizard.png
  ```

  **Commit**: NO (group with Wave 4)

- [ ] 26. CV Manager Page (Upload LaTeX, Preview PDF, Edit History)

  **What to do**:
  - Implement `frontend/src/routes/cv/+page.svelte`: CV Manager page.
  - Upload section: file drag-and-drop area for `.tex` file upload. On upload, calls `PUT /api/settings/profile` with `base_cv_path`. Show success message.
  - Preview section: shows the base CV compiled to PDF. On first load, compiles the uploaded template via `POST /api/documents/preview` and shows the PDF in an iframe.
  - Edit history section: list of all `tailored_documents` for this user (from `GET /api/documents/history`) — each row shows: job title, company, date generated, 'View PDF' and 'View Diff' buttons.
  - Marker detection: after upload, call a new endpoint `POST /api/documents/validate-template` that runs the LaTeX parser and returns `{has_markers: true/false, warnings: []}`. Show a banner: 'JOBPILOT markers detected ✓' or 'No markers found — add them for better tailoring' with a copyable code snippet.

  **Must NOT do**:
  - Do NOT implement LaTeX editing in the browser — read-only preview only
  - Do NOT show CV edit history from other users (single-user app)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4)
  - **Parallel Group**: Wave 4
  - **Blocked By**: Tasks 10, 12, 15 (needs layout, LaTeX pipeline, documents routes)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1019 — CV Manager page purpose
  - `JOBPILOT_PLAN.md` lines 641-665 — LaTeX markers format (for the 'add markers' code snippet)
  - `JOBPILOT_PLAN.md` lines 1345-1347 — CV Manager route file

  **Acceptance Criteria**:
  - [ ] File upload area accepts .tex files
  - [ ] After upload, marker detection banner shows correct state
  - [ ] Edit history shows rows for each tailored document

  **QA Scenarios**:
  ```
  Scenario: CV template upload and marker detection
    Tool: Playwright
    Preconditions: App running, sample_cv.tex with markers as test fixture
    Steps:
      1. page.goto('http://localhost:8000/cv')
      2. page.set_input_files('input[type="file"]', 'tests/fixtures/sample_cv.tex')
      3. page.wait_for_selector('[data-testid="markers-detected"]', timeout=5000)
      4. assert 'JOBPILOT markers detected' in page.locator('[data-testid="markers-detected"]').inner_text()
    Expected Result: Marker detection banner shows success state
    Evidence: .sisyphus/evidence/task-26-cv-upload.png
  ```

  **Commit**: NO (group with Wave 4)

- [ ] 27. Analytics Page + First-Run Setup Wizard Component

  **What to do**:
  - Implement `frontend/src/routes/analytics/+page.svelte`: Analytics dashboard. Load `GET /api/analytics/summary` (totals) and `GET /api/analytics/trends` (time series).
  - Display: (1) 4 stat cards: Total Applications, Response Rate %, Avg Match Score, Applications This Week. (2) Bar chart for 'Applications per day (last 30 days)' using a minimal SVG chart (no heavy charting library — hand-coded SVG bars or use shadcn-svelte's Chart component if available). (3) Source breakdown: pie/donut or horizontal bars showing which source (Adzuna, LinkedIn, etc.) produced the most applications.
  - Finalize the first-run setup wizard component `frontend/src/lib/components/SetupWizard.svelte`: 3-step modal overlay: Step 1 = check .env and show which keys are missing with a copy-paste snippet; Step 2 = upload CV template; Step 3 = set initial keywords. Progress indicator at top. 'Skip for now' on each step.

  **Must NOT do**:
  - Do NOT use heavy charting libraries (Chart.js, D3.js) — SVG or shadcn-svelte built-ins only
  - Do NOT predict or forecast future applications

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-design`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 4)
  - **Parallel Group**: Wave 4
  - **Blocked By**: Tasks 10, 15 (needs layout and analytics routes)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1006-1019 — Analytics page purpose
  - `JOBPILOT_PLAN.md` lines 1349-1351 — analytics route file

  **Acceptance Criteria**:
  - [ ] All 4 stat cards render with numbers from API
  - [ ] Bar chart renders even with 0 data (empty state)
  - [ ] Setup wizard shows all 3 steps with progress indicator

  **QA Scenarios**:
  ```
  Scenario: Analytics page renders with stats
    Tool: Playwright
    Preconditions: App running, at least 1 application in DB
    Steps:
      1. page.goto('http://localhost:8000/analytics')
      2. page.wait_for_load_state('networkidle')
      3. assert page.locator('.stat-card').count() == 4
      4. assert page.locator('[data-testid="total-apps"]').inner_text() matches /\d+/
    Expected Result: 4 stat cards with numeric values
    Evidence: .sisyphus/evidence/task-27-analytics.png
  ```

  **Commit**: YES (commit Wave 4 after Tasks 22-27 all complete)
  - Message: `feat(ui): all 6 frontend pages with full interactions`

<!-- WAVE 5: Packaging + Polish -->

- [ ] 28. Cross-Platform Installer Scripts (install.sh + install.ps1)

  **What to do**:
  - Create `scripts/install.sh` (Linux): (1) Check Python >= 3.12 available; (2) `curl -LsSf https://astral.sh/uv/install.sh | sh` if uv not found; (3) `uv sync`; (4) `uv run playwright install chromium`; (5) `uv run python scripts/download_tectonic.py`; (6) `cd frontend && npm install && npm run build`; (7) Pre-warm Tectonic by compiling a minimal test .tex file; (8) Create `data/` directories; (9) Copy `.env.example` to `.env` if `.env` doesn't exist; (10) Print success message with `uv run python start.py` instruction.
  - Create `scripts/install.ps1` (Windows PowerShell): same steps adapted for Windows — use `winget install --id=astral.uv -e` or the iex installer for uv; use `.exe` binaries; handle paths with `Join-Path`; at end, create desktop shortcut (optional).
  - Verify both scripts are idempotent (running twice doesn't break anything).
  - Update README.md with installation instructions (one-command install on each platform).

  **Must NOT do**:
  - Do NOT bundle Tectonic binary in git repo — download script handles it
  - Do NOT require admin/sudo for Linux install
  - Do NOT write to system directories — everything in the project directory

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 5 — alongside Tasks 29, 30, 31, 32)
  - **Parallel Group**: Wave 5
  - **Blocks**: Task F3 (final QA tests clean install)
  - **Blocked By**: Wave 4 complete (installer needs frontend to be buildable)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1413-1450 — Linux and Windows installation steps to implement
  - `JOBPILOT_PLAN.md` lines 1428-1434 — what install.ps1 does

  **Acceptance Criteria**:
  - [ ] `bash scripts/install.sh` on a clean Linux machine completes without errors
  - [ ] `uv run python start.py` works after running the installer
  - [ ] Running install.sh twice is idempotent (no errors)

  **QA Scenarios**:
  ```
  Scenario: Linux installer runs successfully
    Tool: Bash
    Preconditions: Clean environment with Python 3.12, Node.js installed
    Steps:
      1. bash scripts/install.sh 2>&1 | tee .sisyphus/evidence/task-28-install-log.txt
      2. Check exit code == 0
      3. ls frontend/build/index.html bin/tectonic data/
    Expected Result: Exit 0, all required files present
    Evidence: .sisyphus/evidence/task-28-install-log.txt
  ```

  **Commit**: NO (group with Wave 5)

- [ ] 29. Tectonic Auto-Download Script + bin/ Directory Setup

  **What to do**:
  - Create `scripts/download_tectonic.py`: cross-platform Tectonic downloader. (1) Detect platform: `sys.platform == 'win32'` (Windows), `sys.platform == 'linux'` (Linux), `sys.platform == 'darwin'` (macOS — bonus). (2) Determine the correct Tectonic GitHub release URL for the detected platform + architecture (x86_64, aarch64). (3) Download the binary from `https://github.com/tectonic-typesetting/tectonic/releases/latest/` to `bin/tectonic` (Linux) or `bin/tectonic.exe` (Windows). (4) Set executable bit on Linux. (5) Verify by running `bin/tectonic --version`.
  - Create `bin/.gitkeep` so the directory is tracked but binaries are gitignored.
  - Update `.gitignore` to include `bin/tectonic` and `bin/tectonic.exe`.
  - Update `backend/latex/compiler.py` `_find_tectonic()` to also check `PATH` before `bin/` (the method already does this per plan — just verify).

  **Must NOT do**:
  - Do NOT commit the binary to git

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 5)
  - **Parallel Group**: Wave 5
  - **Blocked By**: Tasks 28 (install scripts call this script)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1394-1398 — bin/ directory contents and purpose
  - `JOBPILOT_PLAN.md` lines 760-773 — `_find_tectonic()` and binary naming logic
  - Tectonic releases: `https://github.com/tectonic-typesetting/tectonic/releases`

  **Acceptance Criteria**:
  - [ ] `python scripts/download_tectonic.py` downloads Tectonic to `bin/tectonic` on Linux
  - [ ] `bin/tectonic --version` runs successfully after download
  - [ ] Script is idempotent (skips download if binary exists and works)

  **QA Scenarios**:
  ```
  Scenario: Tectonic downloaded and executable
    Tool: Bash
    Steps:
      1. rm -f bin/tectonic && python scripts/download_tectonic.py
      2. bin/tectonic --version
    Expected Result: Version string printed, exit code 0
    Evidence: .sisyphus/evidence/task-29-tectonic-version.txt
  ```

  **Commit**: NO (group with Wave 5)

- [ ] 30. Site-Specific browser-use Prompts (LinkedIn, Indeed, Google Jobs, WTTJ)

  **What to do**:
  - Update `backend/scraping/site_prompts.py`: expand with fully tested prompt templates for: `linkedin`, `indeed`, `google_jobs`, `welcome_to_the_jungle`. Each prompt should be self-contained with detailed instructions for that specific site's layout.
  - For LinkedIn specifically: add a `linkedin_easy_apply` prompt for the Application Engine auto-apply flow. This prompt handles LinkedIn's multi-step Easy Apply modal (personal info → questions → review → submit).
  - Add `SITE_CONFIGS` dict mapping site names to config: `{name, prompt_key, requires_login, apply_method, country_codes}`.
  - Update `backend/scraping/orchestrator.py` `run_morning_batch()` to use `SITE_CONFIGS` for routing browser vs API vs lab_url sources.
  - For Glassdoor, Dice, AngelList: add `generic` prompt (these are lower priority per the plan — labeled as Phase 4 / lower priority).

  **Must NOT do**:
  - Do NOT write hardcoded CSS selectors for any site
  - Do NOT implement scraping code changes — only add/update PROMPT STRINGS in site_prompts.py

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Prompt engineering for specific job sites requires careful instruction design and iteration
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 5)
  - **Parallel Group**: Wave 5
  - **Blocked By**: Task 17 (adaptive scraper must exist to use these prompts)

  **References**:
  - `JOBPILOT_PLAN.md` lines 351-387 — site prompt templates to START from and expand
  - `JOBPILOT_PLAN.md` lines 1638-1650 — Phase 3 LinkedIn + Easy Apply as high priority
  - browser-use docs for multi-step form handling patterns

  **Acceptance Criteria**:
  - [ ] `from backend.scraping.site_prompts import SITE_PROMPTS, SITE_CONFIGS` imports without error
  - [ ] All 4 new site prompts are defined (linkedin, indeed, google_jobs, welcome_to_the_jungle)
  - [ ] LinkedIn Easy Apply prompt includes instructions for multi-step modal

  **QA Scenarios**:
  ```
  Scenario: Site prompts import correctly
    Tool: Bash
    Steps:
      1. python3 -c "from backend.scraping.site_prompts import SITE_PROMPTS, SITE_CONFIGS; print(list(SITE_PROMPTS.keys()))"
    Expected Result: Prints list including 'linkedin', 'indeed', 'google_jobs', 'welcome_to_the_jungle', 'generic', 'lab_website'
    Evidence: .sisyphus/evidence/task-30-prompts-import.txt
  ```

  **Commit**: NO (group with Wave 5)

- [ ] 31. Error Handling, Retry Logic, and Graceful Degradation

  **What to do**:
  - Audit all `backend/` modules for bare `except: pass` blocks and replace with: logged warning + fallback behavior + appropriate exception type.
  - Add retry logic to `backend/scraping/adaptive_scraper.py`: 3 retries with exponential backoff (2s, 4s, 8s) for browser-use agent failures. After 3 failures, log error and return empty list (not exception).
  - Add retry logic to `backend/llm/gemini_client.py`: already has rate limit backoff — add JSON parse retry (ask Gemini to reformat if first response is invalid JSON, max 1 retry).
  - Add global FastAPI exception handlers in `backend/main.py`: (1) `HTTPException` → standard error JSON; (2) `LaTeXCompilationError` → 422 with details; (3) `GeminiJSONError` → 500 with message; (4) Generic `Exception` → 500 with sanitized message (no internal paths in prod).
  - Add LaTeX pipeline fallback: if Gemini editing fails → return base CV without edits (not fail the whole morning batch). Clearly mark job_match record as `cv_tailored=False`.
  - Add graceful degradation for missing Tectonic: `GET /api/health` returns `tectonic: false` with instructions, and PDF-dependent endpoints return 503 with clear message.
  - Write `tests/test_error_handling.py`: test each recovery path: (1) scraper retry on agent failure, (2) LaTeX pipeline falls back to base CV on Gemini error, (3) FastAPI returns structured error JSON, not HTML

  **Must NOT do**:
  - Do NOT expose internal stack traces or file paths in API error responses
  - Do NOT swallow errors silently — always log them
  - Do NOT make the system completely unusable if one optional component (Tectonic, Gemini) fails

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Error handling audit requires understanding all failure modes across the full codebase
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 5)
  - **Parallel Group**: Wave 5
  - **Blocked By**: All Wave 3 and 4 tasks (needs code to exist before auditing it)

  **References**:
  - `JOBPILOT_PLAN.md` lines 1709-1722 — Risk Matrix with specific risks and mitigations to implement as code
  - `JOBPILOT_PLAN.md` lines 453-467 — ScrapingOrchestrator error handling pattern to audit

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/test_error_handling.py -v` — all tests pass
  - [ ] `curl -s http://localhost:8000/api/nonexistent` returns JSON (not HTML) with `{error: ..., code: 404}`
  - [ ] LaTeX pipeline with Gemini mocked to fail still returns a CV (base, unedited)

  **QA Scenarios**:
  ```
  Scenario: API errors return JSON not HTML
    Tool: Bash (curl)
    Steps:
      1. BODY=$(curl -s http://localhost:8000/api/jobs/99999)
      2. python3 -c "import json,sys; d=json.loads('$BODY'); assert 'error' in d or 'detail' in d"
    Expected Result: JSON error response, not HTML
    Evidence: .sisyphus/evidence/task-31-json-errors.txt

  Scenario: Gemini failure falls back to base CV
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/test_error_handling.py::test_latex_pipeline_gemini_fallback -v
    Expected Result: Base CV returned (not an exception)
    Evidence: .sisyphus/evidence/task-31-cv-fallback.txt
  ```

  **Commit**: NO (group with Wave 5)

- [ ] 32. Integration Tests Suite (Backend API + Pipeline End-to-End)

  **What to do**:
  - Create `tests/integration/test_full_pipeline.py`: end-to-end integration tests using real SQLite (in-memory), mocked Gemini, mocked browser-use, real LaTeX parser, real Tectonic (if available).
  - Test suite must cover:
    1. `test_adzuna_to_queue_flow`: Adzuna returns jobs → matcher scores → stored in DB → `/api/queue` returns them
    2. `test_cv_tailoring_pipeline`: job_match in DB → CVPipeline generates tailored .tex → Tectonic compiles → PDF path saved in `tailored_documents`
    3. `test_manual_apply_flow`: apply via manual mode → Application record created with status=manual → event added
    4. `test_morning_batch_end_to_end`: full batch mock (Adzuna + matcher + CV generation) → morning queue populated with top N
    5. `test_settings_persistence`: PUT /api/settings/search → GET /api/settings/search returns same data
    6. `test_websocket_status_messages`: trigger queue refresh → WS messages received in correct order
  - Add `tests/test_api_routes.py`: parameterized tests for all REST endpoints (GET/POST/PATCH/DELETE) — status codes, required fields, pagination.

  **Must NOT do**:
  - Do NOT make real Gemini or Adzuna API calls in tests — mock them
  - Do NOT require Playwright in unit/integration tests

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Integration tests require understanding the full data flow across all modules
  - **Skills**: [`run-tests`]
    - `run-tests`: Needed for coverage configuration and pytest-asyncio best practices

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 5 — can write tests while other Wave 5 tasks run)
  - **Parallel Group**: Wave 5 (with Tasks 28, 29, 30, 31)
  - **Blocks**: Task F2 (code quality review runs the test suite)
  - **Blocked By**: All Wave 3 tasks (all modules must exist for integration testing)

  **References**:
  - All previously implemented modules in `backend/`
  - `JOBPILOT_PLAN.md` lines 1280-1295 — route files and their responsibilities (for parameterized route tests)
  - pytest-asyncio docs for async integration testing patterns

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/ -v` shows ≥80% pass rate
  - [ ] `uv run pytest tests/integration/ -v` — all 6 integration tests pass
  - [ ] `uv run pytest tests/ --cov=backend --cov-report=term-missing` shows ≥60% line coverage

  **QA Scenarios**:
  ```
  Scenario: Full pipeline integration test
    Tool: Bash (pytest)
    Steps:
      1. uv run pytest tests/integration/test_full_pipeline.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-32-integration-tests.txt
      2. Check exit code == 0
    Expected Result: All 6 integration tests pass
    Evidence: .sisyphus/evidence/task-32-integration-tests.txt

  Scenario: Code coverage ≥60%
    Tool: Bash
    Steps:
      1. uv run pytest tests/ --cov=backend --cov-report=term-missing 2>&1 | tail -20
    Expected Result: Total coverage reported ≥60%
    Evidence: .sisyphus/evidence/task-32-coverage.txt
  ```

  **Commit**: YES (commit Wave 5 after Tasks 28-32 all complete)
  - Message: `feat(package): installers, error handling, site prompts, integration tests`

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read files, curl endpoints, run commands). For each "Must NOT have": search codebase for forbidden patterns (chktex usage, absolute paths in DB, LLM raw LaTeX rewriting, missing max_steps on Agents, API keys in DB). Check evidence files exist. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run ruff check backend/` + `uv run pyright backend/` + `cd frontend && npx tsc --noEmit`. Review all Python modules for empty excepts, print() statements, unvalidated LLM outputs. Check Svelte files for `console.log`, `as any`. Run `uv run pytest tests/ -v` — must pass ≥80%.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [ ] F3. **Full QA Smoke Test** — `unspecified-high` (+ `playwright` skill)
  Start the app with `uv run python start.py`. Use Playwright to: (1) Navigate all 6 pages, (2) Trigger a manual queue refresh via the Settings page, (3) Check WebSocket connection indicator in StatusBar, (4) Verify the Morning Queue renders job cards. Save screenshots to `.sisyphus/evidence/final-qa/`.
  Output: `Pages [6/6] | WS [CONNECTED] | Queue [N jobs] | VERDICT`

- [ ] F4. **Scope Fidelity + Security Check** — `deep`
  For each of the 32 tasks: read "What to do", read the actual code diff (`git diff`). Verify nothing was built beyond scope. Search for: hardcoded API keys, credentials in code, absolute paths in DB, chktex binary calls. Verify `.env` is in `.gitignore`. Flag any `TODO: skip for now` comments left in code.
  Output: `Tasks [N/N compliant] | Security [CLEAN/N issues] | VERDICT`

---

## Commit Strategy

Each wave commits independently when all its tasks are complete:

- **Wave 0**: `feat(scaffold): initialize jobpilot project structure and dependencies`
- **Wave 1**: `feat(core): add Gemini client, Adzuna client, LaTeX parser, DB layer, frontend shell`
- **Wave 2**: `feat(pipeline): add LaTeX pipeline, job matching, FastAPI routes, WebSocket`
- **Wave 3**: `feat(scraping): add adaptive scraper, session manager, morning batch, apply engine`
- **Wave 4**: `feat(ui): add all 6 frontend pages with full interactions`
- **Wave 5**: `feat(package): add installers, Tectonic download, error handling, integration tests`

---

## Success Criteria

### Verification Commands
```bash
# App starts successfully
uv run python start.py &
sleep 5

# Health check
curl -s http://localhost:8000/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok', d"

# Adzuna search (requires API key in .env)
curl -s "http://localhost:8000/api/jobs/search?keywords=python+developer&country=gb" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'jobs' in d"

# Frontend served
curl -s http://localhost:8000/ | grep -q "JobPilot"

# DB tables exist
python3 -c "
import sqlite3
c = sqlite3.connect('data/jobpilot.db')
tables = c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
names = [t[0] for t in tables]
assert 'jobs' in names and 'applications' in names and 'user_profile' in names
print('DB OK:', names)
"

# Tests pass
uv run pytest tests/ -v --tb=short
```

### Final Checklist
- [ ] All "Must Have" modules implemented and verified
- [ ] All "Must NOT" prohibitions clean (ruff + pyright + manual search)
- [ ] App starts with `uv run python start.py` on Linux
- [ ] App starts with `uv run python start.py` on Windows (or documented how)
- [ ] `uv run pytest tests/` passes ≥80% of tests
- [ ] All 6 frontend pages render without JavaScript errors
- [ ] `.env` is in `.gitignore`
- [ ] No hardcoded credentials in any file
- [ ] Tectonic binary downloads automatically via install script
- [ ] Alembic migrations can be applied cleanly from scratch: `alembic upgrade head`
