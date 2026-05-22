# Module: Scheduler

## Purpose

The `scheduler` package contains the on-demand batch pipeline that drives JobPilot's job-discovery and CV pre-generation workflow. Despite the package name, no APScheduler/cron machinery is wired up: runs are triggered explicitly by `POST /api/queue/refresh` (or any other caller of `BatchRunner.run_batch()`). The pipeline scrapes all enabled job sources, scores and filters results against the user's search preferences, persists the best matches to the database, runs a skill-gap fit assessment, and pre-generates tailored CVs so they are ready when the user opens the dashboard.

---

## Key Components

### `batch_runner.py`

Contains the `BatchRunner` class and two private helper functions. The class owns the full pipeline from "wake up" to "notify dashboard":

1. **Six-step pipeline** (`_run_batch_inner`) — load settings → scrape → rank → store → fit-assess → CV generation → WebSocket broadcast.
2. **Deduplication** — jobs are fingerprinted by `MD5(company|title|location)` so repeated runs do not duplicate rows. Existing `Job` rows are updated with richer data (longer description, missing `apply_url`) rather than re-inserted.
3. **Daily limit enforcement** — only the top N matches receive pre-generated CVs, where N = remaining application slots for today (queried via `DailyLimitGuard`).
4. **Concurrency control** — fit assessment and CV generation are parallelised with `asyncio.gather`, capped by a `asyncio.Semaphore` (default `CONCURRENCY_GEMINI = 3`).
5. **Graceful degradation** — the WebSocket broadcast is wrapped so a disconnected/missing client never crashes the pipeline.

### `__init__.py`

Empty package marker (`# scheduler package`). Exports nothing; consumers import directly from `backend.scheduler.batch_runner`.

---

## Public Interface

### Module-level helpers

#### `_extract_json_list(value: Any, key: str) -> list[str]`

Private utility. Normalises a JSON column that may be stored as a bare `list` or as a dict with a well-known key (`{"include": [...]}`, `{"items": [...]}`).

- `value` — raw value from the SQLAlchemy JSON column.
- `key` — dict key to extract when `value` is a dict.
- Returns an empty list when `value` is neither a list nor a dict.

#### `_resolve_cv_path(profile_row: Any, data_dir: Path) -> Path | None`

Private utility. Determines which `.tex` CV file to use for the batch run.

Resolution order:
1. `profile_row.base_cv_path` — used if set and the file exists on disk.
2. Auto-detect: scans `<data_dir>/templates/` for `*.tex` files; picks the alphabetically first one.
3. Returns `None` if no CV can be found; a warning is logged on fallback.

- `profile_row` — a `UserProfile` ORM row (or `None`).
- `data_dir` — root of the JobPilot data directory (from `settings.jobpilot_data_dir`).

---

### `class BatchRunner`

```python
class BatchRunner:
    def __init__(
        self,
        scraper: ScrapingOrchestrator,
        matcher: JobMatcher,
        cv_pipeline: CVPipeline,
        db_factory: Callable[[], AsyncSession],
        embedder: Embedder | None = None,
        fit_engine: FitEngine | None = None,
    ) -> None
```

**Constructor parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `scraper` | `ScrapingOrchestrator` | Handles multi-source job scraping. |
| `matcher` | `JobMatcher` | Scores `JobDetails` objects against `JobFilters`. |
| `cv_pipeline` | `CVPipeline` | Generates tailored LaTeX/PDF CVs. |
| `db_factory` | `Callable[[], AsyncSession]` | Factory that returns a new async DB session per call. In production this is `AsyncSessionLocal` from `backend.database`. |
| `embedder` | `Embedder \| None` | Optional Gemini-backed embedder; populated in production wiring (`backend.main.lifespan`). When present, enables the fit-assessment skill-gap path. |
| `fit_engine` | `FitEngine \| None` | Optional skill-gap fit engine; paired with `embedder`. When present, `_assess_one` runs cosine-similarity-based gap assessment for each ranked match. |

---

#### `BatchRunner.run_batch() -> None` *(async)*

Public entry point for the full pipeline. Opens a DB session via `db_factory`, delegates to `_run_batch_inner`, and ensures the session is closed even on failure.

- No parameters beyond `self`.
- Exceptions are caught and logged; the method never raises to its caller.
- Maintains `self.running` and `self.last_status` so reconnecting WebSocket clients can render the most recent state.

---

#### Internal methods (not part of the public API but documented for maintainers)

| Method | Signature | Description |
|--------|-----------|-------------|
| `_broadcast_and_track` | `(message: str, progress: float) -> None` *(async)* | Constructs a `Status(message=, progress=)` model, broadcasts via `manager.broadcast`, and updates `self.last_status`. |
| `_run_batch_inner` | `(db: AsyncSession) -> None` *(async)* | Executes all six pipeline steps. |
| `_assess_one` | `(db, match_id, jd) -> tuple[int, FitAssessment \| None]` *(async)* | Runs the fit-assessment for a single match; returns `(match_id, FitAssessment)` or `(match_id, None)` when fit-engine is disabled or job has no description. Designed to be `asyncio.gather`-ed under a semaphore. |
| `_load_settings` | `(db: AsyncSession) -> SearchSettings` *(async)* | Fetches the first `SearchSettings` row; returns safe defaults if none exists. |
| `_load_profile` | `(db: AsyncSession) -> UserProfile \| None` *(async)* | Fetches the first `UserProfile` row. |
| `_load_sources` | `(db: AsyncSession) -> list[JobSource]` *(async)* | Fetches all `JobSource` rows where `enabled = True`. |
| `_store_matches` | `(db, ranked) -> list[int]` *(async)* | Upserts `Job` rows and creates `JobMatch` rows; returns their IDs in ranked order. |
| `_store_tailored_doc` | `(db, match_id, tailored, doc_type) -> None` *(async)* | Persists a `TailoredDocument` row for a generated CV or letter. |
| `_raw_to_details` | `(raw: Any) -> JobDetails` *(static)* | Converts a `RawJob` (from the scraper) into a `JobDetails` schema object used by the matcher. |

---

## Data Flow

The pipeline executes sequentially inside `_run_batch_inner`. Each step's output feeds the next.

```
DB (SearchSettings, UserProfile, JobSource)
        │
        ▼
Step 1 — SCRAPE
  ScrapingOrchestrator.scrape_batch(keywords, filters, sources, location, countries)
  → list[RawJob]                               [WebSocket: 5% progress]

        │
        ▼
Step 2 — MATCH & RANK
  _raw_to_details() converts each RawJob → JobDetails
  JobMatcher.score(jd, filters) → float score
  Filter: keep only jobs where score >= filters.min_score
  Sort descending by score
  → list[tuple[JobDetails, float]]             [WebSocket: 35% progress]

        │
        ▼
Step 3 — STORE MATCHES
  For each (JobDetails, score):
    Compute MD5 dedup_hash = MD5("company|title|location")
    Upsert Job row (insert new / update description & apply_url if richer)
    Upsert JobMatch row with batch_date=today, status="new", score
      (if match already exists today, update score only if improved)
  DB.commit()
  → list[int] match_ids (in ranked order)      [WebSocket: 55% progress]

        │
        ▼
Step 4 — FIT ASSESS  (only when embedder + fit_engine are wired)
  Embedder.embed_cv_profile(cv_profile) — one batched call
  For each (match_id, JobDetails) — up to CONCURRENCY_GEMINI concurrently:
    _assess_one() → FitAssessment | None
  Persist ats_score / gap_severity / fit_assessment_json on JobMatch rows
  Broadcast per-job assessment via WebSocket (job_progress payload)
                                                [WebSocket: 60% progress]

        │
        ▼
Step 5 — PRE-GENERATE TAILORED CVs
  DailyLimitGuard(db, limit=daily_limit).remaining_today() → int remaining
  top_ids = match_ids[:remaining]
  For each (match_id, JobDetails) — up to CONCURRENCY_GEMINI concurrently:
    CVPipeline.generate_tailored_cv(base_cv_path, job, output_dir,
                                    fit_assessment=fit_assessment_or_none)
    Output dir: <jobpilot_data_dir>/cvs/<match_id>/
    _store_tailored_doc() → TailoredDocument row (tex_path, pdf_path, diff_json)
  → TailoredDocument rows written to DB        [WebSocket: 65%→95% progress]

        │
        ▼
Step 6 — NOTIFY DASHBOARD
  broadcast_status("N applications ready for review", progress=1.0)
  [WebSocket: 100% progress]
```

**Reads from DB:**
- `SearchSettings` (single row): keywords, locations, salary_min, remote_only, excluded_keywords, excluded_companies, min_match_score, daily_limit, batch_time, countries.
- `UserProfile` (single row): `base_cv_path`.
- `JobSource` (all enabled rows): passed to scraper to select which job boards to query.
- `Application` (count): used by `DailyLimitGuard` to calculate remaining slots.
- `Job` (by dedup_hash): checked before inserting to prevent duplicates.
- `JobMatch` (by job_id + batch_date): checked before inserting to prevent same-day duplicates.

**Writes to DB:**
- `Job` rows — new listings discovered by the scraper.
- `JobMatch` rows — link between a job and the batch run, carrying the match score, plus `ats_score` / `gap_severity` / `fit_assessment_json` when the fit-engine path runs.
- `TailoredDocument` rows — paths to generated `.tex`/`.pdf` files plus structured diff data.

---

## Configuration

All filtering parameters are read from the `search_settings` database table (`SearchSettings` model in `backend/models/user.py`). None of these values require an application restart to take effect — they are loaded fresh at the start of every batch run.

| Setting | Column | Type | Default | Description |
|---------|--------|------|---------|-------------|
| Daily limit | `daily_limit` | `int` | `10` | Maximum CV pre-generations (and applications) per calendar day. |
| Minimum match score | `min_match_score` | `float` | `30.0` | Jobs scoring below this threshold are discarded after ranking. |
| Keywords | `keywords` | JSON | `["python", "machine learning"]` | Include-list for scraping and scoring. Stored as `{"include": [...]}` or bare list. |
| Locations | `locations` | JSON | `null` | Target locations. First entry is also passed as the primary `location` string to the scraper. |
| Countries | `countries` | JSON | `null` | Country filter passed to the scraper. |
| Salary min | `salary_min` | `int` | `null` | Minimum salary filter applied during matching. |
| Remote only | `remote_only` | `bool` | `False` | When true, only remote positions pass the filter. |
| Excluded keywords | `excluded_keywords` | JSON | `null` | Keywords whose presence disqualifies a job. |
| Excluded companies | `excluded_companies` | JSON | `null` | Company names to skip entirely. |

**Concurrency** for both fit assessment and CV generation is governed by `CONCURRENCY_GEMINI` in `backend/defaults.py` (default `3`) — bounded by an `asyncio.Semaphore` to respect the Gemini rate limit.

**No auto-start.** The batch runs only when explicitly triggered (the common path is `POST /api/queue/refresh`, which schedules `runner.run_batch()` on the event loop in the background). The `SearchSettings.batch_time` column is persisted and surfaced in the settings API but is currently unused at runtime.

---

## Known Limitations / TODOs

1. **No auto-scheduler.** APScheduler scaffolding was removed in the honesty pass. `SearchSettings.batch_time` is persisted but unused — re-enabling a cron would require wiring an `AsyncIOScheduler` (or equivalent) inside `backend.main.lifespan` and reading the column. Track this with the Gmail-integration spec which proposes a unified background-task layer.
2. **Hardcoded fallback defaults in `_load_settings`.** When no `SearchSettings` row exists the method fabricates a row with `keywords=["python", "machine learning"]`, `daily_limit=10`, `min_match_score=30.0`. These defaults are not written to the DB, so they reappear on every call rather than being seeded once.
3. **Single-user assumption.** All DB queries use `.limit(1)` for both `UserProfile` and `SearchSettings`. The scheduler has no concept of multiple users; adding multi-tenancy would require a significant redesign.
4. **Only `doc_type="cv"` is pre-generated.** Cover letters (`doc_type="letter"`) are never pre-generated by the batch job; they must be generated on demand.
5. **First location used as primary scraping target.** When multiple locations are configured, `location = filters.locations[0]` sends only the first to the scraper as a plain string; remaining locations in the list are not iterated.
6. **`experience_min` / `experience_max` / `job_types` / `languages` not used.** These columns exist on `SearchSettings` and are stored, but `_run_batch_inner` never reads or passes them to `JobFilters` or the scraper.
