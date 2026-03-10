# JobPilot — Technical Architecture

## Module Map

```
backend/
├── main.py                  FastAPI app, lifespan, singleton wiring, WS routing
├── config.py                pydantic-settings: GOOGLE_API_KEY, ADZUNA_*, etc.
├── database.py              Async SQLAlchemy engine + session factory
│
├── models/
│   ├── base.py              DeclarativeBase
│   ├── user.py              UserProfile, SearchSettings ORM models
│   ├── job.py               Job, JobMatch, JobSource ORM models
│   ├── document.py          TailoredDocument ORM model
│   ├── application.py       Application, ApplicationEvent ORM models
│   ├── session.py           BrowserSession ORM model
│   ├── schemas.py           Shared Pydantic schemas
│   └── __init__.py
│
├── api/
│   ├── deps.py              DBSession dependency (Annotated[AsyncSession, ...])
│   ├── jobs.py              GET/POST /api/jobs
│   ├── queue.py             GET/PATCH /api/queue
│   ├── applications.py      GET/POST/PATCH /api/applications
│   ├── documents.py         GET/POST/DELETE /api/documents
│   ├── settings.py          GET/PUT /api/settings/{profile,search,sources,status}
│   ├── analytics.py         GET /api/analytics/{summary,trends}
│   ├── ws.py                WebSocket /ws
│   └── ws_models.py         WS message Pydantic models
│
├── scraping/
│   ├── adzuna_client.py     Adzuna REST search client
│   ├── adaptive_scraper.py  browser-use agent for non-Adzuna sites
│   ├── site_prompts.py      Per-site Gemini prompts (LinkedIn, Indeed, etc.)
│   ├── session_manager.py   BrowserSessionManager (persistent cookies)
│   ├── orchestrator.py      ScrapingOrchestrator (coordinates all sources)
│   └── deduplicator.py      MD5-hash-based deduplication
│
├── matching/
│   ├── matcher.py           JobMatcher: keyword scoring → score 0–100
│   └── filters.py           JobFilters: hard exclusion filters
│
├── llm/
│   ├── gemini_client.py     GeminiClient: rate-limited Gemini 2.0 Flash calls
│   ├── cv_editor.py         CVEditor: orchestrates section selection + diff
│   ├── prompts.py           Prompt templates for CV editing
│   └── validators.py        JSON schema validation for LLM responses
│
├── latex/
│   ├── parser.py            Parse .tex → sections (marker-based)
│   ├── injector.py          Apply JSON diff → modified .tex
│   ├── compiler.py          Tectonic subprocess wrapper
│   ├── validator.py         Validate .tex via trial Tectonic compile
│   └── pipeline.py          CVPipeline + LetterPipeline (end-to-end)
│
├── applier/
│   ├── engine.py            ApplicationEngine: dispatch + confirm/cancel gating
│   ├── auto_apply.py        AutoApplier: browser-use form filling
│   ├── assisted_apply.py    AssistedApplier: user-guided browser session
│   ├── manual_apply.py      ManualApplier: record-only (user applies manually)
│   └── daily_limit.py       DailyLimitGuard: count today's applications
│
└── scheduler/
    └── morning_batch.py     MorningBatchScheduler: APScheduler wrapper

frontend/src/
├── routes/
│   ├── +layout.svelte       App shell, navigation sidebar
│   ├── +page.svelte         Morning Queue (home)
│   ├── jobs/[id]/+page.svelte  Job detail view
│   ├── tracker/+page.svelte Kanban application tracker
│   ├── cv/+page.svelte      CV manager + upload
│   ├── settings/+page.svelte Settings (profile, search, sources)
│   └── analytics/+page.svelte Analytics charts
├── lib/
│   ├── api.ts               Typed API client (all REST calls)
│   ├── utils.ts             Shared utilities
│   ├── stores/
│   │   └── websocket.ts     WebSocket Svelte store
│   └── components/
│       ├── JobCard.svelte        Job listing card
│       ├── KanbanBoard.svelte    Drag-and-drop tracker board
│       ├── ScoreIndicator.svelte Match score badge
│       ├── SetupWizard.svelte    First-run wizard
│       └── StatusBar.svelte      Live connection status bar

alembic/
├── env.py                   Async Alembic env
├── versions/
│   ├── 001_initial_schema.py   All 9 tables
│   ├── 002_add_job_sources.py  job_sources table
│   └── 003_add_browser_sessions.py  browser_sessions table
```

---

## Database Schema

### Table: `user_profile`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Always 1 (singleton) |
| full_name | String | Required |
| email | String | Required |
| phone | String | Optional |
| location | String | Optional |
| base_cv_path | String | Path to .tex base CV |
| base_letter_path | String | Path to .tex base letter |
| additional_info | JSON | Extra context for LLM |
| created_at | DateTime | Auto |
| updated_at | DateTime | Auto-updated |

### Table: `search_settings`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Always 1 (singleton) |
| keywords | JSON | `{"include": [...], "must": [...]}` |
| excluded_keywords | JSON | Terms to reject |
| locations | JSON | Target locations |
| salary_min | Integer | Minimum salary filter |
| experience_min/max | Integer | Years of experience filter |
| remote_only | Boolean | Filter |
| job_types | JSON | full-time, contract, etc. |
| languages | JSON | Language requirements |
| excluded_companies | JSON | Blocklist |
| daily_limit | Integer | Default 10 |
| batch_time | String | "08:00" |
| min_match_score | Float | Default 30.0 |

### Table: `jobs`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| title | String | |
| company | String | |
| location | String | |
| salary_text | String | Raw text |
| salary_min/max | Integer | Parsed |
| description | Text | Full JD |
| url | String | Unique listing URL |
| apply_url | String | Direct apply link |
| apply_method | String | auto/assisted/manual |
| posted_at | DateTime | |
| scraped_at | DateTime | |
| dedup_hash | String | MD5 of company|title|location |
| external_id | String | Adzuna ID |
| raw_data | JSON | Full API response |

### Table: `job_matches`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| job_id | FK → jobs | |
| score | Float | 0–100 |
| keyword_hits | JSON | Which keywords matched |
| status | String | new/skipped/applying/applied/rejected |
| batch_date | Date | Which morning batch created this |
| matched_at | DateTime | |

### Table: `tailored_documents`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| job_id | FK → jobs | |
| doc_type | String | cv / letter |
| tex_path | String | Path to .tex output |
| pdf_path | String | Path to .pdf output |
| diff_json | JSON | Gemini's edit diff |
| created_at | DateTime | |

### Table: `applications`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| job_id | FK → jobs | |
| document_id | FK → tailored_documents | |
| status | String | pending/submitted/interview/offer/rejected |
| apply_method | String | auto/assisted/manual |
| created_at | DateTime | |
| updated_at | DateTime | |

### Table: `application_events`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| application_id | FK → applications | |
| event_type | String | submitted/interview/offer/rejected/etc. |
| notes | Text | |
| occurred_at | DateTime | |

### Table: `browser_sessions`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| site | String | Domain |
| cookies_path | String | Path to saved cookies JSON |
| last_used | DateTime | |
| valid | Boolean | |

### Table: `job_sources`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String | adzuna, linkedin, indeed, etc. |
| enabled | Boolean | |
| config | JSON | Per-source configuration |

### Table: `alembic_version`
Internal Alembic migration tracking.

---

## Data Flow: Morning Batch

```
MorningBatchScheduler.run_batch()
    │
    ├─ _load_settings(db)
    │      │
    │      │  ⚠ keywords are stored as JSON dicts, not plain lists:
    │      │    keywords:          {"include": ["python", "ml"]}
    │      │    excluded_keywords:  {"items": ["intern"]}
    │      │    locations:          {"items": ["Paris"]}
    │      │    excluded_companies: {"items": ["ACME"]}
    │      │
    │      │  _extract_json_list() handles all four:
    │      │    - If dict with 'include' key → returns value
    │      │    - If dict with 'items' key → returns value
    │      │    - If already a list → returns as-is
    │      │    - Otherwise → returns []
    │      │
    │      └─▶ keywords: List[str], filters: dict
    │
    ├─ _load_sources(db)
    │      └─ SELECT * FROM job_sources WHERE enabled=True
    │         Returns List[JobSource] — each has .type: "api" | "browser" | "lab_url"
    │
    ├─ ScrapingOrchestrator.run_morning_batch(keywords, filters, sources)
    │      │
    │      ├─ Phase 1: API sources
    │      │    api_sources = [s for s in sources if s.type == "api"]
    │      │    IF api_sources exist → AdzunaClient.search(keywords) per source
    │      │    ELSE IF no api_sources AND adzuna configured AND keywords →
    │      │         AdzunaClient.search(keywords)  ← fallback, always runs
    │      │
    │      ├─ Phase 2: Browser sources
    │      │    browser_sources = [s for s in sources if s.type == "browser"]
    │      │    FOR each → session_manager.get_or_create_session(site)
    │      │             → adaptive_scraper.scrape_job_listings()
    │      │
    │      └─ Phase 3: Deduplicate + store
    │           JobDeduplicator.deduplicate() → INSERT INTO jobs (new only)
    │
    ├─ JobMatcher.score_all(jobs, settings)
    │      → INSERT INTO job_matches (score ≥ min_score)
    │
    └─ CVPipeline.prepare_for_queue(matches, db)
           → INSERT INTO tailored_documents (pre-tailor top N)
```

### Where to modify

| Change | File | Function |
|---|---|---|
| Add a new JSON settings field | `morning_batch.py` | `_extract_json_list()` + `_load_settings()` |
| Change how keywords are parsed | `morning_batch.py` | `_extract_json_list()` |
| Add a new source type | `orchestrator.py` | `run_morning_batch()` — add a new phase block |
| Change Adzuna fallback logic | `orchestrator.py` | Phase 1 block (~line 120) |
| Add a new scraping site | `site_prompts.py` | `SITE_CONFIGS` dict + add credentials |

---

## Data Flow: Application Submission

```
Frontend: user clicks "Apply" on a queue match
    │
    ├─ POST /api/applications {job_id, match_id}
    │
    ├─ ApplicationEngine.apply(job, document)
    │      ├─ DailyLimitGuard.check() — raises if at limit
    │      │
    │      ├─ [auto_apply mode]
    │      │      AutoApplier.apply(job, document)
    │      │          ├─ Playwright browser opens apply_url
    │      │          ├─ browser-use agent fills form fields
    │      │          ├─ WS broadcast: {"type": "confirm_apply", "job_id": ...}
    │      │          └─ WAIT for confirm_submit WS message from frontend
    │      │
    │      ├─ [assisted mode]
    │      │      AssistedApplier.apply(job, document)
    │      │          └─ Opens URL + notifies user to complete manually
    │      │
    │      └─ [manual mode]
    │             ManualApplier.apply(job, document)
    │                 └─ Records application without browser action
    │
    └─ INSERT INTO applications + application_events
```

---

## WebSocket Protocol

The `/ws` endpoint uses a persistent connection for live UI updates.

### Handler Registry Pattern

Client → Server messages are dispatched via a **handler registry** in `ConnectionManager` (`ws.py`):

```python
# Registration (in main.py lifespan):
manager.register_handler("login_done", handler_fn)
manager.register_handler("confirm_submit", handler_fn)
manager.register_handler("cancel_apply", handler_fn)

# Dispatch (in ws.py websocket_endpoint):
handler = manager._message_handlers.get(msg_type)
if handler:
    await handler(data, db)
```

**Why this pattern**: The previous approach patched `ws_module.router.routes` during
`lifespan`, but `app.include_router(ws.router)` copies routes at import time, so
patched handlers never ran. The registry avoids this by dispatching inside the already-
registered WebSocket endpoint.

### Where to modify

| Change | File | Location |
|---|---|---|
| Add a new WS message type | `main.py` | `lifespan()` — add `manager.register_handler(...)` |
| Change dispatch logic | `ws.py` | `websocket_endpoint()` message loop |
| Change connection management | `ws.py` | `ConnectionManager` class |

### Server → Client messages
| type | Payload | When |
|---|---|---|
| `pong` | `{}` | Response to ping |
| `job_scraped` | `{job: {...}}` | New job stored |
| `match_scored` | `{match: {...}}` | New match created |
| `cv_tailored` | `{document_id, job_id}` | CV tailoring complete |
| `confirm_apply` | `{job_id, title, company}` | Waiting for user confirm |
| `applied` | `{job_id, application_id}` | Application submitted |
| `batch_start` | `{batch_date}` | Morning batch starting |
| `batch_complete` | `{new_jobs, new_matches}` | Morning batch done |
| `login_required` | `{site}` | Browser needs manual login |
| `error` | `{message, code}` | Error notification |

### Client → Server messages
| type | Payload | Effect |
|---|---|---|
| `ping` | `{}` | Server responds with pong |
| `confirm_submit` | `{job_id}` | Unblocks AutoApplier submit |
| `cancel_apply` | `{job_id}` | Cancels pending application |
| `cancel` | `{job_id}` | General cancel |
| `login_done` | `{site}` | Confirms browser login → unblocks `session_manager.confirm_login()` |
---

## Singleton Architecture

All service instances are created once at startup in `main.py`'s `lifespan` function and stored on `app.state`. API routes access them via the `request.app.state` pattern or through FastAPI's dependency injection (`Annotated[..., Depends(...)]`).

```python
app.state.gemini             # GeminiClient
app.state.cv_pipeline        # CVPipeline
app.state.letter_pipeline    # LetterPipeline
app.state.scraping_orchestrator  # ScrapingOrchestrator
app.state.matcher            # JobMatcher
app.state.apply_engine       # ApplicationEngine
app.state.morning_scheduler  # MorningBatchScheduler
app.state.session_manager    # BrowserSessionManager
```

---

## Rate Limiting

### Gemini (15 RPM)
`GeminiClient` uses an `asyncio.Semaphore(1)` + 4-second sleep between calls to stay within the 15 requests/minute free tier limit.

### Adzuna (250 req/day)
`AdzunaClient` does not auto-throttle; the morning batch is designed to make at most one search call per configured keyword set. The daily limit is not enforced in code — it relies on sensible configuration.

### Daily apply limit
`DailyLimitGuard` counts `applications` rows for today and raises `DailyLimitReachedError` if `count ≥ daily_limit` (default: 10).

---

## Browser Session Management (`session_manager.py`)

### browser_use API (NOT raw Playwright)

`BrowserSessionManager` uses the `browser_use` package. **This is NOT Playwright's**
**native API** — it's a CDP-based wrapper with its own object model:

```
browser_use.Browser  =  alias for BrowserSession (Pydantic model, CDP wrapper)

Lifecycle:
  Browser(headless=False)          → config only, NO browser launch
  await browser.start()            → launches Chromium via CDP (idempotent)
  await browser.new_page(url)      → returns Page (CDP-based, NOT Playwright Page)
  await browser.stop()             → graceful shutdown + saves storage state
  await browser.kill()             → force shutdown, no state save

Page API:
  page.goto(url)                   → navigate
  page.get_url()                   → current URL (async, not a property)
  page.get_elements_by_css_selector(sel) → List[Element]
  page.evaluate(js_expr)           → run JS
  page.press(key)                  → keyboard input

Element API:
  element.fill(text)               → type into input
  element.click()                  → click
  element.hover()                  → hover
  element.focus()                  → focus

⚠ These DO NOT exist on browser_use objects:
  browser.close()                  → use browser.stop() or browser.kill()
  page.fill(selector, value)       → use get_elements_by_css_selector() then element.fill()
  page.click(selector)             → use get_elements_by_css_selector() then element.click()
  page.url                         → use await page.get_url()
  page.wait_for_timeout(ms)        → use await asyncio.sleep(seconds)
  page.keyboard.press(key)         → use await page.press(key)
```

### Storage State (Cookies / Session Persistence)

```
Storage state persistence via watchdog:

  Browser(storage_state="data/sessions/linkedin.json")
    → on start(): loads cookies/localStorage from file (if exists)
    → during session: auto-saves every 30 seconds
    → on stop(): dispatches SaveStorageStateEvent → watchdog saves to file

  kill() does NOT save state — only stop() triggers the save.
```

### Manual Login Flow

```
session_manager.get_or_create_session(site="linkedin")
    │
    ├─ Check for saved session file at data/sessions/linkedin.json
    │
    ├─ IF exists → Browser(storage_state=path) → start() → validate session
    │    └─ IF valid → return browser (reuse session)
    │    └─ IF invalid → stop() → fall through to manual login
    │
    └─ No valid session → _request_login(site)
           │
           ├─ Browser(headless=False, storage_state=path)
           ├─ await browser.start()          ← VISIBLE browser window opens
           ├─ page = await browser.new_page(login_url)
           │
           ├─ Try auto-fill credentials (if stored in DB):
           │    get_elements_by_css_selector → element.fill(email)
           │    get_elements_by_css_selector → element.fill(password)
           │    (continues even if auto-fill fails)
           │
           ├─ WS broadcast: {"type": "login_required", "site": "linkedin"}
           │    → Frontend shows LoginRequiredModal
           │
           ├─ WAIT on asyncio.Event (blocks until user action)
           │    ← User logs in manually in visible browser
           │    ← User clicks "Done" in frontend modal
           │
           ├─ Frontend sends WS: {"type": "login_done", "site": "linkedin"}
           │    → ws.py dispatches to registered handler
           │    → handler calls session_manager.confirm_login(site)
           │    → sets asyncio.Event → unblocks _request_login()
           │
           ├─ Validate: check URL changed from login page
           ├─ await browser.stop()            ← saves cookies via watchdog
           └─ return new browser with saved storage_state
```

### Where to modify

| Change | File | Location |
|---|---|---|
| Add a new site for login | `site_prompts.py` | `SITE_CONFIGS` dict (add login_url, selectors) |
| Change browser launch options | `session_manager.py` | `Browser()` constructor call |
| Change credential auto-fill | `session_manager.py` | `_request_login()` — element selectors |
| Change session storage path | `session_manager.py` | `_session_path()` |
| Change login validation logic | `session_manager.py` | `_validate_session()` |
| Handle new WS message from frontend | `main.py` | `lifespan()` — `manager.register_handler()` |

---

## Common Pitfalls

These are real bugs encountered during development. Check here before modifying:

### 1. JSON settings fields are dicts, not lists
The frontend sends `keywords` as `{"include": ["python", ...]}` — a dict.
`list(settings.keywords)` iterates dict **keys**, giving `["include"]` instead of
the actual keywords. Always use `_extract_json_list()` in `morning_batch.py`.

### 2. browser_use is NOT Playwright
`from browser_use import Browser` imports `BrowserSession`, not Playwright's `Browser`.
Calling Playwright methods (`page.fill()`, `browser.close()`) will silently fail or
raise `AttributeError`. See the API table above.

### 3. browser.start() is required
`Browser(headless=False)` only creates configuration — it does NOT launch a browser.
`await browser.start()` must be called before any page interaction. Without it,
no browser window appears.

### 4. browser.stop() vs browser.kill()
`stop()` is graceful — it triggers `SaveStorageStateEvent` which saves cookies.
`kill()` is forceful — no state is saved. Use `stop()` for normal cleanup,
`kill()` only in error handlers.

### 5. WebSocket handler registration timing
`app.include_router(ws.router)` copies routes at import time. Patching
`ws_module.router.routes` in `lifespan()` has no effect on the already-registered
endpoint. Use `manager.register_handler()` instead.
