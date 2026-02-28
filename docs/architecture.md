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
    ├─ ScrapingOrchestrator.run_morning_batch()
    │      ├─ AdzunaClient.search() → List[RawJob]
    │      ├─ AdaptiveScraper.scrape(site) → List[RawJob]  (per configured site)
    │      └─ JobDeduplicator.deduplicate() → List[RawJob]
    │                → INSERT INTO jobs (new only)
    │
    ├─ JobMatcher.score_all(jobs, settings)
    │      → INSERT INTO job_matches (score ≥ min_score)
    │
    └─ CVPipeline.prepare_for_queue(matches, db)
           → INSERT INTO tailored_documents (pre-tailor top N)
```

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
| `error` | `{message, code}` | Error notification |

### Client → Server messages
| type | Payload | Effect |
|---|---|---|
| `ping` | `{}` | Server responds with pong |
| `confirm_submit` | `{job_id}` | Unblocks AutoApplier submit |
| `cancel_apply` | `{job_id}` | Cancels pending application |
| `login_done` | `{site}` | Confirms browser login complete |

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
