# JobPilot — Developer Guide

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.12+ | Backend runtime |
| Node.js | 18+ | Frontend build |
| uv | latest | Python package manager |
| Git | any | Version control |

---

## Environment Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/jobpilot.git
cd jobpilot

# Install Python dependencies (uv creates .venv automatically)
uv sync

# Install Playwright Chromium
uv run playwright install chromium

# Install frontend dependencies
cd frontend && npm install && cd ..

# Download Tectonic LaTeX engine
uv run python scripts/download_tectonic.py

# Copy environment template
cp .env.example .env
# Edit .env and fill in GOOGLE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY
```

---

## Running the Application

### Development (hot-reload)
```bash
# Backend (hot-reload)
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Frontend dev server (in a separate terminal)
cd frontend && npm run dev
# Frontend available at http://localhost:5173
# Backend available at http://localhost:8000
```

### Production (single server)
```bash
# Build frontend first
cd frontend && npm run build && cd ..

# Start everything (backend serves frontend static files)
uv run python start.py
# App available at http://localhost:8000
```

---

## Running Tests

```bash
# All tests (fast)
uv run pytest tests/ -q

# With coverage
uv run pytest tests/ --cov=backend --cov-report=term-missing

# Single module
uv run pytest tests/test_matcher.py -v

# Integration only
uv run pytest tests/integration/ -v
```

**Test structure:**
```
tests/
├── conftest.py                  Async DB fixtures, test client
├── fixtures/__init__.py
├── integration/
│   └── test_full_pipeline.py    End-to-end pipeline tests
├── test_adzuna_client.py
├── test_api_jobs.py
├── test_api_routes.py           All 24 routes
├── test_apply_engine.py
├── test_deduplicator.py
├── test_error_handling.py
├── test_gemini_client.py        Mocked Gemini calls
├── test_gemini_editors.py
├── test_latex_parser.py
├── test_latex_pipeline.py
├── test_matcher.py
├── test_morning_batch.py
├── test_scraping.py
├── test_session_manager.py
├── test_smoke.py                Basic startup + health
└── test_websocket.py
```

All tests use an in-memory SQLite DB (`sqlite+aiosqlite:///:memory:`) via the `conftest.py` fixtures. No live API calls are made — Gemini and Adzuna are mocked.

---

## Code Quality

```bash
# Lint (ruff)
uv run ruff check backend/ tests/

# Format (ruff)
uv run ruff format backend/ tests/

# Type check (pyright)
uv run pyright backend/

# Frontend lint + type check
cd frontend && npm run check
```

---

## Database Migrations

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Create a new migration (auto-generate from model changes)
uv run alembic revision --autogenerate -m "description"

# Downgrade one step
uv run alembic downgrade -1

# Show current revision
uv run alembic current
```

The DB file is at `data/jobpilot.db` by default (configurable via `JOBPILOT_DATA_DIR`).

---

## Module Guide

### `backend/config.py`
`Settings` is a `pydantic-settings` class loaded from `.env`. Access anywhere via:
```python
from backend.config import settings
settings.GOOGLE_API_KEY
settings.jobpilot_data_dir
```

### `backend/database.py`
```python
from backend.database import AsyncSessionLocal, init_db

# Create tables
await init_db()

# Get a session (in tests or scripts)
async with AsyncSessionLocal() as session:
    result = await session.execute(select(Job))
```

### `backend/api/deps.py`
The `DBSession` dependency provides an `AsyncSession` to route handlers:
```python
from backend.api.deps import DBSession

@router.get("/")
async def my_route(db: DBSession):
    result = await db.execute(select(Job))
```

### `backend/scraping/`

**AdzunaClient** (`adzuna_client.py`):
- `await client.search(keywords, filters, country, results_per_page)` → `List[RawJob]`
- Uses `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` from settings

**AdaptiveScraper** (`adaptive_scraper.py`):
- `await scraper.scrape(site_name, keywords, location)` → `List[RawJob]`
- Uses browser-use + Gemini to navigate and extract jobs from any website
- Site-specific prompts are in `site_prompts.py`

**ScrapingOrchestrator** (`orchestrator.py`):
- `await orchestrator.run_morning_batch()` — runs all enabled sources
- `await orchestrator.scrape_source(source_name)` — single source

**JobDeduplicator** (`deduplicator.py`):
- `dedup.deduplicate(raw_jobs)` → filtered list
- Hash = MD5 of `company|title|location` (lowercased)

### `backend/matching/`

**JobMatcher** (`matcher.py`):
- `await matcher.score_job(job, settings)` → float 0–100
- Score = weighted sum of keyword hits, title match, salary match, location match

**JobFilters** (`filters.py`):
- `filters.passes(job)` → bool
- Hard filters: excluded keywords, excluded companies, remote_only

### `backend/llm/`

**GeminiClient** (`gemini_client.py`):
- `await gemini.generate_json(prompt, schema)` → dict
- Rate-limited: 15 RPM via semaphore + sleep
- Raises `GeminiRateLimitError` or `GeminiJSONError` on failure

**CVEditor** (`cv_editor.py`):
- `await editor.tailor_cv(tex_source, job_description, user_profile)` → diff dict
- Uses `prompts.py` for prompt construction
- Validates response with `validators.py`

### `backend/latex/`

**LaTeX section markers** (required in .tex files):
```latex
%==EXPERIENCE==
\begin{itemize}
  \item ...
\end{itemize}
%==END_EXPERIENCE==
```

**Parser** (`parser.py`):
- `parser.parse(tex_source)` → `{section_name: content}`

**Injector** (`injector.py`):
- `injector.apply_diff(tex_source, diff)` → modified tex string

**Compiler** (`compiler.py`):
- `await compiler.compile(tex_path)` → pdf_path
- Calls `bin/tectonic` subprocess

**CVPipeline** (`pipeline.py`):
```python
result = await cv_pipeline.run(job, user_profile, db)
# result.pdf_path, result.tex_path, result.diff_json
```

### `backend/applier/`

**ApplicationEngine** (`engine.py`):
- Dispatches to `AutoApplier`, `AssistedApplier`, or `ManualApplier` based on job.apply_method
- `engine.signal_confirm(job_id)` — called by WS handler when user confirms
- `engine.signal_cancel(job_id)` — called by WS handler when user cancels

**DailyLimitGuard** (`daily_limit.py`):
- `await guard.check(db)` — raises `DailyLimitReachedError` if at limit

### `backend/scheduler/morning_batch.py`

```python
scheduler = MorningBatchScheduler(
    scraper=orchestrator,
    matcher=matcher,
    cv_pipeline=cv_pipeline,
    db_factory=AsyncSessionLocal,
)
scheduler.start(batch_time="08:00")
scheduler.stop()
```

Uses APScheduler's `BackgroundScheduler` with an asyncio bridge.

---

## Adding a New Job Source

1. Add a site prompt to `backend/scraping/site_prompts.py`:
```python
SITE_PROMPTS["mysite"] = """
Navigate to {url} and extract job listings as JSON with fields:
title, company, location, salary_text, description, url
"""
```

2. Add the source to `job_sources` table via the settings API:
```bash
curl -X POST http://localhost:8000/api/settings/sources \
  -H "Content-Type: application/json" \
  -d '{"name": "mysite", "enabled": true, "config": {"base_url": "https://mysite.com/jobs"}}'
```

The `AdaptiveScraper` will automatically pick up the new source on the next batch run.

---

## Adding a New API Route

1. Create or edit a file in `backend/api/`:
```python
from fastapi import APIRouter
from backend.api.deps import DBSession

router = APIRouter(prefix="/api/myroute", tags=["myroute"])

@router.get("")
async def list_things(db: DBSession):
    ...
```

2. Register in `backend/main.py`:
```python
import backend.api.myroute as myroute
app.include_router(myroute.router)
```

3. Add tests in `tests/test_api_myroute.py` following the pattern in `tests/test_api_routes.py`.

---

## Frontend Development

The frontend is a SvelteKit app in Svelte 5 runes mode (`$state`, `$derived`, `$effect`).

### API calls
All backend calls go through `frontend/src/lib/api.ts`:
```typescript
import { api } from '$lib/api';

const jobs = await api.get<JobListOut>('/api/jobs');
await api.post('/api/applications', { job_id: 1 });
```

### WebSocket
The `websocket.ts` store exposes a reactive WebSocket connection:
```typescript
import { wsStore } from '$lib/stores/websocket';

wsStore.subscribe((msg) => {
  if (msg?.type === 'confirm_apply') { /* show confirm dialog */ }
});

wsStore.send({ type: 'confirm_submit', job_id: 42 });
```

### Adding a new page
1. Create `frontend/src/routes/mypage/+page.svelte`
2. Add nav link in `frontend/src/routes/+layout.svelte`

---

## Git Conventions

Commit message format: `type(scope): description`

| Type | When |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructure, no behavior change |
| `test` | Test additions/changes |
| `docs` | Documentation |
| `chore` | Build, deps, config |

Examples from this project:
```
feat(wave0): database models, config, migrations
feat(wave1): scraping and matching modules
feat(wave2): LaTeX pipeline and LLM integration
feat(wave3): application engine and API routes
feat(wave4): frontend SvelteKit dashboard
feat(wave5): scheduler and integration tests
fix(security): ruff clean, bin/ gitignore, env validation
```
