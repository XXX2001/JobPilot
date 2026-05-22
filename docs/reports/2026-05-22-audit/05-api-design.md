# API Design Audit — JobPilot HTTP/WS Surface

Scope: `backend/api/*.py`, `backend/main.py`, `backend/models/schemas.py`, `backend/api/ws_models.py`.
Date: 2026-05-22. Auditor: design review only — security / CORS / EH-05 / RG-01 are covered in the
`2026-05-22-standards/` backlog and are deliberately not re-litigated here.

---

## TL;DR — biggest design issues

1. **No API versioning, no consistent error envelope.** Routes live at `/api/...` with no `/v1`
   namespace; FastAPI's default `{"detail": "..."}` envelope is used in 21 places, but the global
   exception handlers in `backend/main.py:288–340` emit `{"error": "...", "code": "..."}`, and many
   handlers (e.g. `backend/api/queue.py:177`, `backend/api/jobs.py:246`) return ad-hoc dicts with
   none of those shapes. The OpenAPI schema therefore cannot describe an error response type and
   the frontend has to special-case both shapes.
2. **Half the success responses are untyped raw dicts.** 18 of the 34 routes have no
   `response_model=`. Every PATCH/PUT/POST mutating endpoint in `queue.py`, `settings.py`,
   `documents.py`, and the apply endpoint returns `dict[str, Any]` — generated clients see
   `application/json` with no schema. Sample: `backend/api/queue.py:218–267` (skip + status update
   + enrich), `backend/api/settings.py:551–700` (entire credentials/sites surface).
3. **Status codes are sloppy.** `POST .../{match_id}/apply` is explicitly forced to `200`
   (`backend/api/applications.py:433`) even though it creates an Application row; `POST /api/jobs/search`
   returns 200 for a write that may insert dozens of rows (`backend/api/jobs.py:177`); `POST
   /api/queue/refresh` returns 200 instead of `202 Accepted` for a fire-and-forget background task
   (`backend/api/queue.py:149`); DELETE endpoints (`/credentials/.../session`, `/custom-sites/{id}`)
   return `200 + {"deleted": ...}` instead of `204 No Content`.
4. **No idempotency, no bulk endpoints, and `apply` is dangerously re-runnable.** `POST
   /api/applications/{match_id}/apply` will happily run twice in parallel for the same match — there
   is no `Idempotency-Key` header, no precondition check on an existing in-flight `Application` row,
   and no DB-level uniqueness on `(job_match_id, status="pending")`. The frontend that needs to
   apply to 50 jobs makes 50 HTTP calls; there is no `POST /api/applications/bulk-apply` or
   `PATCH /api/queue/matches` for batch status updates.
5. **WebSocket is unauthenticated, untyped, and one-way-typed.** `/ws` accepts any connection with
   no token (`backend/api/ws.py:147`); inbound messages are parsed as `dict` and dispatched by
   raw `type` string lookup (`backend/api/ws.py:181`) — the `ClientMessage` discriminated union in
   `ws_models.py:177` is **defined but never used**. `broadcast_status` and `broadcast_job_assessment`
   send plain dicts (`backend/api/ws.py:198, 218`) that bypass `WSMessage` too, so the typed schema
   is decorative. No heartbeat from the server, no reconnect-on-token, no per-client subscription —
   every message goes to every connected browser.

---

## Route inventory

Method on left, full path. `Auth` is uniformly **none** (no `Depends(get_current_user)` anywhere —
see standards `ST-01-cors-lockdown.md` and the missing-auth design implication discussed in
Finding API-11). Files are abbreviated; line numbers are the `@router.*` decorator.

| Method    | Path                                              | response_model            | Status      | Notes (file:line) |
|-----------|---------------------------------------------------|---------------------------|-------------|-------------------|
| GET       | `/api/health`                                     | — (raw dict)              | 200         | `main.py:248` — leaks env config bools |
| GET       | `/api/jobs`                                       | `JobListOut`              | 200         | `jobs.py:78` — paginated, but N+1 score query |
| GET       | `/api/jobs/{job_id}`                              | `JobOut`                  | 200/404     | `jobs.py:136` |
| POST      | `/api/jobs/search`                                | — (raw dict)              | 200/502     | `jobs.py:177` — should be 202 + status endpoint |
| GET       | `/api/jobs/{job_id}/score`                        | — (raw dict)              | 200/404     | `jobs.py:249` |
| GET       | `/api/queue`                                      | `QueueOut`                | 200         | `queue.py:87` — **unpaginated, unbounded** |
| GET       | `/api/queue/status`                               | — (raw dict)              | 200         | `queue.py:133` |
| POST      | `/api/queue/refresh`                              | — (raw dict)              | 200/409/503 | `queue.py:149` — should be 202; also see ST-08 |
| GET       | `/api/queue/{match_id}`                           | `QueueMatchOut`           | 200/404     | `queue.py:180` |
| PATCH     | `/api/queue/{match_id}/skip`                      | — (raw dict)              | 200/404     | `queue.py:218` — overlaps with `/status` |
| PATCH     | `/api/queue/{match_id}/status`                    | — (raw dict)              | 200/404/422 | `queue.py:244` |
| POST      | `/api/queue/{match_id}/enrich-description`        | — (raw dict)              | 200/404/422/502/503 | `queue.py:270` — sync block on LLM, should be 202 |
| POST      | `/api/applications`                               | `ApplicationOut`          | **201**     | `applications.py:136` — correct |
| GET       | `/api/applications`                               | `ApplicationListOut`      | 200         | `applications.py:158` — paginated |
| GET       | `/api/applications/{application_id}`              | `ApplicationOut`          | 200/404     | `applications.py:226` |
| PATCH     | `/api/applications/{application_id}`              | `ApplicationOut`          | 200/404     | `applications.py:267` — accepts any string for `status` |
| POST      | `/api/applications/{application_id}/events`       | `ApplicationEventOut`     | **201**     | `applications.py:327` — correct |
| POST      | `/api/applications/{match_id}/apply`              | — (raw dict)              | **200**     | `applications.py:433` — forced 200, no idempotency |
| GET       | `/api/documents`                                  | `list[DocumentOut]`       | 200         | `documents.py:77` — **unpaginated** |
| POST      | `/api/documents/validate-template`                | — (raw dict)              | 200         | `documents.py:90` — verb/path: should be GET-with-query or different noun |
| GET       | `/api/documents/{match_id}/cv/pdf`                | — (FileResponse)          | 200/404     | `documents.py:105` |
| GET       | `/api/documents/{match_id}/letter/pdf`            | — (FileResponse)          | 200/404     | `documents.py:149` |
| GET       | `/api/documents/{match_id}/diff`                  | — (raw dict)              | 200/404     | `documents.py:193` |
| POST      | `/api/documents/{match_id}/regenerate`            | — (raw dict)              | 200/404     | `documents.py:223` — see RG-01 (dead) + status should be 202 |
| GET       | `/api/settings/profile`                           | `ProfileOut`              | 200         | `settings.py:162` — returns synthetic `id=0` instead of 404 |
| PUT       | `/api/settings/profile`                           | `ProfileOut`              | 200         | `settings.py:193` — upsert; first call is a create, should be 201 |
| GET       | `/api/settings/search`                            | `SearchSettingsOut`       | 200/404     | `settings.py:244` |
| PUT       | `/api/settings/search`                            | `SearchSettingsOut`       | 200         | `settings.py:264` — upsert, no 201 on create |
| GET       | `/api/settings/sources`                           | — (raw dict)              | 200         | `settings.py:342` |
| PUT       | `/api/settings/sources`                           | — (raw dict)              | 200         | `settings.py:369` — no-op endpoint, returns guidance |
| GET       | `/api/settings/status`                            | `SetupStatus`             | 200         | `settings.py:388` |
| GET       | `/api/settings/sites`                             | `list[SiteOut]`           | 200         | `settings.py:520` |
| PUT       | `/api/settings/sites/{site_name}`                 | — (raw dict)              | 200/404     | `settings.py:551` — flipping a single bool, should be PATCH |
| GET       | `/api/settings/credentials`                       | `list[CredentialOut]`     | 200         | `settings.py:587` |
| PUT       | `/api/settings/credentials/{site_name}`           | — (raw dict)              | 200/400/404 | `settings.py:629` |
| DELETE    | `/api/settings/credentials/{site_name}/session`   | — (raw dict)              | 200/404     | `settings.py:677` — should be 204 |
| GET       | `/api/settings/custom-sites`                      | `list[CustomSiteOut]`     | 200         | `settings.py:706` |
| POST      | `/api/settings/custom-sites`                      | `CustomSiteOut`           | **200**     | `settings.py:732` — should be 201 |
| DELETE    | `/api/settings/custom-sites/{site_id}`            | — (raw dict)              | 200/404     | `settings.py:759` — should be 204 |
| GET       | `/api/analytics/summary`                          | `AnalyticsSummary`        | 200         | `analytics.py:70` |
| GET       | `/api/analytics/trends`                           | `AnalyticsTrends`         | 200         | `analytics.py:118` |
| WEBSOCKET | `/ws`                                             | — (untyped)               | n/a         | `ws.py:147` — no auth, no heartbeat |

Total: 41 HTTP endpoints + 1 WS endpoint. 23 use `response_model=`, 18 do not.

---

## Findings

| ID      | Title                                                       | Severity | Routes affected |
|---------|-------------------------------------------------------------|----------|-----------------|
| API-01  | No API versioning prefix (`/v1`)                            | medium   | all 41 |
| API-02  | Inconsistent error response shape (3 distinct envelopes)    | high     | all error paths |
| API-03  | Raw-dict success responses bypass OpenAPI                   | high     | 18 routes (see table) |
| API-04  | Status codes wrong on writes / deletes / async kicks        | high     | apply, refresh, regenerate, search, enrich, 2× DELETE, 2× POST creates |
| API-05  | `POST /apply` is non-idempotent and racy                    | high     | `applications.py:433` |
| API-06  | No bulk endpoints — frontend must loop N times              | medium   | apply, status, regenerate, enrich, skip |
| API-07  | List endpoints leak unbounded `.all()` (no pagination cap)  | high     | `/api/queue`, `/api/documents`, `/api/settings/sites`, `/api/settings/credentials`, `/api/settings/custom-sites` |
| API-08  | List filtering/sorting is ad-hoc and inconsistent           | medium   | `/api/jobs`, `/api/applications`, `/api/queue` |
| API-09  | Synchronous endpoints block on slow work (LLM, scraping)    | high     | `enrich-description`, `apply` (auto), `search`, `refresh` |
| API-10  | Body schemas accept `dict` and unrestricted strings         | medium   | settings (keywords/locations/job_types `dict`), update_application (status `str`) |
| API-11  | Every route is anonymous — design assumes auth doesn't exist | high     | all 41 + `/ws` |
| API-12  | WS: no auth, no typed client messages, no heartbeat         | high     | `/ws` |
| API-13  | OpenAPI metadata is bare — no summaries/examples/error specs | medium   | all routes |
| API-14  | Verb/path semantics drift                                   | medium   | `POST /validate-template`, `PUT /sites/{name}`, `POST /jobs/search`, upsert PUTs |
| API-15  | Duplicate response schemas (two `JobOut` definitions)       | low      | `jobs.py:32` vs `queue.py:36` |
| API-16  | Singleton routes return synthetic empty rows instead of 404 | low      | `/api/settings/profile` |
| API-17  | `/api/health` mixes liveness, readiness, and config         | low      | `main.py:248` |

---

## Per-finding detail

### API-01 — No API versioning

Every router uses `prefix="/api/<noun>"` directly (`backend/api/jobs.py:29`, `queue.py:30`,
`applications.py:34`, …). There is no `/api/v1/`. A breaking change to `ApplicationOut`
(adding/removing a field, changing `status` enum values) forces every client to update in lockstep.

**Suggest:** mount under `/api/v1` now while there is exactly one client. Either:

- Per-router: `APIRouter(prefix="/api/v1/jobs", tags=["jobs"])`.
- Or wrap all includes in a parent router: `v1 = APIRouter(prefix="/api/v1"); v1.include_router(jobs.router); app.include_router(v1)`.

Keep `/api/health` and `/ws` outside the versioned namespace deliberately.

### API-02 — Three distinct error envelopes

Concrete examples in the same response:

- FastAPI default (used by every `raise HTTPException(detail=...)`):
  `{"detail": "Job 42 not found"}` — emitted by 21 raise sites.
- Global handlers in `main.py:288–324`: `{"error": "...", "code": "..."}`.
- Generic handler `main.py:327`: `{"error": "Internal server error", "code": "internal_error"}`.

The frontend therefore has to check both `body.detail` *and* `body.error`. The OpenAPI schema
documents neither shape.

**Suggest:** define a single `ErrorResponse(BaseModel)` with `code: str`, `message: str`,
`details: dict | None`. Register a `RequestValidationError` handler and rewrite `HTTPException`
into it via a single exception handler so `detail` is mapped to `message`. Set
`responses={400: {"model": ErrorResponse}, 404: ...}` on every route via a router-level default.

### API-03 — 18 routes return untyped raw dicts

These have no `response_model=` and return `dict` literals. The generated TypeScript client will see
them as `any`:

- `backend/api/jobs.py:177` `POST /api/jobs/search` → `{"stored": int, "jobs": [...]}`.
- `backend/api/jobs.py:249` `GET /api/jobs/{job_id}/score` → `{"job_id", "score", "keyword_hits"}`.
- `backend/api/queue.py:133` `GET /api/queue/status`.
- `backend/api/queue.py:149` `POST /api/queue/refresh`.
- `backend/api/queue.py:218` `PATCH /api/queue/{match_id}/skip`.
- `backend/api/queue.py:244` `PATCH /api/queue/{match_id}/status`.
- `backend/api/queue.py:270` `POST /api/queue/{match_id}/enrich-description`.
- `backend/api/applications.py:433` `POST /api/applications/{match_id}/apply` — returns `result.model_dump()` (an `ApplyResult` from `backend.applier.engine`) but the route signature doesn't declare it, so OpenAPI sees nothing.
- `backend/api/documents.py:90, 193, 223` — validate-template, diff, regenerate.
- `backend/api/settings.py:342, 369, 551, 629, 677, 759` — sources GET/PUT, sites PUT, credentials PUT/DELETE, custom-sites DELETE.

**Suggest:** define small `Out` models — `JobScoreOut`, `BatchStatusOut`, `BatchRefreshOut`,
`MatchStatusUpdateOut`, `EnrichResultOut`, `RegenerateOut`, `DeletedOut(deleted: int)`,
`SitesUpdateOut`, `SourcesOut`, etc. The apply endpoint should import and re-export
`ApplicationEngine`'s `ApplyResult` (or a thin API mirror) as the `response_model`.

### API-04 — Status codes

Concrete violations:

| Route                                                     | Now | Should be | Why |
|-----------------------------------------------------------|-----|-----------|-----|
| `POST /api/applications/{match_id}/apply`                 | 200 (forced) | 201 (sync) or 202 (auto/assisted background) | Creates an Application row |
| `POST /api/applications/{application_id}/events`          | 201 | 201 | already correct |
| `POST /api/applications`                                  | 201 | 201 | already correct |
| `POST /api/jobs/search`                                   | 200 | 202 + Location to a search-job status endpoint | runs Adzuna and inserts rows in-band |
| `POST /api/queue/refresh`                                 | 200 | 202 + `Location: /api/queue/status` | fire-and-forget background task |
| `POST /api/queue/{match_id}/enrich-description`           | 200 | 202 (or sync 200) | currently blocks on Gemini for tens of seconds |
| `POST /api/documents/{match_id}/regenerate`               | 200 ("queued") | 202 | claims to be queued (it isn't — see RG-01) but uses 200 |
| `POST /api/settings/custom-sites`                         | 200 | 201 | creates a JobSource row |
| `PUT  /api/settings/profile` (first call)                 | 200 | 201 on create, 200 on update | upsert — emit `Location` header on create |
| `PUT  /api/settings/search` (first call)                  | 200 | 201 on create | same |
| `DELETE /api/settings/credentials/{site_name}/session`    | 200 + body | 204 No Content | nothing in the body the client needs |
| `DELETE /api/settings/custom-sites/{site_id}`             | 200 + `{"deleted": id}` | 204 No Content | id is already in the URL |
| `PATCH /api/queue/{match_id}/skip`                        | 200 | 204 (no body) or 200 with full `QueueMatchOut` | currently returns a 2-key echo |

### API-05 — `apply` is non-idempotent

`backend/api/applications.py:433` `POST /api/applications/{match_id}/apply`:

- Two concurrent calls for the same `match_id` produce two `Application` rows (the engine creates
  one each time).
- No `Idempotency-Key` header.
- No precondition: there is no check `SELECT 1 FROM applications WHERE job_match_id = :id AND
  status IN ('pending','applying')`.
- The match's own status (`new`/`applying`/`applied`) is not advisory-locked — the engine
  updates `JobMatch.status = 'applying'` separately from the Application creation.

**Suggest:**

- Accept an `Idempotency-Key` header (24h dedupe table, key → response).
- On entry, atomic `SELECT ... FOR UPDATE` on `JobMatch` (or `BEGIN IMMEDIATE` on SQLite); if status
  is already `applying`/`applied`, return `409 Conflict` with the existing `application_id`.
- Add a partial unique index on `applications(job_match_id) WHERE status = 'pending'`.

### API-06 — No bulk endpoints

The frontend "apply to top 10 matches" workflow has to make 10 sequential HTTP calls because
`apply` is per-match. The same is true for "skip these 5 jobs", "regenerate documents for these 3
matches", and "update statuses for a column drag". Each call carries the full `ApplyRequest`
payload separately.

**Suggest:** add `POST /api/applications/bulk-apply { match_ids: int[], method: ... }` returning
`202 + { batch_id }` and `GET /api/applications/bulk-apply/{batch_id}` for progress. Add `PATCH
/api/queue/matches { ids: int[], status: ... }` for batch status updates. Add `POST
/api/documents/regenerate { match_ids: int[], force: bool }` to replace the per-match loop.

### API-07 — Unbounded list endpoints

These `.all()` with no LIMIT:

- `backend/api/queue.py:94` — `GET /api/queue` returns every match with `status='new'` and joins
  every `Job`. After a few morning batches this is hundreds of rows × 14 columns of JSON.
- `backend/api/documents.py:84` — `GET /api/documents` selects every `TailoredDocument` ever
  generated.
- `backend/api/settings.py:529, 599, 715` — sites/credentials/custom-sites all `.all()`; tolerable
  today only because `SITE_CONFIGS` is small (~10 entries).
- `analytics.py:135` — `GET /api/analytics/trends` selects every `Application.created_at` in the
  window with no cap; on a year-long window with high volume this is unbounded.

`/api/jobs` (jobs.py:78) and `/api/applications` (applications.py:158) **do** paginate (`limit`
capped at 200) — that's the model to copy.

**Suggest:** cap `/queue` at `limit=200`; paginate `/documents` and `/analytics/trends`.

### API-08 — List filtering / sorting inconsistency

- `/api/jobs`: filters by `min_score` only; sorted hard-coded `scraped_at DESC`. The score filter
  is applied **after** pagination (`jobs.py:108`) — `skip=0&limit=50&min_score=80` may return 3
  rows instead of 50.
- `/api/applications`: filters by `status` exact-match only; no date range; sorted hard-coded
  `created_at DESC`.
- `/api/queue`: no filters at all; sorted hard-coded `batch_date DESC, score DESC`.
- Param naming: jobs/applications use `skip/limit`; analytics uses `days`; queue uses nothing.

**Suggest:** standardize on `?limit=&cursor=` (cursor-based: opaque base64 of `(sort_key, id)`),
`?sort=field[,-field]`, `?filter[field]=value`. At minimum, harmonise pagination param names and
apply filters in the SQL layer not Python post-filter.

### API-09 — Sync endpoints block on slow work

These execute slow work inside the HTTP request:

- `POST /api/queue/{match_id}/enrich-description` (`queue.py:270–332`) — `fetcher.fetch_page` does
  a real HTTP fetch + `gemini.generate_text` on 20 KB of content. Easily 10–30 s. Blocks the
  request worker.
- `POST /api/jobs/search` (`jobs.py:177`) — calls Adzuna + dedup + bulk insert; can be 5+ seconds.
- `POST /api/applications/{match_id}/apply` with `method="auto"` — runs the whole Playwright
  apply engine inside the request (see `applier/engine.py`).
- `POST /api/queue/refresh` (`queue.py:149`) — this one **does** the right thing
  (`asyncio.create_task` and return immediately), but uses 200 instead of 202 and has no status
  resource for the client to poll the launched task (only the global `runner.running` flag).

**Suggest:** for each long-running POST, return `202 Accepted` with `Location:
/api/<resource>/<task_id>` and persist a `Task` row with `(id, kind, state, started_at,
finished_at, result_json)`. Replace the global `runner.running` boolean (a singleton state
nightmare in a multi-worker deploy) with per-task rows.

### API-10 — Body schemas accept untyped dicts and free-form strings

- `SearchSettingsUpdate` (`settings.py:107`) types `keywords`, `excluded_keywords`, `locations`,
  `job_types`, `languages`, `excluded_companies`, `countries` all as `Optional[dict]`. A client
  can post `{"keywords": {"banana": 12}}` and it gets written to the DB and consumed by the
  matcher. Define `KeywordSet(BaseModel)` with `include: list[str]; exclude: list[str]` and use it
  for each.
- `UpdateApplicationRequest.status` (`applications.py:108`) is `Optional[str]` — no `Literal[...]`
  enum like its sibling `CreateApplicationRequest.status`. `PATCH` will silently accept `"banana"`
  and write it.
- `StatusUpdate.status` (`queue.py:236`) is also `str`; the route validates against `allowed` set
  at line 256 — that should be a Pydantic `Literal[...]` so the OpenAPI schema shows the legal
  values and validation happens at parse time (422 with a structured error).
- `CustomSiteCreate.url` (`settings.py:507`) — `str`, no http/https check, no length cap. Compare
  to `ApplyRequest.apply_url` (`applications.py:411`) which validates both — adopt the same
  validator.

### API-11 — Anonymous routes (design implication)

The standards backlog notes that no route is auth-guarded. From a *design* standpoint this means:

- There is no `Depends(get_current_user)` and so the routes can't take a `user_id` from context;
  `UserProfile` is hard-coded to `id=1` (`applications.py:471`, `settings.py:170, 252, 407`). This
  is a single-tenant assumption baked into the route layer. The moment a second user exists,
  every route needs a signature change.
- `apply_engine` and `batch_runner` are app-level singletons (`main.py:160–161`). A per-user
  apply queue, daily limit, or batch state is impossible without redesigning these as
  `Dict[user_id, ...]` or moving the state into the DB.
- Authorization (e.g. "user A cannot read user B's applications") cannot be added without
  rewriting every query — none of them filter by owner.

**Suggest:** even before implementing auth, introduce a `CurrentUser` dependency that returns a
hard-coded user 1 today but is the single seam to flip when auth lands. Make `apply_engine` and
`batch_runner` keyed by `user_id` from day one.

### API-12 — WebSocket protocol

`backend/api/ws.py`:

- **No auth:** `/ws` calls `websocket.accept()` (line 90) before reading any token. A query
  param `?token=` should be inspected before accept, with a 1008 close on failure.
- **`ClientMessage` discriminated union is unused.** `ws_models.py:177` defines a typed
  client→server discriminator, but `ws.py:175–186` does `json.loads` → `dict` → handler lookup by
  raw string. The handlers themselves (`main.py:171–197`) take `msg: dict` and call `.get("site",
  "")` / `.get("job_id", -1)` with hard-coded defaults — no validation. Replace with
  `ClientMessage.model_validate_json(data)` and dispatch on the `.type` discriminator.
- **`WSMessage` discriminated union is unused.** `broadcast_status` and
  `broadcast_job_assessment` (`ws.py:192, 201`) send plain dicts; the typed union doesn't include
  a `status` or `job_progress` variant at all (compare `ws_models.py:43–123` — there's no message
  type called `"status"` or `"job_progress"`). The schema is out of sync with what the server
  actually sends.
- **No heartbeat:** the server responds to `ping` from the client (`ws.py:179`) but does not
  emit its own. Idle connections will be reaped by ALBs/NGINX at 60 s; the client has no signal
  that the server is healthy.
- **No subscriptions / no fan-out filter:** `broadcast` (`ws.py:105`) sends every message to
  every connection. Two users in two browsers would each see the other's apply progress.
- **No backpressure handling:** `manager.broadcast` awaits each `send_text` serially; one slow
  client blocks everyone (`ws.py:117–121`).
- **`ConnectionManager._message_handlers` is touched from outside:** `main.py:199–202` reaches
  into a private attribute by name. Provide `manager.register_handler(...)` (which exists, line
  74) and use it; the dispatch at line 181 (`elif msg_type in manager._message_handlers`) should
  use `.get(msg_type)` on a property.
- **No reconnect semantics:** when a client reconnects, only the currently-running batch status is
  replayed (`ws.py:160–164`). There's no "missed events since timestamp X" mechanism, so a
  dropped connection during apply means the user loses the `apply_review` / `apply_result`
  message and is stuck.

**Suggest:** redesign as `POST /api/realtime/connect` returning a single-use ticket → `/ws?ticket=...`;
typed messages on both directions; server-side `ping` every 25 s; per-`user_id` channel; replace
broadcast with `send_to_user(user_id, msg)`.

### API-13 — OpenAPI metadata is bare

Every router has `tags=[...]` (good) but:

- No `summary=` on any route — the docs UI shows the function name.
- No `description=` beyond the docstring; the docstrings use Doxygen-style `@brief/@param/@return`
  that **FastAPI does not parse for OpenAPI**. The docs page shows the raw `@brief` text.
- No `responses=` declarations, so error 404/422/503/502 status codes documented in docstrings
  are invisible to the spec consumer.
- No `examples=` on request bodies.
- No `openapi_examples=` on `Query` params.

**Suggest:** standardise on `@router.get("/path", summary="One line", responses={404:
{"model": ErrorResponse}, ...})`. Strip the `@brief` Doxygen syntax from the docstring or route it
into a single `description=` parameter. Generate a TypeScript client via `openapi-typescript` from
the resulting schema — today it would be unusable.

### API-14 — Verb/path semantics

- `POST /api/documents/validate-template` (`documents.py:90`) — this is a pure function (no side
  effects). Should be `POST /api/latex/validate` (template isn't always a document) or kept POST
  but accepting body is fine; the noun is wrong — it's not validating a *document*.
- `PUT /api/settings/sites/{site_name}` (`settings.py:551`) — flipping a single boolean is a
  partial update. Should be `PATCH /api/settings/sites/{site_name}` accepting `{"enabled": bool}`.
- `POST /api/jobs/search` — semantically a query, not a creation. Reasonable alternatives:
  `GET /api/jobs?keywords=...&country=...` for the search, plus `POST /api/scrapes` (returning
  202) to trigger a persistence run.
- `PUT /api/settings/profile`, `PUT /api/settings/search`, `PUT /api/settings/sources` are all
  upserts with partial bodies — that's `PATCH` semantics, not `PUT` (which by spec replaces the
  whole resource). The handlers explicitly check `if body.X is not None` for every field, which
  is the canonical sign that PATCH is what's wanted.
- `POST /api/applications/{match_id}/apply` — overloads `application_id` and `match_id` in
  the same path namespace. Three routes earlier (`applications.py:226, 267, 327`) treat the path
  param as `application_id`, then `apply` treats it as `match_id`. The same integer means two
  different things in the same router; consider `POST /api/job-matches/{match_id}/apply`.

### API-15 — Duplicate response schemas

`JobOut` is defined twice with different field sets:

- `backend/api/jobs.py:32` — full job, includes `salary_text`, `description`, `score`,
  `posted_at`, etc.
- `backend/api/queue.py:36` — nested-inside-match, includes `country`, `apply_method`, drops
  `salary_text` / `score`, has different defaults (`apply_url: str = ""` vs `Optional[str] =
  None`).

Generated OpenAPI gives them auto-suffixed names (`JobOut`, `JobOut_1`), which a TS client will
import as two unrelated types. Rename the queue-side one (`QueueJobOut` or `JobSnapshot`) or
extract a shared model in `backend/api/_schemas.py`.

### API-16 — Singleton GET returns synthetic empty row instead of 404

`GET /api/settings/profile` (`settings.py:162`): when no `UserProfile` exists, returns
`ProfileOut(id=0, full_name="", email="", ..., created_at=now, updated_at=now)`. This violates the
schema's intent (`id: int` of a real row) and confuses callers that legitimately want to
distinguish "no profile yet" from "empty profile". Compare to `GET /api/settings/search`
(`settings.py:244`), which correctly raises 404. Standardise on 404.

### API-17 — `/api/health` mixes concerns

`main.py:248` returns liveness (`status: "ok"`), readiness (`db: "connected"` — but it's
hard-coded, not actually checked), config presence (`gemini_key_set`, `tectonic`), and a
human-readable `tectonic_hint`. K8s/load-balancer liveness probes want a 1-byte response;
readiness probes want a real DB ping; status pages want the config summary.

**Suggest:** split into `/api/livez` (200 always), `/api/readyz` (200 only if DB ping succeeds),
and `/api/settings/status` (already exists — fold the config flags into that, drop from
`/health`).

---

## WebSocket section

See API-12 for the structural findings. To summarise the protocol-shape issues separately:

**Defined-but-unused models** (`backend/api/ws_models.py`):

- `ClientMessage` discriminator (line 177) — not used; ws.py uses raw dict + `msg.get("type")`.
- `WSMessage` discriminator (line 126) — not used; broadcast helpers in ws.py send plain dicts
  whose `"type"` values (`"status"`, `"job_progress"`) **aren't even members of `WSMessage`**.

**Messages the server actually sends that aren't in `WSMessage`:**

- `{"type": "status", "message": str, "progress": float}` — `ws.py:198`.
- `{"type": "job_progress", "match_id": int, "ats_score": float, "gap_severity": float, "decision":
  str, "covered": list[str], "gaps": list[dict]}` — `ws.py:218`.
- `{"type": "pong"}` — `ws.py:180`.

**Messages `WSMessage` declares but no code emits** (grep `type=` and constructor names across
`backend/`):

- `ScrapingStatus`, `MatchingStatus`, `TailoringStatus` — referenced in tests only; the actual
  pipeline (`scheduler/morning_batch.py`, `scraping/orchestrator.py`) calls `broadcast_status()`
  which emits the untyped `"status"` shape.

**Action:** treat `ws_models.py` as the source of truth and rewrite every `broadcast_*` call to
construct a `WSMessage` variant. Add the missing variants (`Status`, `JobProgress`, `Pong`) to
the union. Validate inbound with `ClientMessage.model_validate_json` so handlers receive typed
objects, not `dict.get("...", default)`.

**Handshake / lifecycle gaps:**

- No `hello` message on accept — client has no way to learn its `client_id`.
- No version negotiation — adding a new message type breaks all old clients silently.
- No `close` codes on errors (the loop swallows everything at line 187 `except Exception: pass`).

---

## Already good

- **Dependency injection is consistent.** `DBSession` type alias (`deps.py:26`) is used by every
  route that needs a DB session; singleton getters (`get_apply_engine`, `get_cv_pipeline`, etc.) are
  defined uniformly. The pattern is clean — it just needs `get_current_user` added.
- **Pydantic response models are correctly configured** (`model_config = ConfigDict(from_attributes=True)`)
  on every `Out` model that wraps an ORM row — `JobOut`, `ApplicationOut`, `ProfileOut`, etc.
- **`backend/api/jobs.py:78` and `backend/api/applications.py:158`** are good models of paginated
  list endpoints with capped `limit` (`ge=1, le=200`) — extend that style to `/queue`,
  `/documents`, `/analytics/trends`.
- **`CreateApplicationRequest`, `CreateEventRequest`** (`applications.py:89, 114`) correctly use
  `Literal[...]` for enums so the OpenAPI schema lists the legal values — that's the pattern the
  ad-hoc `str` fields (API-10) should follow.
- **`ApplyRequest.validate_url` / `validate_json`** (`applications.py:411–430`) are the only
  custom field validators in the API surface and they do the right thing (length cap, schema
  check, scheme check). Re-use them.
- **`RawJob`** in `models/schemas.py:24` already has `Field(max_length=...)` on every string
  column — that pattern is missing in the API request bodies and should be lifted.
- **Status code 409 for conflict** (`queue.py:168` on duplicate batch run) and **422 for
  validation** (`queue.py:258` on bad match status) are *correctly chosen* — the issue is the
  enum check happens in the handler body instead of in the Pydantic model (API-10).
- **The exception-handler-based mapping** for `LaTeXCompilationError → 422`,
  `GeminiRateLimitError → 429` (`main.py:288–324`) is the right pattern — extend it to all
  domain exceptions instead of `raise HTTPException(...)` in every handler.
- **The lifespan pattern** in `main.py:53–211` correctly wires singletons via `app.state` and
  shuts down cleanly; no anti-patterns there.

---

## Suggested next steps (ordered by impact)

1. **Define `ErrorResponse` and one global exception → JSON shape mapping.** Closes API-02 and
   makes future findings cleaner.
2. **Mount under `/api/v1/`.** One-line change today, expensive to do later.
3. **Add `response_model=` to all 18 untyped routes** and a small set of `Out` schemas for the
   raw-dict responses. Closes API-03.
4. **Fix status codes** per API-04 table — particularly 201 on create, 204 on delete, 202 on
   async kicks.
5. **Add `Idempotency-Key` and uniqueness on `apply`.** Closes API-05.
6. **Introduce `CurrentUser` dependency** (returns user 1 today) — single seam for auth landing
   (API-11).
7. **Rewrite WS to use the typed models** and add auth on connect (API-12).
8. **Add bulk endpoints for apply/skip/regenerate** (API-06). Cheaper than fixing the frontend
   loop and trims request count by ~10×.
9. **Standardise pagination/filtering params** across `/jobs`, `/queue`, `/applications`,
   `/documents` (API-07, API-08).
10. **Replace `dict` body fields with structured `KeywordSet` / `LocationSet` models** (API-10).
