# API Reference

## Base URL

```
http://localhost:8000
```

All REST endpoints are prefixed with `/api`. The WebSocket endpoint is at `/ws`.

## Authentication

There is no authentication. Every endpoint is completely open. JobPilot is designed as a local single-user tool and relies on network-level access control (binding to `127.0.0.1` by default) as its only security boundary. There are no API keys, sessions, JWTs, or OAuth flows on the backend.

---

## REST Endpoints

### Jobs

#### `GET /api/jobs`

**Description:** List all scraped job postings, newest first. Each job is annotated with its latest `JobMatch.score` if one exists.

**Auth required:** No

**Query params:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `skip` | integer | 0 | Number of rows to skip (offset pagination) |
| `limit` | integer | 50 | Max rows to return; range 1–200 |
| `min_score` | float | none | If provided, jobs with a match score below this value (or no score at all) are excluded from the `jobs` array; the `total` count is not filtered |

**Request body:** None

**Response `200`:**

```json
{
  "jobs": [
    {
      "id": 42,
      "title": "Backend Engineer",
      "company": "Acme Corp",
      "location": "London, UK",
      "salary_text": "£60k – £80k",
      "salary_min": 60000,
      "salary_max": 80000,
      "description": "We are looking for a senior...",
      "url": "https://linkedin.com/jobs/view/42",
      "apply_url": "https://linkedin.com/jobs/apply/42",
      "posted_at": "2026-03-01T00:00:00",
      "scraped_at": "2026-03-11T08:05:00",
      "score": 74.5
    }
  ],
  "total": 210
}
```

`score` is `null` when no `JobMatch` row exists for the job.

**Error responses:**

| Status | Condition |
|---|---|
| 500 | Unhandled database or serialisation error |

---

#### `GET /api/jobs/{job_id}`

**Description:** Retrieve a single job by its database ID, including its latest match score.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `job_id` | integer | Database ID of the job |

**Query params:** None

**Request body:** None

**Response `200`:** Same shape as a single entry in `GET /api/jobs`.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No job with the given ID |

---

#### `POST /api/jobs/search`

**Description:** Trigger a live search against the Adzuna API. New results are deduplicated against the database and stored. Returns the count of newly stored jobs and a summary list of all returned jobs (including existing duplicates).

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:**

```json
{
  "keywords": ["python", "fastapi"],
  "location": "London",
  "country": "gb",
  "max_results": 20
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `keywords` | array of strings | Yes | Search terms joined into the Adzuna query string |
| `location` | string | No | City or region string passed to Adzuna `where` parameter |
| `country` | string | No (default `"gb"`) | ISO 2-letter country code for the Adzuna country endpoint |
| `max_results` | integer | No (default `20`) | Maximum results requested from Adzuna; Adzuna caps at 50 per page |

**Response `200`:**

```json
{
  "stored": 12,
  "jobs": [
    {"title": "Backend Engineer", "company": "Acme Corp"},
    {"title": "Python Developer", "company": "StartupXYZ"}
  ]
}
```

`stored` is the count of newly inserted rows (duplicates are skipped). `jobs` contains all jobs returned by Adzuna regardless of whether they were new.

**Error responses:**

| Status | Condition |
|---|---|
| 502 | Adzuna API returned a non-200 response |

---

#### `GET /api/jobs/{job_id}/score`

**Description:** Return only the latest match score and keyword hits for a job, without fetching the full job record.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `job_id` | integer | Database ID of the job |

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{"job_id": 42, "score": 74.5, "keyword_hits": ["python", "fastapi"]}
```

When no `JobMatch` row exists:

```json
{"job_id": 42, "score": null}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No job with the given ID |

---

### Queue

#### `GET /api/queue`

**Description:** Return all pending job matches with `status="new"`, ordered by `batch_date` descending then `score` descending. Each match includes the full embedded job record.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

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
        "company": "Acme Corp",
        "location": "London, UK",
        "country": "gb",
        "salary_min": 60000,
        "salary_max": 80000,
        "description": "We are looking for...",
        "url": "https://linkedin.com/jobs/view/42",
        "apply_url": "https://linkedin.com/jobs/apply/42",
        "apply_method": "easy_apply",
        "posted_at": "2026-03-01T00:00:00"
      }
    }
  ],
  "total": 5
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 500 | Database error |

---

#### `POST /api/queue/refresh`

**Description:** Manually trigger a morning batch run (scrape + match + store + pre-generate CVs + broadcast). The batch is launched as a background `asyncio.create_task()` and the endpoint returns immediately. Progress is delivered over WebSocket.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None (empty body or omit)

**Response `200`:**

```json
{"status": "started", "message": "Morning batch triggered in background"}
```

**Error responses:**

| Status | Condition |
|---|---|
| 503 | `morning_scheduler` singleton is not on `app.state` (failed to initialise at startup) |

---

#### `GET /api/queue/{match_id}`

**Description:** Fetch a single queue match by its `JobMatch` ID with the embedded job record.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | Database ID of the `JobMatch` row |

**Query params:** None

**Request body:** None

**Response `200`:** Same shape as a single entry in `GET /api/queue`.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No match with the given ID |

---

#### `PATCH /api/queue/{match_id}/skip`

**Description:** Mark a queue match as `"skipped"`, removing it from the default queue view.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{"match_id": 7, "status": "skipped"}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No match with the given ID |

---

#### `PATCH /api/queue/{match_id}/status`

**Description:** Set the status of a queue match to any allowed value.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:**

```json
{"status": "applying"}
```

| Field | Type | Required | Allowed values |
|---|---|---|---|
| `status` | string | Yes | `"new"`, `"skipped"`, `"applying"`, `"applied"`, `"rejected"` |

**Response `200`:**

```json
{"match_id": 7, "status": "applying"}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No match with the given ID |
| 422 | `status` is not one of the allowed values |

---

### Applications

#### `POST /api/applications`

**Description:** Create a new application record manually (without triggering automation).

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:**

```json
{
  "job_match_id": 17,
  "method": "manual",
  "status": "pending",
  "notes": "Applied via company portal"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `job_match_id` | integer | No | ID of the associated `JobMatch` row |
| `method` | string | No (default `"manual"`) | `"auto"`, `"assisted"`, or `"manual"` |
| `status` | string | No (default `"pending"`) | `"pending"`, `"applied"`, `"cancelled"`, `"failed"`, `"interview"`, `"offer"`, or `"rejected"` |
| `notes` | string | No | Freeform notes |

**Response `201`:**

```json
{
  "id": 1,
  "job_match_id": 17,
  "method": "manual",
  "status": "pending",
  "applied_at": null,
  "notes": "Applied via company portal",
  "error_log": null,
  "created_at": "2026-03-11T10:00:00",
  "events": [],
  "job_title": "Backend Engineer",
  "company": "Acme Corp",
  "location": "London, UK",
  "url": "https://linkedin.com/jobs/view/42"
}
```

The `job_title`, `company`, `location`, and `url` fields are denormalized from the linked `Job` via `JobMatch`. They are `null` if no `job_match_id` is provided or the join fails.

**Error responses:**

| Status | Condition |
|---|---|
| 422 | Invalid `method` or `status` value |

---

#### `GET /api/applications`

**Description:** List applications with optional status filter and pagination. Each application includes all its lifecycle events and denormalized job fields.

**Auth required:** No

**Path params:** None

**Query params:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `skip` | integer | 0 | Offset for pagination (min 0) |
| `limit` | integer | 50 | Max rows to return; range 1–200 |
| `status` | string | none | Filter to a specific status value. One of: `pending`, `applied`, `cancelled`, `failed`, `interview`, `offer`, `rejected` |

**Request body:** None

**Response `200`:**

```json
{
  "applications": [
    {
      "id": 1,
      "job_match_id": 17,
      "method": "auto",
      "status": "applied",
      "applied_at": "2026-03-11T08:12:00",
      "notes": null,
      "error_log": null,
      "created_at": "2026-03-11T08:10:00",
      "events": [
        {
          "id": 1,
          "application_id": 1,
          "event_type": "applied",
          "details": "Submitted via AutoApplyStrategy Tier 1",
          "event_date": "2026-03-11T08:12:00"
        }
      ],
      "job_title": "Backend Engineer",
      "company": "Acme Corp",
      "location": "London, UK",
      "url": "https://linkedin.com/jobs/view/42"
    }
  ],
  "total": 42
}
```

Events are batch-fetched to avoid N+1 queries.

**Error responses:**

| Status | Condition |
|---|---|
| 500 | Database error |

---

#### `GET /api/applications/{application_id}`

**Description:** Get a single application by its database ID, including all lifecycle events.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `application_id` | integer | Database ID of the `Application` row |

**Query params:** None

**Request body:** None

**Response `200`:** Same shape as a single entry in `GET /api/applications`.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No application with the given ID |

---

#### `PATCH /api/applications/{application_id}`

**Description:** Update an application's mutable fields. All fields are optional; only provided fields are updated.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `application_id` | integer | Database ID of the application |

**Query params:** None

**Request body:**

```json
{
  "status": "interview",
  "notes": "Phone screen scheduled for Friday",
  "applied_at": "2026-03-11T10:00:00",
  "error_log": null
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | string | No | New status value |
| `notes` | string | No | Freeform notes |
| `applied_at` | datetime (ISO 8601) | No | Override the applied timestamp |
| `error_log` | string | No | Error details; pass `null` to clear |

**Response `200`:** Full `ApplicationOut` with updated fields.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No application with the given ID |

---

#### `POST /api/applications/{application_id}/events`

**Description:** Append a lifecycle event to an existing application. Events are append-only; they cannot be edited or deleted.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `application_id` | integer | Database ID of the application |

**Query params:** None

**Request body:**

```json
{
  "event_type": "interview",
  "details": "Technical interview scheduled for 2026-03-15 at 14:00"
}
```

| Field | Type | Required | Allowed values for `event_type` |
|---|---|---|---|
| `event_type` | string | Yes | `"pending"`, `"applied"`, `"cancelled"`, `"failed"`, `"interview"`, `"offer"`, `"rejected"`, `"viewed"`, `"follow_up"` |
| `details` | string | No | Human-readable description of the event |

**Response `201`:**

```json
{
  "id": 5,
  "application_id": 1,
  "event_type": "interview",
  "details": "Technical interview scheduled for 2026-03-15 at 14:00",
  "event_date": "2026-03-11T14:00:00"
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No application with the given ID |
| 422 | `event_type` is not an allowed value |

---

#### `POST /api/applications/{match_id}/apply`

**Description:** Trigger automated or assisted application for a job match. The API layer resolves the tailored CV and cover letter PDF paths from the `TailoredDocument` table, resolves the `apply_url` from the `Job` record if not supplied, and delegates to `ApplicationEngine.apply()`. For `auto` and `assisted` modes, the result is delivered interactively via WebSocket while this HTTP request is in progress.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID (not `Application` ID) |

**Query params:** None

**Request body:**

```json
{
  "method": "auto",
  "apply_url": "https://jobs.example.com/apply/123",
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+447700900000",
  "location": "London, UK",
  "additional_answers_json": "{\"years_experience\": \"5\", \"notice_period\": \"1 month\"}"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `method` | string | Yes | `"auto"`, `"assisted"`, or `"manual"` |
| `apply_url` | string | No | Direct application URL. If omitted, resolved from `Job.apply_url` falling back to `Job.url`. Must be `http` or `https`, max 2048 characters. |
| `full_name` | string | No | Applicant's full name for form filling |
| `email` | string | No | Applicant's email for form filling |
| `phone` | string | No | Applicant's phone number for form filling |
| `location` | string | No | Applicant's location for form filling |
| `additional_answers_json` | string | No | JSON-serialised dict of custom question/answer pairs. Max 5000 characters. Truncated silently if longer. |

**Response `200`:**

```json
{
  "status": "applied",
  "method": "auto",
  "message": "Application submitted successfully via Tier 1 form filler"
}
```

The response structure is the `ApplicationResult` dict returned by the engine. `status` is one of `"applied"`, `"assisted"`, `"manual"`, or `"cancelled"`.

**Error responses:**

| Status | Condition |
|---|---|
| 422 | `method` is not one of the allowed values, or `apply_url` is not a valid HTTP/HTTPS URL |
| 503 | `ApplicationEngine` singleton is not available (startup failure) |

---

### Documents

#### `GET /api/documents`

**Description:** List all tailored document records in the database, newest first.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
[
  {
    "id": 3,
    "job_match_id": 17,
    "doc_type": "cv",
    "tex_path": "/home/user/data/cvs/17/cv.tex",
    "pdf_path": "/home/user/data/cvs/17/cv.pdf",
    "diff_json": [
      {
        "section": "Experience",
        "original_text": "Developed internal tooling",
        "edited_text": "Developed and deployed internal tooling using Python and FastAPI",
        "change_description": "Aligned with job requirement for FastAPI experience"
      }
    ],
    "created_at": "2026-03-11T08:00:00"
  }
]
```

**Error responses:**

| Status | Condition |
|---|---|
| 500 | Database error |

---

#### `POST /api/documents/validate-template`

**Description:** Check whether a LaTeX string contains properly balanced JOBPILOT section marker pairs. Returns whether markers are present and a list of any imbalance warnings.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:**

```json
{"tex_content": "\\documentclass{article}\n% --- JOBPILOT:SUMMARY:START ---\nMy summary.\n% --- JOBPILOT:SUMMARY:END ---\n\\begin{document}\\end{document}"}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `tex_content` | string | Yes | Full LaTeX source to validate |

**Response `200`:**

```json
{
  "has_markers": true,
  "warnings": []
}
```

If markers are imbalanced:

```json
{
  "has_markers": true,
  "warnings": ["Marker 'EXPERIENCE' has START but no END"]
}
```

An empty `warnings` list means all markers are balanced. `has_markers` is `false` when no JOBPILOT markers are found at all (the template will still compile but LLM-guided section editing will not work).

**Error responses:**

| Status | Condition |
|---|---|
| 422 | LaTeX compilation error (surface as `latex_compile_error` code) |

---

#### `GET /api/documents/{match_id}/cv/pdf`

**Description:** Stream the compiled tailored CV PDF for a job match as a binary file download.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:** None

**Response `200`:** Binary PDF stream with headers:
- `Content-Type: application/pdf`
- `Content-Disposition: inline; filename="cv_match_{match_id}.pdf"`

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No `TailoredDocument` with `doc_type="cv"` for this match, no `pdf_path` on the record, or the PDF file does not exist on disk |

---

#### `GET /api/documents/{match_id}/letter/pdf`

**Description:** Stream the compiled tailored cover letter PDF for a job match.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:** None

**Response `200`:** Binary PDF stream with headers:
- `Content-Type: application/pdf`
- `Content-Disposition: inline; filename="letter_match_{match_id}.pdf"`

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No `TailoredDocument` with `doc_type="letter"` for this match, no `pdf_path`, or file missing from disk |

---

#### `GET /api/documents/{match_id}/diff`

**Description:** Return the JSON diff of CV customisations made for a job match, showing what text was changed and in which section.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{
  "match_id": 17,
  "diff": [
    {
      "section": "Experience",
      "original_text": "Developed internal tooling",
      "edited_text": "Developed and deployed internal tooling using Python and FastAPI",
      "change_description": "Aligned with job requirement for FastAPI experience"
    }
  ],
  "generated_at": "2026-03-11T08:00:00"
}
```

`diff` is an empty array if `TailoredDocument.diff_json` is `null`.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No `TailoredDocument` with `doc_type="cv"` for this match |

---

#### `POST /api/documents/{match_id}/regenerate`

**Description:** Queue re-generation of tailored documents for a job match. If `force=true`, existing `TailoredDocument` rows for this match are deleted first. **Note:** The actual pipeline invocation is not yet implemented; this endpoint returns `"status": "queued"` but does not trigger CV generation.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `match_id` | integer | `JobMatch` ID |

**Query params:** None

**Request body:**

```json
{"force": false}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `force` | boolean | No (default `false`) | If `true`, delete existing `TailoredDocument` rows for this match before queuing |

**Response `200`:**

```json
{
  "match_id": 17,
  "status": "queued",
  "message": "Document regeneration has been queued"
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No `JobMatch` with the given ID |

---

### Analytics

#### `GET /api/analytics/summary`

**Description:** Return high-level application statistics derived from the `Application` table and `JobMatch` table.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{
  "total_apps": 42,
  "apps_this_week": 7,
  "response_rate": 14.3,
  "avg_match_score": 61.5
}
```

| Field | Type | Description |
|---|---|---|
| `total_apps` | integer | Total `Application` rows |
| `apps_this_week` | integer | `Application` rows with `created_at` in the last 7 days |
| `response_rate` | float | Percentage of applications with `status IN ("interview", "offer", "rejected")` |
| `avg_match_score` | float or null | Average `JobMatch.score` across all matches; `null` if the table is empty or the query fails silently |

**Error responses:**

| Status | Condition |
|---|---|
| 500 | Database error |

---

#### `GET /api/analytics/trends`

**Description:** Return daily application counts for the last N days, zero-filled for days with no applications.

**Auth required:** No

**Path params:** None

**Query params:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | integer | 30 | Number of days to include; range 1–365 |

**Request body:** None

**Response `200`:**

```json
{
  "days": 30,
  "trends": [
    {"date": "2026-02-10", "count": 3},
    {"date": "2026-02-11", "count": 0},
    {"date": "2026-02-12", "count": 1}
  ]
}
```

Dates are in `YYYY-MM-DD` format. The array always has exactly `days` entries, ordered from oldest to newest.

**Error responses:**

| Status | Condition |
|---|---|
| 422 | `days` is outside the range 1–365 |

---

### Settings

#### `GET /api/settings/profile`

**Description:** Retrieve the singleton user profile (row with `id=1`). Returns a zeroed-out profile with `id=0` rather than a 404 if no profile has been created yet. Check `id == 0` to detect the unset state.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{
  "id": 1,
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+447700900000",
  "location": "London, UK",
  "base_cv_path": "/data/templates/cv.tex",
  "base_letter_path": "/data/templates/letter.tex",
  "additional_info": {"linkedin": "https://linkedin.com/in/jane"},
  "created_at": "2026-03-01T00:00:00",
  "updated_at": "2026-03-11T10:00:00"
}
```

---

#### `PUT /api/settings/profile`

**Description:** Create or update (upsert) the singleton user profile. All fields are optional; missing fields are left unchanged on update.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:**

```json
{
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+447700900000",
  "location": "London, UK",
  "base_cv_path": "/data/templates/cv.tex",
  "base_letter_path": "/data/templates/letter.tex",
  "additional_info": {"linkedin": "https://linkedin.com/in/jane"}
}
```

All fields are optional strings (or dict for `additional_info`).

**Response `200`:** Same shape as `GET /api/settings/profile`.

---

#### `GET /api/settings/search`

**Description:** Retrieve the singleton search and matching settings (row with `id=1`).

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{
  "id": 1,
  "keywords": {"include": ["python", "fastapi"]},
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

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No `SearchSettings` row has been created yet |

---

#### `PUT /api/settings/search`

**Description:** Create or update (upsert) the singleton search settings. All fields are optional.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** All fields from `GET /api/settings/search` response except `id`, all optional.

Defaults applied on first creation if omitted: `keywords={"include": []}`, `remote_only=false`, `daily_limit=10`, `batch_time="08:00"`, `min_match_score=30.0`.

**Response `200`:** Same shape as `GET /api/settings/search`.

---

#### `GET /api/settings/sources`

**Description:** Return which external API sources are configured. Key values are never returned in full; only masked hints are shown.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

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

A source is considered unconfigured if its env var value is `null`, `""`, or `"placeholder"`.

---

#### `PUT /api/settings/sources`

**Description:** Placeholder route. API keys must be set in the `.env` file; they cannot be updated at runtime via the API. This endpoint accepts the request body but ignores it entirely, returning guidance text.

**Auth required:** No

**Request body:** All fields optional and ignored:

```json
{
  "adzuna_app_id": "...",
  "adzuna_app_key": "...",
  "google_api_key": "..."
}
```

**Response `200`:**

```json
{
  "message": "API keys must be set in the .env file at the project root. Edit ADZUNA_APP_ID, ADZUNA_APP_KEY, and GOOGLE_API_KEY then restart the server.",
  "env_file": ".env"
}
```

---

#### `GET /api/settings/status`

**Description:** Return setup completeness flags used by the frontend onboarding flow.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{
  "gemini_key_set": true,
  "adzuna_key_set": true,
  "tectonic_found": true,
  "base_cv_uploaded": true,
  "setup_complete": true
}
```

| Field | Logic |
|---|---|
| `gemini_key_set` | `GOOGLE_API_KEY` env var is set and not `""` or `"placeholder"` |
| `adzuna_key_set` | Both `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are set and not `""` or `"placeholder"` |
| `tectonic_found` | `bin/tectonic` exists relative to CWD, or `tectonic` is on `PATH` |
| `base_cv_uploaded` | `UserProfile.base_cv_path` points to an existing file, or any `*.tex` file exists in `{data_dir}/templates/` |
| `setup_complete` | `gemini_key_set AND adzuna_key_set AND base_cv_uploaded` (tectonic is not required) |

---

#### `GET /api/settings/sites`

**Description:** Return all known job-source sites with their current enabled state and browser session presence.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

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
  },
  {
    "name": "adzuna",
    "display_name": "Adzuna",
    "type": "api",
    "requires_login": false,
    "base_url": "https://api.adzuna.com",
    "enabled": true,
    "has_session": false
  }
]
```

`has_session` is `true` when a valid Playwright storage-state file exists for the site. Site configuration is sourced from `SITE_CONFIGS` in `backend/scraping/site_prompts.py`; the `enabled` flag is stored in the `job_sources` table and defaults to `true`.

---

#### `PUT /api/settings/sites/{site_name}`

**Description:** Enable or disable a job-source site. The change is persisted to the `job_sources` table and takes effect on the next batch run.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `site_name` | string | Site key (e.g. `"linkedin"`, `"adzuna"`, `"google_jobs"`) |

**Query params:** None

**Request body:**

```json
{"enabled": false}
```

**Response `200`:**

```json
{"name": "linkedin", "enabled": false}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | Site name is not in `SITE_CONFIGS` |

---

#### `GET /api/settings/credentials`

**Description:** Return all sites that require login, with masked email addresses and session status. Emails are Fernet-decrypted for masking (first two characters shown, rest replaced with `***@domain`).

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
[
  {
    "site_name": "linkedin",
    "display_name": "LinkedIn",
    "masked_email": "ja***@example.com",
    "has_session": true
  },
  {
    "site_name": "indeed",
    "display_name": "Indeed",
    "masked_email": null,
    "has_session": false
  }
]
```

Only sites with `requires_login: true` in `SITE_CONFIGS` are returned.

---

#### `PUT /api/settings/credentials/{site_name}`

**Description:** Encrypt and store email/password credentials for a login-required site using Fernet symmetric encryption (keyed by `CREDENTIAL_KEY` env var). An existing credential for the site is updated; a new row is created if none exists.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `site_name` | string | Site key (e.g. `"linkedin"`) |

**Query params:** None

**Request body:**

```json
{
  "email": "jane@example.com",
  "password": "s3cret"
}
```

Both fields are required strings.

**Response `200`:**

```json
{"site_name": "linkedin", "saved": true}
```

**Error responses:**

| Status | Condition |
|---|---|
| 400 | Site does not require login, or `CREDENTIAL_KEY` is not set in the environment |
| 404 | Site name is not known |

---

#### `DELETE /api/settings/credentials/{site_name}/session`

**Description:** Delete browser session state files for a site, forcing a new login on the next scrape or apply run. Deletes both the canonical `browser_profiles/{site}/state.json` path and the legacy `browser_sessions/{site}_state.json` path for backward compatibility.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `site_name` | string | Site key |

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{"cleared": true}
```

Returns `{"cleared": false}` if no session files existed for the site.

**Error responses:**

| Status | Condition |
|---|---|
| 404 | Site name is not known |

---

#### `GET /api/settings/custom-sites`

**Description:** Return all custom job-source entries (user-added lab or company career page URLs).

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:** None

**Response `200`:**

```json
[
  {
    "id": 5,
    "name": "mylab",
    "display_name": "My Lab Jobs",
    "url": "https://mylab.io/jobs",
    "enabled": true
  }
]
```

Custom sites are `JobSource` rows with `type="lab_url"`.

---

#### `POST /api/settings/custom-sites`

**Description:** Add a new custom job-source URL. A `JobSource` row with `type="lab_url"` is created.

**Auth required:** No

**Path params:** None

**Query params:** None

**Request body:**

```json
{
  "name": "mylab",
  "url": "https://mylab.io/jobs",
  "display_name": "My Lab Jobs"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Internal identifier (used as the source key) |
| `url` | string | Yes | Full URL of the careers or jobs page |
| `display_name` | string | No | Human-readable label for the UI |

**Response `200`:** Same shape as a single entry in `GET /api/settings/custom-sites`.

---

#### `DELETE /api/settings/custom-sites/{site_id}`

**Description:** Delete a custom job-source by its database ID.

**Auth required:** No

**Path params:**

| Parameter | Type | Description |
|---|---|---|
| `site_id` | integer | Database ID of the `JobSource` row |

**Query params:** None

**Request body:** None

**Response `200`:**

```json
{"deleted": 5}
```

**Error responses:**

| Status | Condition |
|---|---|
| 404 | No custom site with the given ID |

---

### Health

#### `GET /api/health`

**Description:** System health check. Returns version, database connectivity, Tectonic availability, and Gemini key presence.

**Auth required:** No

**Response `200`:**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "db": "connected",
  "tectonic": true,
  "gemini_key_set": true
}
```

When Tectonic is not found, an additional `tectonic_hint` field provides installation instructions:

```json
{
  "tectonic_hint": "Tectonic not found. Run: uv run python scripts/download_tectonic.py"
}
```

---

## WebSocket Protocol

### `GET /ws`

Upgrade an HTTP connection to a persistent WebSocket. The server assigns a UUID to each connection and registers it in the `ConnectionManager`. The connection is kept alive until the client disconnects or the server shuts down. Reconnection is handled by the client (the SvelteKit frontend reconnects every 3 seconds on drop).

**Connection flow:**

1. Client sends an HTTP `GET /ws` with `Upgrade: websocket` headers.
2. Server accepts the connection and assigns a connection UUID.
3. Client and server exchange JSON-encoded text frames as needed.
4. On disconnect, the connection UUID is removed from the manager.

All messages are JSON text frames. Both directions use `{"type": "<message_type>", ...}` as the discriminant field.

---

### Built-in Ping/Pong

| Client sends | Server replies |
|---|---|
| `{"type": "ping"}` | `{"type": "pong"}` |

---

### Server-to-Client Messages

All server-to-client messages are broadcast to all connected clients unless noted otherwise.

#### `scraping_status`

Emitted during a scraping run to report progress on a specific source.

```json
{
  "type": "scraping_status",
  "message": "Scraping LinkedIn for 'python'...",
  "source": "linkedin",
  "progress": 0.45
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"scraping_status"` |
| `message` | string | Human-readable status description |
| `source` | string | Site key being scraped (e.g. `"linkedin"`, `"adzuna"`) |
| `progress` | float | Overall batch progress in range 0.0–1.0 |

---

#### `matching_status`

Emitted after the matching step of a batch run.

```json
{
  "type": "matching_status",
  "count": 12
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"matching_status"` |
| `count` | integer | Number of jobs that passed the minimum match score threshold |

---

#### `tailoring_status`

Emitted during CV pre-generation to report progress per job.

```json
{
  "type": "tailoring_status",
  "job_id": 42,
  "progress": 0.75
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"tailoring_status"` |
| `job_id` | integer | `Job` database ID being tailored |
| `progress` | float | Progress for this specific job, 0.0–1.0 |

---

#### `apply_review`

Emitted by the applier during an `auto` mode apply, after the form has been pre-filled but before submission. The client must respond with `confirm_submit` or `cancel_apply` within 30 minutes, or the apply is automatically cancelled.

```json
{
  "type": "apply_review",
  "job_id": 7,
  "filled_fields": {
    "#first-name": "Jane",
    "#email": "jane@example.com",
    "#cover-letter": "Dear Hiring Manager..."
  },
  "screenshot_base64": "iVBORw0KGgo..."
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"apply_review"` |
| `job_id` | integer | `JobMatch` ID for which the review is requested |
| `filled_fields` | object | Map of CSS selector → filled value for all fields that were successfully filled |
| `screenshot_base64` | string or null | Base64-encoded PNG screenshot of the filled form, or `null` if screenshot failed |

---

#### `apply_result`

Emitted after an apply attempt completes (whether successful, cancelled, or failed).

```json
{
  "type": "apply_result",
  "job_id": 7,
  "status": "applied",
  "method": "auto"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"apply_result"` |
| `job_id` | integer | `JobMatch` ID |
| `status` | string | `"applied"`, `"assisted"`, `"manual"`, or `"cancelled"` |
| `method` | string | `"auto"`, `"assisted"`, or `"manual"` |

---

#### `login_required`

Emitted by `BrowserSessionManager` when a job board requires manual login and no stored session exists. The server opens a visible browser window and waits for the user to log in and signal completion.

```json
{
  "type": "login_required",
  "site": "linkedin",
  "browser_window_title": "Please log in to LinkedIn in the browser window that just opened"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"login_required"` |
| `site` | string | Site key requiring login |
| `browser_window_title` | string | Instruction message for the user |

The server waits up to 600 seconds (10 minutes) for a `login_done` or `login_cancel` client message before timing out.

---

#### `login_confirmed`

Emitted by `BrowserSessionManager` after a successful login is confirmed and the session has been saved.

```json
{
  "type": "login_confirmed",
  "site": "linkedin"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"login_confirmed"` |
| `site` | string | Site key for which login succeeded |

---

#### `error`

Emitted when a backend operation fails in a way that the user should be notified about.

```json
{
  "type": "error",
  "message": "Gemini rate limit reached — please try again shortly",
  "code": "rate_limit"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"error"` |
| `message` | string | Human-readable error description |
| `code` | string | Machine-readable error code (e.g. `"rate_limit"`, `"latex_compile_error"`, `"gemini_json_error"`, `"internal_error"`) |

---

#### `status` (broadcast helper)

A generic progress broadcast emitted by `broadcast_status()` used throughout the scraping and scheduling modules. Note: this message type does not correspond to any typed model in `ws_models.py` and uses a different shape from the typed messages above.

```json
{
  "type": "status",
  "message": "12 applications ready for review",
  "progress": 1.0
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"status"` |
| `message` | string | Human-readable status text |
| `progress` | float | Overall progress in range 0.0–1.0 |

---

### Client-to-Server Messages

Client messages are dispatched by raw `type` string lookup to registered handlers. Unrecognised message types are silently ignored.

#### `confirm_submit`

Sent by the user to approve submission of a pre-filled application form. Must be sent while the server is paused at the `apply_review` gate for the specified job.

```json
{
  "type": "confirm_submit",
  "job_id": 7
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"confirm_submit"` |
| `job_id` | integer | `JobMatch` ID to confirm |

**Effect:** Sets the `confirm_event` asyncio.Event in `ApplicationEngine`, unblocking the apply strategy and proceeding to click the submit button.

---

#### `cancel_apply`

Sent by the user to abort a pending apply review. The apply attempt will be recorded as `status="cancelled"`.

```json
{
  "type": "cancel_apply",
  "job_id": 7
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"cancel_apply"` |
| `job_id` | integer | `JobMatch` ID to cancel |

**Effect:** Sets the `cancel_event` asyncio.Event in `ApplicationEngine`, unblocking the strategy and returning `status="cancelled"`.

---

#### `login_done`

Sent by the user after they have completed manual login in the browser window opened by `BrowserSessionManager`. The session is then saved and scraping continues.

```json
{
  "type": "login_done",
  "site": "linkedin"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"login_done"` |
| `site` | string | Site key for which login was completed |

**Effect:** Calls `BrowserSessionManager.confirm_login(site)`, which sets the internal asyncio.Event to unblock `get_or_create_session`.

---

#### `login_cancel`

Sent by the user to abort the manual login flow for a site.

```json
{
  "type": "login_cancel",
  "site": "linkedin"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"login_cancel"` |
| `site` | string | Site key for which login is being cancelled |

**Effect:** Calls `BrowserSessionManager.cancel_login(site)`, which marks the site as cancelled and raises `RuntimeError` in the waiting `get_or_create_session` call, causing the scraper to skip this site.

---

#### `ping`

Standard liveness check. No handler registration needed; handled directly in the WebSocket receive loop.

```json
{"type": "ping"}
```

**Effect:** Server replies immediately with `{"type": "pong"}`.

---

### Global Exception Handlers

The following HTTP error responses can be returned by any endpoint when the corresponding condition occurs:

| Exception | HTTP Status | Response body |
|---|---|---|
| `LaTeXCompilationError` | 422 | `{"error": "<message>", "code": "latex_compile_error"}` |
| `GeminiJSONError` | 500 | `{"error": "LLM response validation failed", "code": "gemini_json_error"}` |
| `GeminiRateLimitError` | 429 | `{"error": "LLM rate limit reached — please try again shortly", "code": "rate_limit"}` |
| Any uncaught `Exception` | 500 | `{"error": "Internal server error", "code": "internal_error"}` |
