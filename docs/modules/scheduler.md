# Module: Scheduler

## Purpose

The `scheduler` package provides an APScheduler-based morning batch job that automates daily job discovery and CV pre-generation for JobPilot. It exists to remove the manual effort of checking job boards every morning: at a configured time the scheduler wakes up, scrapes all enabled job sources, scores and filters the raw results against the user's search preferences, persists the best matches to the database, and immediately pre-generates tailored CVs so they are ready before the user opens the dashboard. Without this module the application would be entirely reactive (search on demand); with it, JobPilot behaves as an autonomous job-hunting agent that works overnight.

---

## Key Components

### `morning_batch.py`

Contains the `MorningBatchScheduler` class and two private helper functions. The class owns the full pipeline from "wake up" to "notify dashboard":

1. **APScheduler lifecycle** — wraps an `AsyncIOScheduler` instance; `start()` registers the job under the `"morning_batch"` job ID with a `CronTrigger`, and `stop()` shuts the scheduler down gracefully.
2. **Five-step pipeline** (`_run_batch_inner`) — scraping → matching → DB persistence → CV generation → WebSocket broadcast.
3. **Deduplication** — jobs are fingerprinted by `MD5(company|title|location)` so repeated runs do not duplicate rows. Existing `Job` rows are updated with richer data (longer description, missing `apply_url`) rather than re-inserted.
4. **Daily limit enforcement** — only the top N matches receive pre-generated CVs, where N = remaining application slots for today (queried via `DailyLimitGuard`).
5. **Concurrency control** — CV generation is parallelised with `asyncio.gather`, capped at 3 concurrent Gemini API calls via a `asyncio.Semaphore`.
6. **Graceful degradation** — APScheduler is imported inside a `try/except`; if the package is missing the scheduler is disabled but the rest of the application continues to run. The WebSocket broadcast is similarly wrapped.

### `__init__.py`

Empty package marker (`# scheduler package`). Exports nothing; consumers import directly from `morning_batch`.

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

### `class MorningBatchScheduler`

```python
class MorningBatchScheduler:
    def __init__(
        self,
        scraper: ScrapingOrchestrator,
        matcher: JobMatcher,
        cv_pipeline: CVPipeline,
        db_factory: Callable[[], AsyncSession],
    ) -> None
```

**Constructor parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `scraper` | `ScrapingOrchestrator` | Handles multi-source job scraping. |
| `matcher` | `JobMatcher` | Scores `JobDetails` objects against `JobFilters`. |
| `cv_pipeline` | `CVPipeline` | Generates tailored LaTeX/PDF CVs. |
| `db_factory` | `Callable[[], AsyncSession]` | Factory that returns a new async DB session per call. In production this is `AsyncSessionLocal` from `backend.database`. |

---

#### `MorningBatchScheduler.start(batch_time: str = "08:00") -> None`

Registers the morning batch as a cron job and starts the APScheduler. Safe to call multiple times — the job is registered with `replace_existing=True`.

- `batch_time` — wall-clock time in `HH:MM` format (24-hour). Defaults to `"08:00"`. The value used at runtime comes from `SearchSettings.batch_time` read by the API layer, not from this default.
- No return value.
- Logs a warning and returns early if APScheduler is not installed.

#### `MorningBatchScheduler.stop() -> None`

Shuts down the APScheduler without waiting for running jobs to finish (`wait=False`). No-op if the scheduler is not running.

---

#### `MorningBatchScheduler.run_batch() -> None` *(async)*

Public entry point for the full pipeline. Also callable manually (e.g. via `POST /api/queue/refresh`). Opens a DB session, delegates to `_run_batch_inner`, and ensures the session is closed even on failure.

- No parameters beyond `self`.
- Exceptions are caught and logged; the method never raises to its caller.

---

#### Internal methods (not part of the public API but documented for maintainers)

| Method | Signature | Description |
|--------|-----------|-------------|
| `_run_batch_task` | `() -> None` | Synchronous APScheduler entry point; wraps `run_batch()` in `asyncio.ensure_future`. |
| `_run_batch_inner` | `(db: AsyncSession) -> None` *(async)* | Executes all five pipeline steps. |
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
  ScrapingOrchestrator.run_morning_batch(keywords, filters, sources, location, countries)
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
Step 4 — PRE-GENERATE TAILORED CVs
  DailyLimitGuard(db, limit=daily_limit).remaining_today() → int remaining
  top_ids = match_ids[:remaining]
  For each (match_id, JobDetails) — up to 3 concurrently:
    CVPipeline.generate_tailored_cv(base_cv_path, job, output_dir)
    Output dir: <jobpilot_data_dir>/cvs/<match_id>/
    _store_tailored_doc() → TailoredDocument row (tex_path, pdf_path, diff_json)
  → TailoredDocument rows written to DB        [WebSocket: 65%→95% progress]

        │
        ▼
Step 5 — NOTIFY DASHBOARD
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
- `JobMatch` rows — link between a job and the batch run, carrying the match score.
- `TailoredDocument` rows — paths to generated `.tex`/`.pdf` files plus structured diff data.

---

## Configuration

All scheduling and filtering parameters are read from the `search_settings` database table (`SearchSettings` model in `backend/models/user.py`). None of these values require an application restart to take effect — they are loaded fresh at the start of every batch run.

| Setting | Column | Type | Default | Description |
|---------|--------|------|---------|-------------|
| Batch time | `batch_time` | `str` (HH:MM) | `"08:00"` | Wall-clock time the cron job fires. Passed to `MorningBatchScheduler.start()` by the API layer. |
| Daily limit | `daily_limit` | `int` | `10` | Maximum CV pre-generations (and applications) per calendar day. |
| Minimum match score | `min_match_score` | `float` | `30.0` | Jobs scoring below this threshold are discarded after ranking. |
| Keywords | `keywords` | JSON | `["python", "machine learning"]` | Include-list for scraping and scoring. Stored as `{"include": [...]}` or bare list. |
| Locations | `locations` | JSON | `null` | Target locations. First entry is also passed as the primary `location` string to the scraper. |
| Countries | `countries` | JSON | `null` | Country filter passed to the scraper. |
| Salary min | `salary_min` | `int` | `null` | Minimum salary filter applied during matching. |
| Remote only | `remote_only` | `bool` | `False` | When true, only remote positions pass the filter. |
| Excluded keywords | `excluded_keywords` | JSON | `null` | Keywords whose presence disqualifies a job. |
| Excluded companies | `excluded_companies` | JSON | `null` | Company names to skip entirely. |

**CV generation concurrency** is hardcoded to `asyncio.Semaphore(3)` (3 concurrent Gemini API calls) — this is not currently configurable via settings.

**APScheduler auto-start is disabled.** As of the current implementation (`backend/main.py` line 112–113), `scheduler.start()` is never called during application startup. The morning batch runs only when explicitly triggered via `POST /api/queue/refresh`, which calls `scheduler.run_batch()` directly.

---

## Known Limitations / TODOs

1. **Auto-start removed.** The comment at `backend/main.py:112` reads `# APScheduler auto-start removed — batch runs only on user action`. The `start()` / `stop()` lifecycle methods and `CronTrigger` machinery are fully implemented but never activated in the current deployment. The module is effectively used as an on-demand runner only.

2. **`batch_time` setting is unused at runtime.** `SearchSettings.batch_time` is persisted and surfaced in the settings API, but because auto-start is disabled the value has no effect. If auto-start is re-enabled, the API layer must read this column and pass it to `scheduler.start()`.

3. **Hardcoded CV concurrency.** The `asyncio.Semaphore(3)` cap on concurrent Gemini calls in Step 4 is not exposed as a configurable setting.

4. **Hardcoded fallback defaults in `_load_settings`.** When no `SearchSettings` row exists the method fabricates a row with `keywords=["python", "machine learning"]`, `daily_limit=10`, `batch_time="08:00"`, `min_match_score=30.0`. These defaults are not written to the DB, so they reappear on every call rather than being seeded once.

5. **Single-user assumption.** All DB queries use `.limit(1)` for both `UserProfile` and `SearchSettings`. The scheduler has no concept of multiple users; adding multi-tenancy would require a significant redesign.

6. **Only `doc_type="cv"` is pre-generated.** Cover letters (`doc_type="letter"`) are never pre-generated by the batch job; they must be generated on demand.

7. **First location used as primary scraping target.** When multiple locations are configured, `location = filters.locations[0]` sends only the first to the scraper as a plain string; remaining locations in the list are not iterated.

8. **`experience_min` / `experience_max` / `job_types` / `languages` not used.** These columns exist on `SearchSettings` and are stored, but `_run_batch_inner` never reads or passes them to `JobFilters` or the scraper.
