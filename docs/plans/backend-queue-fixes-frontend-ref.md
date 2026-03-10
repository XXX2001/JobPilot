# Backend Queue & Matching Fixes — Frontend Reference

## Date: 2026-03-10

---

## Backend Changes Made

### 1. Job Deduplication in Morning Batch (CRITICAL FIX)

**File:** `backend/scheduler/morning_batch.py` → `_store_matches()`

**Before:** Every batch run created a **new** `Job` row for every matched job, even
if that job already existed in the DB. This caused:
- Duplicate Job rows with different IDs for the same position
- `match.job_id` pointing to different Job rows across batches
- ID mismatch: clicking a card could fetch the wrong Job record

**After:** Jobs are now deduplicated by `dedup_hash = md5(company|title|location)`.
If a Job already exists, the existing row is reused. JobMatch records are also
deduplicated per `(job_id, batch_date)` — same job won't appear twice in one day's
queue.

### 2. New Endpoint: GET /api/queue/{match_id}

**File:** `backend/api/queue.py`

Returns a single `QueueMatchOut` with the full nested `job` object. This allows
the job detail page to fetch everything it needs (match score, status, job data)
with a single call using the match ID.

### 3. Queue API Null Safety

**File:** `backend/api/queue.py`

- `apply_url` defaults to `""` instead of being required (prevents crash on null)
- Response now falls back: `job.apply_url → job.url → ""`
- `apply_method` defaults to `""` instead of being required

### 4. Prompt Template Fix (previous session)

**File:** `backend/scraping/site_prompts.py`

- `{jobId}` in LinkedIn template was causing `.format()` to raise `KeyError`
- All template variables (`{keywords}`, `{location}`, `{max_jobs}`) were being
  returned as literal strings instead of interpolated values
- Fixed by escaping to `{{jobId}}`

---

## Frontend Changes Made

### Job Detail Page Fix — `routes/jobs/[id]/+page.svelte`

**Before:** The page called `GET /api/jobs/${matchId}` — sending a **match ID** to
an endpoint that expects a **job ID**. This returned the wrong job or 404.

**After:** The page calls `GET /api/queue/${matchId}` which returns the full
`QueueMatch` object with nested `job`. The page now derives `job` and `score`
from the match data, so all fields are correct.

Key changes:
- `job = await apiFetch('/api/jobs/${matchId}')` → `matchData = await apiFetch('/api/queue/${matchId}')`
- `job` is now a derived value: `$derived(matchData?.job ?? null)`
- `score` is now: `$derived(matchData?.score ?? 0)` (was `job.score`)
- Removed unused `scraped_at` from the Job interface

### Navigation Links (unchanged — correct as-is)

`JobCard.svelte` uses `href="/jobs/{match.id}"` which is correct since:
- The URL param is a **match ID**
- The detail page now properly uses it as a match ID
- Document/application APIs already expect match IDs

---

## ID Usage Cheat Sheet

| ID | Where it comes from | Use for |
|----|-------------------|---------|
| `match.id` | `QueueMatchOut.id` | URL param, queue PATCH endpoints, document/application APIs |
| `match.job_id` | `QueueMatchOut.job_id` | Not needed in frontend (job data is nested) |
| `match.job.id` | `QueueMatchOut.job.id` | Not needed in frontend (for reference only) |
| `match.job.apply_url` | Nested job | Opening the actual job posting externally |
| `match.job.url` | Nested job | "View Listing" link |

---

## Queue API Endpoints Reference

| Method | Endpoint | ID type | Description |
|--------|----------|---------|-------------|
| GET | `/api/queue` | — | All pending matches (status=new) |
| GET | `/api/queue/{match_id}` | match ID | Single match with nested job |
| POST | `/api/queue/refresh` | — | Trigger morning batch |
| PATCH | `/api/queue/{match_id}/skip` | match ID | Mark match as skipped |
| PATCH | `/api/queue/{match_id}/status` | match ID | Update match status |
| GET | `/api/jobs/{job_id}` | job ID | Single job (standalone, no match context) |
| POST | `/api/applications/{match_id}/apply` | match ID | Trigger apply flow |
| GET | `/api/documents/{match_id}/diff` | match ID | CV diff for a match |
| GET | `/api/documents/{match_id}/cv/pdf` | match ID | Tailored CV PDF |

---

## Matching/Scoring Logic Summary

Scoring is **100% algorithmic** (no LLM), weighted out of 100 points:

| Factor | Weight | Method |
|--------|--------|--------|
| Keyword match | 40% | Substring match of each keyword in job description |
| Location match | 20% | Substring match against user's location list |
| Experience match | 15% | Regex extraction of "X years" patterns from description |
| Salary match | 10% | Compare job salary range against user's minimum |
| Recency | 10% | Linear decay: 1.0 today → 0.0 at 30 days old |
| Exclusion | instant 0 | Blacklisted keywords in title/description, or blacklisted company |

**Threshold:** Jobs scoring below `min_match_score` (default 30.0) are filtered out.
