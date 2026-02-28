# JobPilot — API Overview

Base URL: `http://localhost:8000`

All endpoints return JSON. Authentication is not implemented (single-user local tool).

---

## Health

### `GET /api/health`
Returns server status and dependency availability.

**Response:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "db": "connected",
  "tectonic": false,
  "gemini_key_set": false,
  "tectonic_hint": "Tectonic not found. Run: uv run python scripts/download_tectonic.py"
}
```

---

## Jobs

### `GET /api/jobs`
List all scraped jobs.

**Query params:**
| Param | Type | Default | Description |
|---|---|---|---|
| skip | int | 0 | Pagination offset |
| limit | int | 50 | Max results (1–200) |
| min_score | float | null | Filter by minimum match score |

**Response:**
```json
{
  "jobs": [
    {
      "id": 1,
      "title": "Senior Python Developer",
      "company": "Acme Corp",
      "location": "London, UK",
      "salary_text": "£60,000 - £80,000",
      "salary_min": 60000,
      "salary_max": 80000,
      "description": "...",
      "url": "https://adzuna.com/jobs/1234",
      "apply_url": "https://acme.com/apply/1234",
      "posted_at": "2026-02-27T10:00:00",
      "scraped_at": "2026-02-28T08:05:00",
      "score": 72.5
    }
  ],
  "total": 1
}
```

### `GET /api/jobs/{job_id}`
Get a single job by ID. Returns 404 if not found.

### `GET /api/jobs/{job_id}/score`
Get the latest match score for a job.

**Response:**
```json
{
  "job_id": 1,
  "score": 72.5,
  "keyword_hits": ["python", "fastapi", "async"]
}
```

### `POST /api/jobs/search`
Trigger a manual Adzuna search and store results.

**Request body:**
```json
{
  "keywords": ["python", "fastapi"],
  "location": "London",
  "country": "gb",
  "max_results": 20
}
```

**Response:**
```json
{
  "stored": 5,
  "jobs": [{"title": "...", "company": "..."}]
}
```

---

## Queue (Morning Matches)

### `GET /api/queue`
Return today's queue — job_matches with status `new`, sorted by score descending.

**Query params:**
| Param | Type | Default | Description |
|---|---|---|---|
| batch_date | date | today | YYYY-MM-DD to filter by |

**Response:**
```json
{
  "matches": [
    {
      "match_id": 1,
      "job_id": 1,
      "title": "Senior Python Developer",
      "company": "Acme Corp",
      "location": "London, UK",
      "score": 72.5,
      "status": "new",
      "batch_date": "2026-02-28",
      "matched_at": "2026-02-28T08:06:00",
      "url": "https://adzuna.com/jobs/1234"
    }
  ],
  "total": 1
}
```

### `POST /api/queue/refresh`
Trigger a new morning batch run immediately (runs in background).

**Response:**
```json
{
  "status": "started",
  "message": "Morning batch triggered in background"
}
```

### `PATCH /api/queue/{match_id}/skip`
Mark a match as skipped.

**Response:**
```json
{"match_id": 1, "status": "skipped"}
```

### `PATCH /api/queue/{match_id}/status`
Update match status.

**Request body:**
```json
{"status": "applying"}
```
Allowed values: `new`, `skipped`, `applying`, `applied`, `rejected`

---

## Applications

### `GET /api/applications`
List all applications.

**Query params:** `skip`, `limit`, `status` (filter by status)

**Response:** Array of application objects with `id`, `job_id`, `document_id`, `status`, `apply_method`, `created_at`, `updated_at`.

### `POST /api/applications`
Create a new application (triggers ApplicationEngine).

**Request body:**
```json
{
  "job_id": 1,
  "document_id": 1,
  "apply_method": "auto"
}
```

### `GET /api/applications/{application_id}`
Get a single application with its events.

### `PATCH /api/applications/{application_id}/status`
Update application status (e.g., after interview/offer/rejection).

**Request body:**
```json
{"status": "interview", "notes": "First round scheduled for March 5"}
```
Allowed: `pending`, `submitted`, `interview`, `offer`, `rejected`, `withdrawn`

---

## Documents (Tailored CVs)

### `GET /api/documents`
List all tailored documents.

**Query params:** `job_id` (filter by job), `doc_type` (cv/letter)

### `GET /api/documents/{document_id}`
Get a single document record.

### `POST /api/documents/tailor`
Trigger CV tailoring for a job. Creates a `TailoredDocument` record.

**Request body:**
```json
{
  "job_id": 1,
  "doc_type": "cv"
}
```

### `GET /api/documents/{document_id}/download`
Download the compiled PDF. Returns binary PDF file.

### `DELETE /api/documents/{document_id}`
Delete a document record and its files.

---

## Settings

### `GET /api/settings/profile`
Get user profile (singleton id=1). Returns 404 if not set up yet.

### `PUT /api/settings/profile`
Create or update user profile (upsert).

**Request body:**
```json
{
  "full_name": "Jane Smith",
  "email": "jane@example.com",
  "phone": "+44 7700 900000",
  "location": "London, UK",
  "base_cv_path": "data/cvs/base_cv.tex",
  "additional_info": {"years_experience": 5}
}
```

### `GET /api/settings/search`
Get search settings (singleton id=1). Returns 404 if not set up yet.

### `PUT /api/settings/search`
Create or update search settings (upsert).

**Request body (all fields optional):**
```json
{
  "keywords": {"include": ["python", "fastapi"], "must": ["async"]},
  "excluded_keywords": {"terms": ["junior", "intern"]},
  "locations": {"cities": ["London", "Remote"]},
  "salary_min": 50000,
  "remote_only": false,
  "daily_limit": 10,
  "batch_time": "08:00",
  "min_match_score": 30.0
}
```

### `GET /api/settings/sources`
Return which API sources are configured (keys masked).

**Response:**
```json
{
  "adzuna": {"configured": true, "app_id_hint": "abc1****"},
  "gemini": {"configured": false}
}
```

### `PUT /api/settings/sources`
Returns guidance: API keys must be set in `.env` file, not via this endpoint.

### `GET /api/settings/status`
Return setup completeness flags.

**Response:**
```json
{
  "gemini_key_set": true,
  "adzuna_key_set": true,
  "tectonic_found": true,
  "base_cv_uploaded": true,
  "setup_complete": true
}
```

---

## Analytics

### `GET /api/analytics/summary`
Return high-level application statistics.

**Response:**
```json
{
  "total_apps": 42,
  "apps_this_week": 8,
  "response_rate": 19.0,
  "avg_match_score": 64.3
}
```

### `GET /api/analytics/trends`
Applications per day for the last N days.

**Query params:**
| Param | Type | Default | Range |
|---|---|---|---|
| days | int | 30 | 1–365 |

**Response:**
```json
{
  "trends": [
    {"date": "2026-02-28", "count": 3},
    {"date": "2026-02-27", "count": 5}
  ],
  "days": 30
}
```

---

## WebSocket

### `WS /ws`
Persistent connection for real-time UI updates.

See [Architecture — WebSocket Protocol](architecture.md#websocket-protocol) for full message reference.

**Quick example:**
```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.send(JSON.stringify({ type: 'ping' }));
// Server responds: { "type": "pong" }
```

---

## Error Responses

All errors return JSON with `error` and `code` fields:

| HTTP | code | When |
|---|---|---|
| 404 | `not_found` | Resource not found |
| 422 | `validation_error` | Invalid request body |
| 422 | `latex_compile_error` | LaTeX compilation failed |
| 429 | `rate_limit` | Gemini rate limit hit |
| 500 | `gemini_json_error` | LLM response validation failed |
| 500 | `internal_error` | Unhandled server error |
| 502 | `upstream_error` | Adzuna API call failed |

```json
{
  "error": "LLM rate limit reached — please try again shortly",
  "code": "rate_limit"
}
```
