# Module: API

## Purpose

The `backend/api` package is the HTTP and WebSocket surface of the JobPilot system. It exposes every client-facing capability as a set of FastAPI routers that the SvelteKit frontend — and any other HTTP client — consumes. Each router owns a narrow vertical slice of the domain: job browsing, the apply queue, application lifecycle management, tailored-document retrieval, user settings/credentials, usage analytics, and real-time push notifications over WebSocket. The module is deliberately thin: it validates input, calls into domain modules (`applier`, `scraping`, `latex`, `scheduler`, `matching`), reads from and writes to the shared SQLite/PostgreSQL database through SQLAlchemy async sessions, and serialises results back to the caller with Pydantic response models. No business logic lives here that belongs elsewhere.

---

## Key Components

### `__init__.py`

Package stub. Declares `__all__` listing the seven submodules (`jobs`, `queue`, `applications`, `documents`, `settings`, `analytics`, `ws`) so they can be imported uniformly from `backend.api`. Contains no route registrations itself.

---

### `deps.py`

Defines shared FastAPI dependency callables used across every router.

- **`DBSession`** — a type alias (`Annotated[AsyncSession, Depends(get_db)]`) used as a route-parameter annotation to inject an async SQLAlchemy session. Sourced from `backend.database.get_db`.
- **`get_session_manager(request)`** — retrieves the `BrowserSessionManager` singleton from `app.state.session_manager`.
- **`get_apply_engine(request)`** — retrieves the `ApplicationEngine` singleton from `app.state.apply_engine`.
- **`get_cv_pipeline(request)`** — retrieves the `CVPipeline` singleton from `app.state.cv_pipeline`.
- **`get_scraping_orchestrator(request)`** — retrieves the `ScrapingOrchestrator` singleton from `app.state.scraping_orchestrator`.
- **`get_morning_scheduler(request)`** — retrieves the `MorningBatchScheduler` singleton from `app.state.morning_scheduler`.

All singletons are placed on `app.state` during the FastAPI lifespan in `backend/main.py` and accessed here without importing the concrete classes at module load time (guarded by `TYPE_CHECKING`).

---

### `analytics.py`

Router prefix: `/api/analytics`. Provides high-level usage metrics derived from the `Application` table.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `AnalyticsSummary` | `total_apps: int`, `apps_this_week: int`, `response_rate: float`, `avg_match_score: Optional[float]` |
| `DailyTrend` | `date: str` (YYYY-MM-DD), `count: int` |
| `AnalyticsTrends` | `trends: list[DailyTrend]`, `days: int` |

---

### `applications.py`

Router prefix: `/api/applications`. Manages the full lifecycle of job applications, including creating, listing, updating, adding lifecycle events, and triggering apply automation.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `ApplicationEventOut` | `id`, `application_id`, `event_type`, `details: Optional[str]`, `event_date` |
| `ApplicationOut` | `id`, `job_match_id: Optional[int]`, `method`, `status`, `applied_at: Optional[datetime]`, `notes: Optional[str]`, `error_log: Optional[str]`, `created_at`, `events: list[ApplicationEventOut]`, `job_title`, `company`, `location`, `url` (last four denormalized from `Job`) |
| `ApplicationListOut` | `applications: list[ApplicationOut]`, `total: int` |
| `CreateApplicationRequest` | `job_match_id: Optional[int]`, `method: "auto"\|"assisted"\|"manual"` (default `"manual"`), `status` (default `"pending"`), `notes: Optional[str]` |
| `UpdateApplicationRequest` | `status: Optional[str]`, `notes: Optional[str]`, `applied_at: Optional[datetime]`, `error_log: Optional[str]` |
| `CreateEventRequest` | `event_type: Literal[...]`, `details: Optional[str]` |
| `ApplyRequest` | `method`, `apply_url`, `full_name`, `email`, `phone`, `location`, `additional_answers_json` |

---

### `documents.py`

Router prefix: `/api/documents`. Serves tailored CV and cover-letter PDFs, diff metadata, and template validation; also provides a document regeneration trigger.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `DocumentOut` | `id`, `job_match_id: Optional[int]`, `doc_type`, `tex_path: Optional[str]`, `pdf_path: Optional[str]`, `diff_json: Optional[dict]`, `created_at` |
| `RegenerateRequest` | `force: bool` (default `False`) |
| `ValidateTemplateRequest` | `tex_content: str` |

---

### `jobs.py`

Router prefix: `/api/jobs`. Browses and searches the scraped job corpus and exposes per-job match scores.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `JobOut` | `id`, `title`, `company`, `location`, `salary_text`, `salary_min`, `salary_max`, `description`, `url`, `apply_url`, `posted_at`, `scraped_at`, `score: Optional[float]` |
| `JobListOut` | `jobs: list[JobOut]`, `total: int` |
| `SearchRequest` | `keywords: list[str]`, `location: Optional[str]`, `country: str` (default `"gb"`), `max_results: int` (default `20`) |

---

### `queue.py`

Router prefix: `/api/queue`. Manages the daily apply queue — the list of `JobMatch` rows with `status="new"` produced by the morning batch. Supports listing, fetching single matches, skipping, status transitions, and manually triggering a batch re-run.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `JobOut` | `id`, `title`, `company`, `location`, `country`, `salary_min`, `salary_max`, `description`, `url`, `apply_url`, `apply_method`, `posted_at` |
| `QueueMatchOut` | `id` (match ID), `job_id`, `score: float`, `status: str`, `batch_date: Optional[date]`, `matched_at: datetime`, `job: JobOut` |
| `QueueOut` | `matches: list[QueueMatchOut]`, `total: int` |
| `StatusUpdate` | `status: str` |

---

### `settings.py`

Router prefix: `/api/settings`. Manages the single-user profile, search preferences, job-source site toggles, site credentials (Fernet-encrypted at rest), custom lab-URL sources, and system setup status.

**Local Pydantic models:**

| Model | Fields |
|---|---|
| `ProfileOut` | `id`, `full_name`, `email`, `phone`, `location`, `base_cv_path`, `base_letter_path`, `additional_info: Optional[dict]`, `created_at`, `updated_at` |
| `ProfileUpdate` | All `ProfileOut` fields except `id`/timestamps, all `Optional` |
| `SearchSettingsOut` | `id`, `keywords: dict`, `excluded_keywords`, `locations`, `salary_min`, `experience_min`, `experience_max`, `remote_only: bool`, `job_types`, `languages`, `excluded_companies`, `daily_limit: int`, `batch_time: str`, `min_match_score: float`, `countries` |
| `SearchSettingsUpdate` | Same fields as `SearchSettingsOut` minus `id`, all `Optional` |
| `SourcesUpdate` | `adzuna_app_id`, `adzuna_app_key`, `google_api_key` (all `Optional[str]`) |
| `SetupStatus` | `gemini_key_set: bool`, `adzuna_key_set: bool`, `tectonic_found: bool`, `base_cv_uploaded: bool`, `setup_complete: bool` |
| `SiteOut` | `name`, `display_name`, `type`, `requires_login: bool`, `base_url`, `enabled: bool`, `has_session: bool` |
| `SiteToggle` | `enabled: bool` |
| `CredentialOut` | `site_name`, `display_name`, `masked_email: Optional[str]`, `has_session: bool` |
| `CredentialUpdate` | `email: str`, `password: str` |
| `CustomSiteOut` | `id`, `name`, `display_name: Optional[str]`, `url: Optional[str]`, `enabled: bool` |
| `CustomSiteCreate` | `name: str`, `url: str`, `display_name: Optional[str]` |

---

### `ws.py`

Mounts a single WebSocket endpoint at `/ws`. Implements a `ConnectionManager` that tracks active connections by UUID, supports broadcast and unicast delivery, and dispatches inbound messages to registered handlers. Exposes a module-level `manager` singleton and a `broadcast_status` helper used by other backend modules to push progress updates to the frontend.

---

### `ws_models.py`

Defines all Pydantic message types for the WebSocket protocol. Split into server-to-client (`WSMessage` union) and client-to-server (`ClientMessage` union) directions, discriminated by the `type` field.

---

## Public Interface

### Analytics — `/api/analytics`

#### `GET /api/analytics/summary`

Returns high-level application statistics.

- **Auth:** none
- **Query params:** none
- **Response `200`:** `AnalyticsSummary`

```
{
  "total_apps": 42,
  "apps_this_week": 7,
  "response_rate": 14.3,        // percent of apps with status interview/offer/rejected
  "avg_match_score": 61.5       // nullable; null if JobMatch table empty or inaccessible
}
```

---

#### `GET /api/analytics/trends`

Returns daily application counts for the last N days (zero-filled for missing days).

- **Auth:** none
- **Query params:**
  - `days: int` — range 1–365, default `30`
- **Response `200`:** `AnalyticsTrends`

```
{
  "days": 30,
  "trends": [
    {"date": "2026-02-10", "count": 3},
    {"date": "2026-02-11", "count": 0},
    ...
  ]
}
```

---

### Applications — `/api/applications`

#### `POST /api/applications`

Create a new application record manually.

- **Auth:** none
- **Request body:** `CreateApplicationRequest`

```json
{
  "job_match_id": 17,
  "method": "manual",
  "status": "pending",
  "notes": "Applied via company portal"
}
```

- **`method`:** `"auto"` | `"assisted"` | `"manual"` (default `"manual"`)
- **`status`:** `"pending"` | `"applied"` | `"cancelled"` | `"failed"` | `"interview"` | `"offer"` | `"rejected"` (default `"pending"`)
- **Response `201`:** `ApplicationOut`

---

#### `GET /api/applications`

List applications with optional status filter and pagination.

- **Auth:** none
- **Query params:**
  - `skip: int` — default `0`, min `0`
  - `limit: int` — default `50`, range 1–200
  - `status: str` — optional filter (`pending`, `applied`, `cancelled`, `failed`, `interview`, `offer`, `rejected`)
- **Response `200`:** `ApplicationListOut`

```json
{
  "applications": [
    {
      "id": 1,
      "job_match_id": 17,
      "method": "auto",
      "status": "applied",
      "applied_at": "2026-03-01T08:12:00",
      "notes": null,
      "error_log": null,
      "created_at": "2026-03-01T08:10:00",
      "events": [...],
      "job_title": "Senior Python Engineer",
      "company": "Acme Corp",
      "location": "London",
      "url": "https://example.com/job/1"
    }
  ],
  "total": 42
}
```

Events are batch-fetched to avoid N+1 queries. Job fields (`job_title`, `company`, `location`, `url`) are denormalized via a `JobMatch → Job` outer join.

---

#### `GET /api/applications/{application_id}`

Get a single application with all its lifecycle events.

- **Auth:** none
- **Path params:** `application_id: int`
- **Response `200`:** `ApplicationOut`
- **Response `404`:** `{"detail": "Application {id} not found"}`

---

#### `PATCH /api/applications/{application_id}`

Update an application's status, notes, applied timestamp, or error log.

- **Auth:** none
- **Path params:** `application_id: int`
- **Request body:** `UpdateApplicationRequest` (all fields optional)

```json
{
  "status": "interview",
  "notes": "Phone screen scheduled",
  "applied_at": "2026-03-05T10:00:00",
  "error_log": null
}
```

- **Response `200`:** `ApplicationOut`
- **Response `404`:** application not found

---

#### `POST /api/applications/{application_id}/events`

Append a lifecycle event to an application.

- **Auth:** none
- **Path params:** `application_id: int`
- **Request body:** `CreateEventRequest`

```json
{
  "event_type": "interview",
  "details": "Technical interview scheduled for 2026-03-10"
}
```

- **`event_type`:** one of `pending`, `applied`, `cancelled`, `failed`, `interview`, `offer`, `rejected`, `viewed`, `follow_up`
- **Response `201`:** `ApplicationEventOut`

```json
{
  "id": 5,
  "application_id": 1,
  "event_type": "interview",
  "details": "Technical interview scheduled for 2026-03-10",
  "event_date": "2026-03-08T14:00:00"
}
```

- **Response `404`:** application not found

---

#### `POST /api/applications/{match_id}/apply`

Trigger automated or assisted application for a job match. Resolves the tailored CV and cover letter PDFs from the `TailoredDocument` table before delegating to `ApplicationEngine.apply()`.

- **Auth:** none
- **Path params:** `match_id: int` (a `JobMatch` ID)
- **Request body:** `ApplyRequest`

```json
{
  "method": "auto",
  "apply_url": "https://jobs.example.com/apply/123",
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+447700900000",
  "location": "London, UK",
  "additional_answers_json": "{\"years_experience\": \"5\"}"
}
```

- **`method`:** `"auto"` | `"assisted"` | `"manual"`
- **`apply_url`:** optional; if omitted, resolved from `Job.apply_url` or `Job.url`; must be `http`/`https` and ≤ 2048 characters
- **`additional_answers_json`:** optional JSON string, max 5000 characters after truncation
- **Response `200`:** the result dict from `engine.apply()` (structure defined by `ApplicationEngine`)
- **Response `503`:** `ApplicationEngine` not initialised or import failed
- **Response `422`:** invalid method value

---

### Documents — `/api/documents`

#### `GET /api/documents`

List all tailored documents in the database, newest first.

- **Auth:** none
- **Response `200`:** `list[DocumentOut]`

```json
[
  {
    "id": 3,
    "job_match_id": 17,
    "doc_type": "cv",
    "tex_path": "/data/documents/cv_17.tex",
    "pdf_path": "/data/documents/cv_17.pdf",
    "diff_json": {"sections": [...]},
    "created_at": "2026-03-01T08:00:00"
  }
]
```

---

#### `POST /api/documents/validate-template`

Check whether a LaTeX string contains the required JOBPILOT section markers.

- **Auth:** none
- **Request body:** `ValidateTemplateRequest`

```json
{"tex_content": "\\documentclass{article}..."}
```

- **Response `200`:**

```json
{
  "has_markers": true,
  "warnings": ["Missing %JOBPILOT:SKILLS% marker"]
}
```

Delegates to `backend.latex.parser.LaTeXParser`.

---

#### `GET /api/documents/{match_id}/cv/pdf`

Stream the compiled CV PDF for a job match.

- **Auth:** none
- **Path params:** `match_id: int`
- **Response `200`:** binary PDF (`application/pdf`), filename `cv_match_{match_id}.pdf`
- **Response `404`:** no document record, no compiled PDF, or file missing from disk

---

#### `GET /api/documents/{match_id}/letter/pdf`

Stream the compiled cover letter PDF for a job match.

- **Auth:** none
- **Path params:** `match_id: int`
- **Response `200`:** binary PDF (`application/pdf`), filename `letter_match_{match_id}.pdf`
- **Response `404`:** no document record, no compiled PDF, or file missing from disk

---

#### `GET /api/documents/{match_id}/diff`

Return the JSON diff of CV customisations made for a job match.

- **Auth:** none
- **Path params:** `match_id: int`
- **Response `200`:**

```json
{
  "match_id": 17,
  "diff": [...],           // contents of TailoredDocument.diff_json, or [] if null
  "generated_at": "2026-03-01T08:00:00"
}
```

- **Response `404`:** no CV document record for match

---

#### `POST /api/documents/{match_id}/regenerate`

Queue re-generation of tailored CV and cover letter for a job match. If `force=true`, deletes existing `TailoredDocument` rows first.

- **Auth:** none
- **Path params:** `match_id: int`
- **Request body:** `RegenerateRequest`

```json
{"force": false}
```

- **Response `200`:**

```json
{
  "match_id": 17,
  "status": "queued",
  "message": "Document regeneration has been queued"
}
```

- **Response `404`:** `JobMatch` not found

> Note: the actual pipeline invocation is deferred (see Known Limitations).

---

### Jobs — `/api/jobs`

#### `GET /api/jobs`

List scraped jobs, newest first. Attaches the latest `JobMatch.score` for each job.

- **Auth:** none
- **Query params:**
  - `skip: int` — default `0`
  - `limit: int` — default `50`, range 1–200
  - `min_score: float` — optional; jobs whose latest match score is below this threshold (or have no match) are excluded from the results list (total count is unaffected)
- **Response `200`:** `JobListOut`

```json
{
  "jobs": [
    {
      "id": 42,
      "title": "Backend Engineer",
      "company": "Acme",
      "location": "London",
      "salary_text": "£60k–£80k",
      "salary_min": 60000,
      "salary_max": 80000,
      "description": "...",
      "url": "https://...",
      "apply_url": "https://.../apply",
      "posted_at": "2026-03-01T00:00:00",
      "scraped_at": "2026-03-01T08:05:00",
      "score": 74.5
    }
  ],
  "total": 210
}
```

> Warning: score attachment uses a per-job subquery (N+1 pattern); see Known Limitations.

---

#### `GET /api/jobs/{job_id}`

Get a single job by ID, including its latest match score.

- **Auth:** none
- **Path params:** `job_id: int`
- **Response `200`:** `JobOut`
- **Response `404`:** job not found

---

#### `POST /api/jobs/search`

Trigger a live Adzuna API search, deduplicate results, and persist new jobs to the database.

- **Auth:** none
- **Request body:** `SearchRequest`

```json
{
  "keywords": ["python", "fastapi"],
  "location": "London",
  "country": "gb",
  "max_results": 20
}
```

- **Response `200`:**

```json
{
  "stored": 12,
  "jobs": [
    {"title": "Backend Engineer", "company": "Acme"},
    ...
  ]
}
```

- **Response `502`:** Adzuna API call failed

Deduplication uses an MD5 hash of `company|title|location` (lowercased). Already-seen hashes are skipped.

---

#### `GET /api/jobs/{job_id}/score`

Return only the latest match score and keyword hits for a job.

- **Auth:** none
- **Path params:** `job_id: int`
- **Response `200`:**

```json
{"job_id": 42, "score": 74.5, "keyword_hits": ["python", "fastapi"]}
// or, if no match exists:
{"job_id": 42, "score": null}
```

- **Response `404`:** job not found

---

### Queue — `/api/queue`

#### `GET /api/queue`

Return all pending job matches (`status="new"`), ordered by `batch_date desc` then `score desc`.

- **Auth:** none
- **Response `200`:** `QueueOut`

```json
{
  "matches": [
    {
      "id": 7,
      "job_id": 42,
      "score": 81.0,
      "status": "new",
      "batch_date": "2026-03-11",
      "matched_at": "2026-03-11T08:05:00",
      "job": {
        "id": 42,
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "London",
        "country": "gb",
        "salary_min": 60000,
        "salary_max": 80000,
        "description": "...",
        "url": "https://...",
        "apply_url": "https://.../apply",
        "apply_method": "form",
        "posted_at": "2026-03-01T00:00:00"
      }
    }
  ],
  "total": 5
}
```

---

#### `POST /api/queue/refresh`

Manually trigger a morning batch run in a background asyncio task.

- **Auth:** none
- **Response `200`:**

```json
{"status": "started", "message": "Morning batch triggered in background"}
```

- **Response `503`:** `morning_scheduler` not on `app.state`

---

#### `GET /api/queue/{match_id}`

Fetch a single match with its nested job.

- **Auth:** none
- **Path params:** `match_id: int`
- **Response `200`:** `QueueMatchOut`
- **Response `404`:** match not found

---

#### `PATCH /api/queue/{match_id}/skip`

Mark a queue match as `"skipped"`.

- **Auth:** none
- **Path params:** `match_id: int`
- **Response `200`:** `{"match_id": 7, "status": "skipped"}`
- **Response `404`:** match not found

---

#### `PATCH /api/queue/{match_id}/status`

Set the match status to any allowed value.

- **Auth:** none
- **Path params:** `match_id: int`
- **Request body:** `StatusUpdate`

```json
{"status": "applying"}
```

- **Allowed values:** `"new"`, `"skipped"`, `"applying"`, `"applied"`, `"rejected"`
- **Response `200`:** `{"match_id": 7, "status": "applying"}`
- **Response `422`:** invalid status
- **Response `404`:** match not found

---

### Settings — `/api/settings`

#### `GET /api/settings/profile`

Retrieve the singleton user profile (id=1). Returns a zeroed-out profile object if none exists (rather than 404).

- **Auth:** none
- **Response `200`:** `ProfileOut`

---

#### `PUT /api/settings/profile`

Create or update (upsert) the user profile.

- **Auth:** none
- **Request body:** `ProfileUpdate` (all fields optional)

```json
{
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+447700900000",
  "location": "London",
  "base_cv_path": "/data/templates/cv.tex",
  "base_letter_path": "/data/templates/letter.tex",
  "additional_info": {"linkedin": "https://linkedin.com/in/jane"}
}
```

- **Response `200`:** `ProfileOut`

---

#### `GET /api/settings/search`

Get current search/matching settings (singleton, id=1).

- **Auth:** none
- **Response `200`:** `SearchSettingsOut`
- **Response `404`:** settings not yet created

---

#### `PUT /api/settings/search`

Create or update (upsert) search settings.

- **Auth:** none
- **Request body:** `SearchSettingsUpdate` (all fields optional)

```json
{
  "keywords": {"include": ["python", "fastapi"], "require_all": false},
  "excluded_keywords": {"exclude": ["senior manager"]},
  "locations": {"cities": ["London"]},
  "salary_min": 50000,
  "experience_min": 2,
  "experience_max": 8,
  "remote_only": false,
  "job_types": {"types": ["full-time"]},
  "languages": {"langs": ["English"]},
  "excluded_companies": {"names": []},
  "daily_limit": 10,
  "batch_time": "08:00",
  "min_match_score": 40.0,
  "countries": {"codes": ["gb"]}
}
```

Defaults on first creation: `keywords={"include": []}`, `remote_only=false`, `daily_limit=10`, `batch_time="08:00"`, `min_match_score=30.0`.

- **Response `200`:** `SearchSettingsOut`

---

#### `GET /api/settings/sources`

Return which external API sources are configured (keys masked, never returned in full).

- **Auth:** none
- **Response `200`:**

```json
{
  "adzuna": {
    "configured": true,
    "app_id_hint": "a1b2****"
  },
  "gemini": {
    "configured": true
  }
}
```

Values are considered unconfigured if they are `None`, `""`, or `"placeholder"`.

---

#### `PUT /api/settings/sources`

Placeholder route — returns guidance rather than accepting keys, because API keys are managed via `.env`.

- **Auth:** none
- **Request body:** `SourcesUpdate` (accepted but ignored)
- **Response `200`:**

```json
{
  "message": "API keys must be set in the .env file at the project root. Edit ADZUNA_APP_ID, ADZUNA_APP_KEY, and GOOGLE_API_KEY then restart the server.",
  "env_file": ".env"
}
```

---

#### `GET /api/settings/status`

Return setup completeness flags, used by the frontend onboarding flow.

- **Auth:** none
- **Response `200`:** `SetupStatus`

```json
{
  "gemini_key_set": true,
  "adzuna_key_set": true,
  "tectonic_found": true,
  "base_cv_uploaded": true,
  "setup_complete": true
}
```

`tectonic_found` checks for `bin/tectonic` relative to CWD or `tectonic` on `PATH`. `base_cv_uploaded` checks `UserProfile.base_cv_path` on disk, falling back to any `*.tex` file in `{JOBPILOT_DATA_DIR}/templates/`. `setup_complete` is `gemini_key_set AND adzuna_key_set AND base_cv_uploaded` (tectonic is not required).

---

#### `GET /api/settings/sites`

Return all known job-source sites with their enabled state and session presence.

- **Auth:** none
- **Response `200`:** `list[SiteOut]`

```json
[
  {
    "name": "linkedin",
    "display_name": "LinkedIn",
    "type": "browser",
    "requires_login": true,
    "base_url": "https://www.linkedin.com",
    "enabled": true,
    "has_session": false
  }
]
```

Site config is sourced from `backend.scraping.site_prompts.SITE_CONFIGS`; enabled state is stored in the `JobSource` table and defaults to `true`.

---

#### `PUT /api/settings/sites/{site_name}`

Enable or disable a job-source site.

- **Auth:** none
- **Path params:** `site_name: str`
- **Request body:** `SiteToggle`

```json
{"enabled": false}
```

- **Response `200`:** `{"name": "linkedin", "enabled": false}`
- **Response `404`:** unknown site name

---

#### `GET /api/settings/credentials`

Return sites that require login, with masked email addresses and session status. Emails are Fernet-decrypted for masking (first two characters + `***@domain`).

- **Auth:** none
- **Response `200`:** `list[CredentialOut]`

```json
[
  {
    "site_name": "linkedin",
    "display_name": "LinkedIn",
    "masked_email": "ja***@example.com",
    "has_session": true
  }
]
```

---

#### `PUT /api/settings/credentials/{site_name}`

Encrypt and store email/password for a login-required site using Fernet symmetric encryption.

- **Auth:** none
- **Path params:** `site_name: str`
- **Request body:** `CredentialUpdate`

```json
{"email": "jane@example.com", "password": "s3cret"}
```

- **Response `200`:** `{"site_name": "linkedin", "saved": true}`
- **Response `400`:** site does not require login, or `CREDENTIAL_KEY` not set
- **Response `404`:** unknown site name

---

#### `DELETE /api/settings/credentials/{site_name}/session`

Delete browser session state files for a site (both new `browser_profiles/{site}/state.json` path and legacy `browser_sessions/{site}_state.json` path).

- **Auth:** none
- **Path params:** `site_name: str`
- **Response `200`:** `{"cleared": true}` or `{"cleared": false}` (if no files existed)
- **Response `404`:** unknown site name

---

#### `GET /api/settings/custom-sites`

Return custom / lab-URL job sources from the database.

- **Auth:** none
- **Response `200`:** `list[CustomSiteOut]`

```json
[
  {"id": 5, "name": "mylab", "display_name": "My Lab Jobs", "url": "https://mylab.io/jobs", "enabled": true}
]
```

---

#### `POST /api/settings/custom-sites`

Add a new custom lab/URL job source.

- **Auth:** none
- **Request body:** `CustomSiteCreate`

```json
{"name": "mylab", "url": "https://mylab.io/jobs", "display_name": "My Lab Jobs"}
```

- **Response `200`:** `CustomSiteOut`

---

#### `DELETE /api/settings/custom-sites/{site_id}`

Delete a custom site by its database ID.

- **Auth:** none
- **Path params:** `site_id: int`
- **Response `200`:** `{"deleted": 5}`
- **Response `404`:** custom site not found

---

### WebSocket — `/ws`

#### `WebSocket /ws`

Persistent bidirectional connection for real-time push notifications and interactive apply review/confirmation. Each connection receives a UUID on connect. The server maintains a `ConnectionManager` with an `asyncio.Lock`-protected dict of active connections.

**Inbound ping/pong (built-in):**

| Client sends | Server replies |
|---|---|
| `{"type": "ping"}` | `{"type": "pong"}` |

**Server-to-client message types (`WSMessage` union, discriminated by `type`):**

| Type | Fields | Description |
|---|---|---|
| `scraping_status` | `message: str`, `source: str`, `progress: float` | Progress update during a scraping run for a specific job source |
| `matching_status` | `count: int` | Emitted after matching; reports how many jobs were matched |
| `tailoring_status` | `job_id: int`, `progress: float` | Progress of CV/letter tailoring for a specific job |
| `apply_review` | `job_id: int`, `filled_fields: dict[str, str]`, `screenshot_base64: str \| None` | In assisted-apply mode: sends the pre-filled form to the client for review before submission |
| `apply_result` | `job_id: int`, `status: str`, `method: str` | Final outcome of an apply attempt |
| `login_required` | `site: str`, `browser_window_title: str` | Signals that the browser session needs manual login |
| `login_confirmed` | `site: str` | Signals that login was detected as successful |
| `error` | `message: str`, `code: str` | Generic error notification |

**Client-to-server message types (`ClientMessage` union, discriminated by `type`):**

| Type | Fields | Description |
|---|---|---|
| `confirm_submit` | `job_id: int` | User confirms that the pre-filled apply form should be submitted |
| `cancel_apply` | `job_id: int` | User cancels the pending apply review |
| `login_done` | `site: str` | User signals they have completed manual login for a site |
| `login_cancel` | `site: str` | User cancels the login flow for a site |

Client messages route to handlers registered via `manager.register_handler(msg_type, handler)`. Handlers are registered by other backend modules (e.g., the applier engine) at runtime; unrecognised types are silently ignored.

**Helper function:**
- `broadcast_status(message: str, progress: float = 0.0)` — broadcasts `{"type": "status", "message": ..., "progress": ...}` to all connected clients. Used by scraping and scheduler modules.

---

## Data Flow

**Inputs:**
- HTTP requests (JSON bodies, query params, path params, binary PDF streams) from the SvelteKit frontend or any HTTP client.
- WebSocket frames from connected browser clients.

**Outputs:**
- JSON responses serialised by Pydantic response models.
- Binary PDF file streams (`FileResponse`) for document download.
- WebSocket text frames (JSON) broadcast or unicast to connected clients.

**Database (read/write):**
- `Application` — CRUD + event appending (`applications.py`)
- `ApplicationEvent` — append only (`applications.py`)
- `Job` — read list/detail/score; write on manual search (`jobs.py`, `queue.py`)
- `JobMatch` — read queue; write status updates; used for join in applications (`queue.py`, `applications.py`, `jobs.py`)
- `TailoredDocument` — read PDF paths and diff JSON; delete on force-regenerate (`documents.py`, `applications.py`)
- `UserProfile` — singleton upsert + read (`settings.py`)
- `SearchSettings` — singleton upsert + read (`settings.py`)
- `JobSource` — read/write site enabled state; read/write custom sites (`settings.py`)
- `SiteCredential` — read/write Fernet-encrypted credentials (`settings.py`)

**External systems (via domain modules):**
- `backend.applier.engine.ApplicationEngine` (`app.state.apply_engine`) — invoked by `POST /api/applications/{match_id}/apply`.
- `backend.scheduler.morning_batch.MorningBatchScheduler` (`app.state.morning_scheduler`) — invoked by `POST /api/queue/refresh`.
- `backend.scraping.adzuna_client.AdzunaClient` — instantiated inline by `POST /api/jobs/search`.
- `backend.latex.parser.LaTeXParser` — instantiated inline by `POST /api/documents/validate-template`.
- Filesystem — reads PDF files for streaming; reads/deletes browser session state files in `JOBPILOT_DATA_DIR`.

---

## Configuration

All settings are loaded from `.env` (or environment variables) via `backend.config.Settings` (pydantic-settings). The following keys are read by this module:

| Variable | Type | Default | Used by |
|---|---|---|---|
| `GOOGLE_API_KEY` | `str` | — (required) | `settings.py` — `gemini_key_set` check |
| `ADZUNA_APP_ID` | `str` | — (required) | `settings.py` — `adzuna_key_set` check; `jobs.py` (via `AdzunaClient`) |
| `ADZUNA_APP_KEY` | `str` | — (required) | `settings.py` — `adzuna_key_set` check; `jobs.py` (via `AdzunaClient`) |
| `CREDENTIAL_KEY` | `str` | `""` | `settings.py` — Fernet key for encrypting/decrypting site credentials |
| `JOBPILOT_DATA_DIR` | `str` | `"./data"` | `settings.py` — base path for `browser_profiles/`, `browser_sessions/`, `templates/` |

Values of `None`, `""`, or `"placeholder"` are treated as "not configured" by the `/api/settings/sources` and `/api/settings/status` endpoints.

---

## Known Limitations / TODOs

1. **No authentication.** Every endpoint is completely unauthenticated. The system is designed as a local single-user tool, but there are no guards preventing access over a network.

2. **N+1 query in `GET /api/jobs`.** Score attachment issues one `SELECT JobMatch.score` subquery per returned job rather than a single join, degrading performance as the job table grows.

3. **`avg_match_score` silently swallowed.** In `GET /api/analytics/summary`, the `JobMatch.score` average is wrapped in a bare `except Exception: pass`, so any model import error or DB problem returns `null` without logging a warning.

4. **`POST /api/documents/{match_id}/regenerate` is a stub.** The comment in `documents.py` (line 193) reads: `"Queue background regeneration (actual pipeline call deferred to Wave 3 scheduler)"`. No CV pipeline is actually invoked; the endpoint only optionally deletes existing rows and returns `"status": "queued"`.

5. **`PUT /api/settings/sources` is a no-op.** The endpoint accepts the request body but ignores it entirely, returning only a guidance message. There is no mechanism to write API keys at runtime.

6. **Hardcoded profile singleton (`id=1`).** `UserProfile` and `SearchSettings` are always fetched/written with `id=1`. Multi-user support would require a complete redesign of these endpoints.

7. **Hardcoded response-rate statuses.** The response rate in `GET /api/analytics/summary` is computed against a hardcoded tuple `("interview", "offer", "rejected")`. This cannot be configured at runtime.

8. **`broadcast_status` sends a raw dict, not a typed model.** The `ws.py` helper sends `{"type": "status", ...}` which does not correspond to any model in `ws_models.py`. The `WSMessage` union has no `status` variant; this message type will not be parsed correctly by any client using `WSMessage` discriminated deserialization.

9. **WebSocket handler dispatch ignores `ClientMessage` union.** Inbound messages are dispatched by raw `msg.get("type")` string lookup into `manager._message_handlers`, bypassing `ws_models.ClientMessage` validation entirely. Handlers must validate their own input.

10. **`GET /api/settings/profile` returns id=0 on missing profile.** Rather than a `404`, a zeroed `ProfileOut` with `id=0` is returned when no profile has been created. Frontend code must handle this edge case explicitly.

11. **Deduplication hash in `POST /api/jobs/search` uses MD5.** MD5 is used for the dedup hash (`company|title|location`). While not a security concern in this context, a collision-resistant algorithm (SHA-256) would be more appropriate for production use.

12. **`POST /api/queue/refresh` creates an untracked asyncio task.** The background batch task is created with `asyncio.create_task()` and not stored anywhere. If an exception occurs, it is only logged; there is no result or progress reported back via WebSocket or the response.
