# Module: Models & Database

## Purpose

This module defines the full persistence layer for JobPilot. It contains SQLAlchemy async ORM models (one file per domain entity), Pydantic schemas used as in-process data transfer objects between the scraping, matching, and LLM layers, and Alembic migrations that manage the physical SQLite schema. Every other backend subsystem — the API, scheduler, scraper, applier, and LLM clients — depends on the types defined here.

---

## Key Components

### `base.py`

Defines `Base`, the single `DeclarativeBase` subclass from which all SQLAlchemy ORM models inherit. Holds no columns or logic of its own; its `metadata` attribute is what Alembic and `init_db()` use to create/migrate tables.

### `user.py`

Defines three models:

- **`UserProfile`** — singleton row (`id` defaults to `1`) storing the candidate's identity and paths to their base CV and cover-letter LaTeX files.
- **`SearchSettings`** — singleton row (`id` defaults to `1`) storing all job-search preference knobs: keywords, salary floor, experience range, remote flag, daily apply limit, morning batch time, and minimum match score threshold.
- **`SiteCredential`** — one row per job board, holding Fernet-encrypted login credentials for automated sign-in.

### `job.py`

Defines three models:

- **`JobSource`** — configuration record for a scraped site (name, type, base URL, JSON config, optional per-source LLM prompt template, enabled flag, last-scraped timestamp). Seeded from `SITE_CONFIGS` on first startup.
- **`Job`** — a raw scraped job posting. Contains all content fields and a `dedup_hash` unique constraint to prevent duplicate ingestion.
- **`JobMatch`** — the output of the matcher: links a `job_id` to a numeric score, optional `keyword_hits` breakdown, a workflow `status` string, and the `batch_date` the match was produced on.

### `document.py`

Defines **`TailoredDocument`** — one row per generated document (CV or cover letter) for a `JobMatch`. Stores the `.tex` and `.pdf` filesystem paths, the diff JSON showing what changed relative to the base document, and the raw LLM prompt and response for auditability.

### `application.py`

Defines two models:

- **`Application`** — tracks a single application attempt for a `JobMatch`: the submission method (e.g. `"auto"`, `"assisted"`), lifecycle `status`, timestamp when submitted, freeform notes, and an error log for failures.
- **`ApplicationEvent`** — an audit log of discrete state-change events tied to an `Application` (e.g. `"submitted"`, `"captcha_detected"`, `"error"`).

### `session.py`

Defines **`BrowserSession`** — one row per job-board site, storing the Playwright storage-state file path and the expiry/last-used timestamps so the session manager can reuse authenticated browser contexts across runs.

### `schemas.py`

Defines two Pydantic models used as in-memory DTOs (not persisted directly):

- **`RawJob`** — the normalised shape of a job emitted by any scraper before database insertion. Validated with max-length constraints.
- **`JobDetails`** — the enriched shape passed into the matcher, LLM CV editor, LaTeX pipeline, and morning batch. Includes a `posted_date` alias field used by recency scoring.

### `alembic/`

Contains the Alembic migration environment and version scripts:

- **`env.py`** — wires `Base.metadata` to the async Alembic runner using `async_engine_from_config` and `NullPool`.
- **`versions/071b973b48b2_initial_schema.py`** — creates all nine original tables in a single migration (2026-02-28).
- **`versions/df6eea4756c3_add_site_credentials_table.py`** — adds the `site_credentials` table (2026-03-03), chained from the initial migration.

---

## Public Interface

### SQLAlchemy Models

#### `UserProfile` — table `user_profile`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, default=1 |
| `full_name` | `str` | `String` | NOT NULL |
| `email` | `str` | `String` | NOT NULL |
| `phone` | `Optional[str]` | `String` | nullable |
| `location` | `Optional[str]` | `String` | nullable |
| `base_cv_path` | `Optional[str]` | `String` | nullable |
| `base_letter_path` | `Optional[str]` | `String` | nullable |
| `additional_info` | `Optional[dict]` | `JSON` | nullable |
| `created_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |
| `updated_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `SearchSettings` — table `search_settings`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, default=1 |
| `keywords` | `dict` | `JSON` | NOT NULL |
| `excluded_keywords` | `Optional[dict]` | `JSON` | nullable |
| `locations` | `Optional[dict]` | `JSON` | nullable |
| `salary_min` | `Optional[int]` | `Integer` | nullable |
| `experience_min` | `Optional[int]` | `Integer` | nullable |
| `experience_max` | `Optional[int]` | `Integer` | nullable |
| `remote_only` | `bool` | `Boolean` | NOT NULL, default=False |
| `job_types` | `Optional[dict]` | `JSON` | nullable |
| `languages` | `Optional[dict]` | `JSON` | nullable |
| `excluded_companies` | `Optional[dict]` | `JSON` | nullable |
| `daily_limit` | `int` | `Integer` | NOT NULL, default=10 |
| `batch_time` | `str` | `String` | NOT NULL, default="08:00" |
| `min_match_score` | `float` | `Float` | NOT NULL, default=30.0 |
| `countries` | `Optional[dict]` | `JSON` | nullable |

Note: `countries` was added to the ORM model after the initial migration and is not covered by any migration script; it relies on `init_db()` running `create_all` to add it when the table does not yet exist.

#### `SiteCredential` — table `site_credentials`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `site_name` | `str` | `String` | NOT NULL, UNIQUE |
| `encrypted_email` | `Optional[str]` | `String` | nullable |
| `encrypted_password` | `Optional[str]` | `String` | nullable |
| `created_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |
| `updated_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `JobSource` — table `job_sources`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `name` | `str` | `String` | NOT NULL |
| `type` | `str` | `String` | NOT NULL |
| `url` | `Optional[str]` | `String` | nullable |
| `config` | `Optional[dict]` | `JSON` | nullable |
| `prompt_template` | `Optional[str]` | `Text` | nullable |
| `enabled` | `bool` | `Boolean` | NOT NULL, default=True |
| `last_scraped_at` | `Optional[datetime]` | `DateTime` | nullable |
| `created_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `Job` — table `jobs`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `source_id` | `Optional[int]` | `Integer` | nullable (logical FK to `job_sources.id`, no DB-level constraint) |
| `external_id` | `Optional[str]` | `String` | nullable |
| `title` | `str` | `String` | NOT NULL |
| `company` | `str` | `String` | NOT NULL |
| `location` | `Optional[str]` | `String` | nullable |
| `country` | `Optional[str]` | `String` | nullable |
| `salary_text` | `Optional[str]` | `String` | nullable |
| `salary_min` | `Optional[int]` | `Integer` | nullable |
| `salary_max` | `Optional[int]` | `Integer` | nullable |
| `description` | `Optional[str]` | `Text` | nullable |
| `requirements` | `Optional[dict]` | `JSON` | nullable |
| `benefits` | `Optional[dict]` | `JSON` | nullable |
| `url` | `str` | `String` | NOT NULL |
| `apply_url` | `Optional[str]` | `String` | nullable |
| `apply_method` | `Optional[str]` | `String` | nullable |
| `posted_at` | `Optional[datetime]` | `DateTime` | nullable |
| `scraped_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |
| `dedup_hash` | `Optional[str]` | `String` | nullable, UNIQUE |
| `raw_data` | `Optional[dict]` | `JSON` | nullable |

#### `JobMatch` — table `job_matches`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `job_id` | `int` | `Integer` | NOT NULL (logical FK to `jobs.id`, no DB-level constraint) |
| `score` | `float` | `Float` | NOT NULL |
| `keyword_hits` | `Optional[dict]` | `JSON` | nullable |
| `status` | `str` | `String` | NOT NULL, default="new" |
| `batch_date` | `Optional[date]` | `Date` | nullable |
| `matched_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `TailoredDocument` — table `tailored_documents`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `job_match_id` | `Optional[int]` | `Integer` | nullable (logical FK to `job_matches.id`, no DB-level constraint) |
| `doc_type` | `str` | `String` | NOT NULL (e.g. `"cv"`, `"letter"`) |
| `tex_path` | `Optional[str]` | `String` | nullable |
| `pdf_path` | `Optional[str]` | `String` | nullable |
| `diff_json` | `Optional[dict]` | `JSON` | nullable |
| `llm_prompt` | `Optional[str]` | `Text` | nullable |
| `llm_response` | `Optional[str]` | `Text` | nullable |
| `created_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `Application` — table `applications`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `job_match_id` | `Optional[int]` | `Integer` | nullable (logical FK to `job_matches.id`, no DB-level constraint) |
| `method` | `str` | `String` | NOT NULL |
| `status` | `str` | `String` | NOT NULL, default="pending" |
| `applied_at` | `Optional[datetime]` | `DateTime` | nullable |
| `notes` | `Optional[str]` | `String` | nullable |
| `error_log` | `Optional[str]` | `Text` | nullable |
| `created_at` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `ApplicationEvent` — table `application_events`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `application_id` | `int` | `Integer` | NOT NULL (logical FK to `applications.id`, no DB-level constraint) |
| `event_type` | `str` | `String` | NOT NULL |
| `details` | `Optional[str]` | `String` | nullable |
| `event_date` | `datetime` | `DateTime` | NOT NULL, default=utcnow |

#### `BrowserSession` — table `browser_sessions`

| Column | Python Type | SQLAlchemy Type | Constraints |
|---|---|---|---|
| `id` | `int` | `Integer` | PK, autoincrement |
| `site_name` | `str` | `String` | NOT NULL, UNIQUE |
| `storage_state_path` | `Optional[str]` | `String` | nullable |
| `last_used_at` | `Optional[datetime]` | `DateTime` | nullable |
| `expires_at` | `Optional[datetime]` | `DateTime` | nullable |

---

### Pydantic Schemas (`schemas.py`)

#### `RawJob`

In-memory DTO produced by every scraper and consumed by the deduplicator and DB-ingestion logic in `scraping/orchestrator.py`.

| Field | Type | Default | Constraint |
|---|---|---|---|
| `external_id` | `str` | `""` | — |
| `title` | `str` | required | max_length=300 |
| `company` | `str` | required | max_length=200 |
| `location` | `str` | `""` | max_length=200 |
| `salary_text` | `str` | `""` | max_length=100 |
| `salary_min` | `Optional[int]` | `None` | — |
| `salary_max` | `Optional[int]` | `None` | — |
| `description` | `str` | `""` | max_length=5000 |
| `requirements` | `list[str]` | `[]` | — |
| `benefits` | `list[str]` | `[]` | — |
| `url` | `str` | `""` | max_length=2048 |
| `apply_url` | `str` | `""` | max_length=2048 |
| `apply_method` | `str` | `""` | — |
| `posted_at` | `Optional[datetime]` | `None` | — |
| `source_name` | `str` | `""` | — |
| `country` | `str` | `""` | — |
| `raw_data` | `Optional[dict]` | `None` | — |

#### `JobDetails`

In-memory DTO used by the matcher (`matching/matcher.py`), LLM CV editor, LaTeX pipeline, and morning batch scheduler. Derived from a `Job` + `JobMatch` DB read.

| Field | Type | Default |
|---|---|---|
| `id` | `Optional[int]` | `None` |
| `title` | `str` | required |
| `company` | `str` | required |
| `location` | `str` | `""` |
| `description` | `str` | `""` |
| `salary_min` | `Optional[int]` | `None` |
| `salary_max` | `Optional[int]` | `None` |
| `posted_at` | `Optional[datetime]` | `None` |
| `posted_date` | `Optional[datetime]` | `None` (alias used by recency scoring) |
| `url` | `str` | `""` |
| `score` | `Optional[float]` | `None` |
| `apply_url` | `str` | `""` |
| `apply_method` | `str` | `""` |
| `country` | `str` | `""` |

---

## Database Schema

### Entity Relationship Overview

```
job_sources (id)
    |
    | [logical, no FK constraint]
    |
jobs (id, source_id → job_sources.id)
    |
    | [logical, no FK constraint]
    |
job_matches (id, job_id → jobs.id)
    |         |
    |         | [logical]           [logical]
    |         |                     |
    |    tailored_documents     applications (id, job_match_id)
    |    (job_match_id)              |
    |                               | [logical]
    |                               |
    |                          application_events
    |                          (application_id)
    |
user_profile        (singleton)
search_settings     (singleton)
site_credentials    (one per job board)
browser_sessions    (one per job board)
```

### Cascade Behavior

No database-level `ON DELETE CASCADE` constraints exist anywhere in the schema. All relationships are enforced solely by application logic. Deleting a parent row (e.g. a `Job`) leaves orphaned child rows in `job_matches`, `tailored_documents`, and `applications`.

### Foreign Key Summary

| Child Table | Column | Logical Parent | DB FK? |
|---|---|---|---|
| `jobs` | `source_id` | `job_sources.id` | No |
| `job_matches` | `job_id` | `jobs.id` | No |
| `tailored_documents` | `job_match_id` | `job_matches.id` | No |
| `applications` | `job_match_id` | `job_matches.id` | No |
| `application_events` | `application_id` | `applications.id` | No |

### Unique Constraints

| Table | Column(s) | Purpose |
|---|---|---|
| `jobs` | `dedup_hash` | Prevent duplicate job ingestion across scrape runs |
| `browser_sessions` | `site_name` | One session record per site |
| `site_credentials` | `site_name` | One credential record per site |

---

## Data Flow

### Scraping pipeline

1. `scraping/orchestrator.py` queries `job_sources` (via `JobSource`) to get enabled sources and their configs/prompts.
2. Scrapers (`adaptive_scraper.py`, `scrapling_fetcher.py`, `adzuna_client.py`) produce `RawJob` Pydantic objects.
3. `scraping/deduplicator.py` consumes `RawJob` objects, computes a hash, and checks it against `jobs.dedup_hash`.
4. New jobs are written as `Job` rows by the orchestrator.

### Matching pipeline (morning batch)

5. `scheduler/morning_batch.py` reads `SearchSettings` and `UserProfile` (singletons), then queries `Job` rows from the current batch.
6. `matching/matcher.py` converts `Job` rows into `JobDetails` Pydantic objects and scores them.
7. Matches above `min_match_score` are written as `JobMatch` rows.

### Document generation pipeline

8. `morning_batch.py` passes top `JobMatch` rows to the LaTeX pipeline (`latex/pipeline.py`) and LLM CV editor, both of which consume `JobDetails`.
9. Generated `.tex`/`.pdf` paths and diffs are stored as `TailoredDocument` rows, linked by `job_match_id`.

### Application pipeline

10. `applier/engine.py` reads a `JobMatch` (and the associated `TailoredDocument` rows) to locate the CV and cover-letter PDFs.
11. On each attempt it creates an `Application` row and appends `ApplicationEvent` rows as the attempt progresses.
12. `applier/daily_limit.py` queries `Application` rows to enforce `SearchSettings.daily_limit`.

### Session management

13. `scraping/session_manager.py` reads `SiteCredential` (for decrypted login credentials) and reads/writes `BrowserSession` (for Playwright storage-state paths and expiry).

### API layer

14. All API routers (`api/jobs.py`, `api/applications.py`, `api/documents.py`, `api/settings.py`, `api/queue.py`, `api/analytics.py`) read and write the ORM models directly via `AsyncSession` injected through `database.get_db()`.

---

## Configuration

### Database URL

```
sqlite+aiosqlite:///{JOBPILOT_DATA_DIR}/jobpilot.db
```

- `JOBPILOT_DATA_DIR` defaults to `./data` (env var `JOBPILOT_DATA_DIR`).
- Configured in `backend/database.py` using `create_async_engine`.

### Connection Pool

- Pool class: `NullPool` (used in Alembic migrations via `env.py`).
- Application sessions use the default `async_sessionmaker` pool (SQLAlchemy's `AsyncAdaptedQueuePool`).
- `expire_on_commit=False` is set on `AsyncSessionLocal` to allow attribute access after commit without re-querying.

### WAL Mode

A `connect` event listener on the sync engine executes `PRAGMA journal_mode=WAL` on every new SQLite connection, improving concurrent read/write throughput for the async workload.

### Session Context

Two session access patterns are provided by `backend/database.py`:

- `db_session()` — async context manager with auto-commit and auto-rollback; used by the scheduler and applier.
- `get_db()` — async generator for FastAPI `Depends()` injection in API routers.

### Alembic

Migration chain:

```
071b973b48b2 (initial schema, 2026-02-28)
    └── df6eea4756c3 (add site_credentials, 2026-03-03)
```

Alembic is configured in `alembic.ini` with `sqlalchemy.url = sqlite+aiosqlite:///data/jobpilot.db` (used only for offline migrations; the application uses the settings-derived URL at runtime).

---

## Known Limitations / TODOs

1. **No database-level foreign keys.** All relationships between tables (e.g. `job_matches.job_id -> jobs.id`) are enforced only in application code. SQLite FK enforcement (`PRAGMA foreign_keys=ON`) is not enabled, so orphaned rows accumulate silently if parent rows are deleted.

2. **No cascading deletes.** Removing a `Job` row does not remove its `JobMatch`, `TailoredDocument`, or `Application` rows. The application has no cleanup mechanism for orphans.

3. **`countries` column missing from initial migration.** `SearchSettings.countries` was added to `user.py` after `071b973b48b2` was generated. There is no migration for it; it is only present when `init_db()` (which calls `create_all`) runs against a fresh database. Existing databases upgraded through Alembic alone will lack this column.

4. **No indexes on foreign-key-equivalent columns.** `job_matches.job_id`, `tailored_documents.job_match_id`, `applications.job_match_id`, and `application_events.application_id` have no indexes, which will cause full table scans as these tables grow.

5. **Singleton tables (`user_profile`, `search_settings`) have no uniqueness guard.** The `id` column defaults to `1` in the ORM but there is no DB-level constraint preventing a second row from being inserted with a different `id`.

6. **`updated_at` is never automatically refreshed on update.** Both `UserProfile.updated_at` and `SiteCredential.updated_at` default to `utcnow` at insert time but have no `onupdate` hook, so they do not reflect the time of the last modification unless the application explicitly sets them.

7. **`Job.source_id` is nullable with no FK constraint.** Jobs scraped from sources that were later deleted retain a dangling `source_id` value with no referential integrity check.

8. **`TailoredDocument.job_match_id` is nullable.** The column is defined `nullable=True` in the ORM, meaning it is possible (though unintentional in normal flow) to create a document record not linked to any match.
