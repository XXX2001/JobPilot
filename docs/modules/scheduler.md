# Module: Scheduler

## Purpose

The `scheduler` package contains the on-demand batch pipeline that drives JobPilot's job-discovery and CV pre-generation workflow. The batch itself is not on a timer: runs are triggered explicitly by `POST /api/queue/refresh` (or any other caller of `BatchRunner.run_batch()`). The timed work — the discovery monitor and the Gmail poll — is scheduled in `backend/main.py` and described under [Scheduled jobs](#scheduled-jobs). The pipeline scrapes all enabled job sources, scores and filters results against the user's search preferences, persists the best matches to the database, runs a skill-gap fit assessment, and pre-generates tailored CVs so they are ready when the user opens the dashboard.

---

## Key Components

### `batch_runner.py`

Contains the `BatchRunner` class and two private helper functions. The class owns the full pipeline from "wake up" to "notify dashboard":

1. **Six-step pipeline** (`_run_batch_inner`) — load settings → scrape → rank → store → fit-assess → CV generation → WebSocket broadcast.
2. **Deduplication** — jobs are fingerprinted by `dedup_key` (MD5 of normalised `company|title|city`, shared with the monitor) so repeated runs do not duplicate rows. Existing `Job` rows are updated with richer data (longer description, missing `apply_url`) rather than re-inserted.
3. **Daily limit enforcement** — only the top N matches receive pre-generated CVs, where N = remaining application slots for today (queried via `DailyLimitGuard`).
4. **Concurrency control** — fit assessment and CV generation are parallelised with `asyncio.gather`, capped by a `asyncio.Semaphore` (default `CONCURRENCY_LLM = 3`).
5. **Graceful degradation** — the WebSocket broadcast is wrapped so a disconnected/missing client never crashes the pipeline.

### `__init__.py`

Empty package marker (`# scheduler package`). Exports nothing; consumers import directly from `backend.scheduler.batch_runner`.

---

## Scheduled jobs

One `AsyncIOScheduler` is started in `backend.main.lifespan` and kept on `app.state.scheduler`. It lives in the app process: no second process, no broker.

| Job id | Function | Interval | Condition |
|---|---|---|---|
| `gmail_poll` | `_run_gmail_poll` | `GMAIL_POLL_INTERVAL_MINUTES` (5 min) | always |
| `monitor_tick` | `backend.monitor.poller.monitor_tick` | `MONITOR_TICK_SECONDS` (30 s) | `MONITOR_ENABLED=true` |

`monitor_tick` is added with `max_instances=1, coalesce=True`: a tick that outlasts the interval is not overlapped, and missed runs collapse into one.

### The discovery monitor

A tick polls every enabled adapter source whose `next_poll_at` is due, at most `MONITOR_MAX_CONCURRENCY` (8) at once, each in its own DB session. Poll state is on the `job_sources` row, not in the scheduler:

| Column | Meaning |
|---|---|
| `poll_interval_s` | Seconds between polls; null uses the adapter default (Greenhouse, Ashby, SmartRecruiters 60 s; LinkedIn guest 300 s; Indeed 420 s; otherwise 300 s) |
| `next_poll_at` | When the source is due (`next_due`). After a success: now + interval × U(0.8, 1.2). After a failure: now + interval × 2^`fail_count`, capped at 6 h |
| `last_scraped_at`, `first_polled_at` | Last and first successful poll. A null `last_scraped_at` marks the seeding poll, which notifies nothing. Indeed and LinkedIn guest also skip notifying postings older than `first_polled_at` |
| `etag` | Validator for the conditional GET (Greenhouse, Ashby, SmartRecruiters) |
| `fail_count`, `last_error` | Consecutive failures and the last error (500 characters). An `auth` error disables the source instead |
| `window_hours` | Indeed's window, halved when a poll overflows one page |

The effective poll period is the source interval rounded up to the next tick. DB writes of concurrent polls are serialised by one in-process lock (`_write_lock`); the HTTP fetches are not. The tick awaits all its polls, so a slow one (LinkedIn page reading in the JobPilot Chrome can take minutes) makes APScheduler skip the ticks due meanwhile.

Per poll: adapter fetch → `upsert_discovered` → `match_new` → (LinkedIn guest only: read the matched cards' pages, rescore) → notify (`backend/monitor/notify.py`: `new_match` WebSocket frame, plus a background POST to `NTFY_URL`). A failed notification never undoes a committed poll. Sources, source configuration, the status API (`GET /api/monitor/status`, pause, resume, add) and the watchlist import (`scripts/import_watchlist.py`) are described in [Job scraping & sources](../scraping.md#the-discovery-monitor).

`rescore_stale` (same module) recomputes stored `new` matches at startup and after a CV, profile CV path, search-settings or skill-file save, then matches unmatched jobs of the last 30 days without notifying. Full design: [monitor.md](../monitor.md).

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
        matcher: JobMatcher | None,
        cv_pipeline: CVPipeline,
        db_factory: Callable[[], AsyncSession],
        fit_engine: FitEngine | None = None,
    ) -> None
```

**Constructor parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `scraper` | `ScrapingOrchestrator` | Handles multi-source job scraping. |
| `matcher` | `JobMatcher \| None` | Scores `JobDetails` against `JobFilters`. Production passes `None`: each batch loads the owner's matcher with `load_matcher(db)` (CV or `my_skills.txt`, keywords, vocabulary). |
| `cv_pipeline` | `CVPipeline` | Generates tailored LaTeX/PDF CVs. |
| `db_factory` | `Callable[[], AsyncSession]` | Factory that returns a new async DB session per call. In production this is `AsyncSessionLocal` from `backend.database`. |
| `fit_engine` | `FitEngine \| None` | Skill-gap fit engine (defaults to `FitEngine()`). `_assess_one` runs the gazetteer-coverage gap assessment for each stored match; no model call. |

---

#### `BatchRunner.run_batch(dry_run: bool = False) -> list[dict] | None` *(async)*

Public entry point for the full pipeline. Opens a DB session via `db_factory`, delegates to `_run_batch_inner`, and ensures the session is closed even on failure.

- `dry_run=True` stops after scrape and rank and returns a preview list (`title`, `company`, `score`, `location`) with no DB writes and no LLM calls. A second call while one runs returns `None` at once.
- Exceptions are caught and logged; the method never raises to its caller.
- Maintains `self.running` and `self.last_status` so reconnecting WebSocket clients can render the most recent state.

---

#### Internal methods (not part of the public API but documented for maintainers)

| Method | Signature | Description |
|--------|-----------|-------------|
| `_broadcast_and_track` | `(message: str, progress: float) -> None` *(async)* | Constructs a `Status(message=, progress=)` model, broadcasts via `manager.broadcast`, and updates `self.last_status`. |
| `_run_batch_inner` | `(db: AsyncSession, dry_run: bool = False) -> list[dict] \| None` *(async)* | Executes the pipeline steps (only scrape and rank on a dry run). |
| `_assess_one` | `(jd, cv_profile, gaz, sensitivity) -> FitAssessment \| None` | Fit assessment of a single match from gazetteer coverage (pure CPU, no LLM); `None` when the posting names fewer than `MIN_JOB_SKILLS_FOR_FIT_ENGINE` skills. |
| `_load_settings` | `(db: AsyncSession) -> SearchSettings` *(async)* | Fetches the first `SearchSettings` row; returns safe defaults if none exists. |
| `_load_profile` | `(db: AsyncSession) -> UserProfile \| None` *(async)* | Fetches the first `UserProfile` row. |
| `_load_sources` | `(db: AsyncSession) -> list[JobSource]` *(async)* | Fetches all `JobSource` rows where `enabled = True`. |
| `_store_matches` | `(db, ranked, filters, matcher=None) -> list[StoredMatch]` *(async)* | Upserts `Job` rows and creates `JobMatch` rows (one per job, ever: a later batch reuses it and only raises its score); returns `StoredMatch(match_id, job)` records in ranked order, omitting already applied/skipped jobs and collapsing a job scraped twice. `job.id` is the persisted `Job.id`. Downstream steps use the record's own job — never the position in `ranked`. |
| `_store_tailored_doc` | `(db, match_id, tailored, doc_type) -> None` *(async)* | Persists a `TailoredDocument` row for a generated CV or letter. |
| `raw_to_details` | `(raw: Any) -> JobDetails` | Imported from `backend.monitor.store`: converts a `RawJob` (or a stored `Job`) into the `JobDetails` the matcher reads. |

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
  matcher = injected or load_matcher(db)
  raw_to_details() converts each RawJob → JobDetails
  JobMatcher.score(jd, filters) → float score (gazetteer scorer, see modules/matching.md)
  Filter: keep only jobs where score >= filters.min_score
  Sort descending by score
  → list[tuple[JobDetails, float]]             [WebSocket: 35% progress]

        │
        ▼
Step 3 — STORE MATCHES
  For each (JobDetails, score):
    dedup_hash = dedup_key(company, title, location)   (normalised, city only)
    Upsert Job row (insert new / update description & apply_url if richer)
    Skip the job if any match is applied / skipped / applying / rejected
    Reuse the job's newest match (raise its score if improved), else create one
      with batch_date=today, status="new", score_basis, provisional, location_unverified
  DB.commit()
  → list[StoredMatch] (match_id + its own job, ranked order)      [WebSocket: 55% progress]

        │
        ▼
Step 4 — FIT ASSESS  (only when a base CV exists; no LLM, no network)
  CVParser.build_profile(cv_tex, gazetteer) — the CV's gazetteer skills
  For each (match_id, JobDetails):
    _assess_one() → FitAssessment | None   (None: fewer than 2 skills in the posting)
  Persist ats_score / gap_severity / fit_assessment_json on JobMatch rows
  Broadcast per-job assessment via WebSocket (job_progress payload)
                                                [WebSocket: 60% progress]

        │
        ▼
Step 5 — PRE-GENERATE TAILORED CVs
  DailyLimitGuard.from_settings(db).remaining_today() → int remaining
  described = stored matches with a description ≥ 80 chars (cards take no CV slot)
  selected = described[:remaining]
  For each (match_id, JobDetails) — up to CONCURRENCY_LLM concurrently:
    CVPipeline.generate_tailored_cv(base_cv_path, job, output_dir,
                                    fit_assessment=fit_assessment_or_none)
    Output dir: <jobpilot_data_dir>/cvs/<match_id>/
    _store_tailored_doc() → TailoredDocument row (tex_path, pdf_path, diff_json)
  → TailoredDocument rows written to DB        [WebSocket: 65%→95% progress]

        │
        ▼
Step 6 — NOTIFY DASHBOARD
  broadcast_status("N ready for review[, M failed]", progress=1.0)  # persisted docs, not selected matches
  [WebSocket: 100% progress]
```

**Reads from DB:**
- `SearchSettings` (single row): keywords, locations, salary_min, remote_only, excluded_keywords, excluded_companies, min_match_score, daily_limit, batch_time, countries.
- `UserProfile` (single row): `base_cv_path`.
- `JobSource` (all enabled rows): passed to scraper to select which job boards to query.
- `Application` (count): used by `DailyLimitGuard` to calculate remaining slots.
- `Job` (by dedup_hash): checked before inserting to prevent duplicates.
- `JobMatch` (by job_id): an actioned match skips the job; otherwise the newest match is reused (one match per job, spec D3).

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

**Concurrency** for both fit assessment and CV generation is governed by `CONCURRENCY_LLM` in `backend/defaults.py` (default `3`) — bounded by an `asyncio.Semaphore` to respect the LLM provider rate limit.

**No auto-start for the batch.** It runs only when explicitly triggered (the common path is `POST /api/queue/refresh`, which schedules `runner.run_batch()` on the event loop in the background). The `SearchSettings.batch_time` column is persisted and surfaced in the settings API but is currently unused at runtime. Continuous discovery is the monitor's job (`MONITOR_ENABLED`, `MONITOR_TICK_SECONDS`, `MONITOR_MAX_CONCURRENCY`, `NTFY_URL` in `.env`).

---

## Known Limitations / TODOs

1. **The batch is not scheduled.** `SearchSettings.batch_time` is persisted but unused at runtime. The discovery monitor covers timed discovery; it does not pre-generate CVs.
2. **Fallback defaults in `_load_settings`.** When no `SearchSettings` row exists the method builds an unsaved row with `keywords=[]`, `daily_limit=DAILY_LIMIT` (10), `min_match_score=MIN_MATCH_SCORE` (30.0) from `backend/defaults.py`. They are not written to the DB, so they are rebuilt on every call.
3. **Single-user assumption.** All DB queries use `.limit(1)` for both `UserProfile` and `SearchSettings`. The scheduler has no concept of multiple users; adding multi-tenancy would require a significant redesign.
4. **Only `doc_type="cv"` is pre-generated.** Cover letters (`doc_type="letter"`) are never pre-generated by the batch job; they must be generated on demand.
5. **First location used as primary scraping target.** When multiple locations are configured, `location = filters.locations[0]` sends only the first to the scraper as a plain string; remaining locations in the list are not iterated.
6. **`experience_min` / `experience_max` / `job_types` / `languages` not used.** These columns exist on `SearchSettings` and are stored, but `_run_batch_inner` never reads or passes them to `JobFilters` or the scraper.
7. **The fit assessment ignores the skill files.** Step 4 builds `Gazetteer.load(filters.keywords)` on the bundled vocabulary and parses the CV, so `$DATA/skills.txt` and `$DATA/my_skills.txt` change the match score but not the gap analysis.
