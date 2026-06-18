# HTTP & WebSocket API

JobPilot's backend is a FastAPI app (`backend/main.py`) that mounts a set of `APIRouter` modules under `/api/*` plus a single `/ws` WebSocket; this reference enumerates every route module, the WebSocket protocol, dependency injection, and the health schema.

## How the app is assembled

The app object is built in `backend/main.py`. Routers are imported and mounted inside a `try/except` block (`backend/main.py:301-332`) so a missing module degrades gracefully instead of crashing startup:

```python
app.include_router(jobs.router)
app.include_router(queue.router)
app.include_router(today.router)
# Export router first so /export is not shadowed by /{id} on applications router
app.include_router(applications_export.router)
app.include_router(applications.router)
...
app.include_router(ws.router)
```

Note the ordering comment at `backend/main.py:319`: `applications_export.router` (which owns `GET /api/applications/export`) is mounted **before** `applications.router`, so the literal `/export` path is not captured by the `/{application_id}` path parameter.

There is **no** `health.py` or `onboarding.py` module — the health probe is defined inline in `backend/main.py:335`, and onboarding/setup state is served by the settings router (`GET /api/settings/status`, `backend/api/settings.py:320`).

### Router modules

| Module | Prefix | Tag | File |
|---|---|---|---|
| jobs | `/api/jobs` | jobs | `backend/api/jobs.py:16` |
| queue | `/api/queue` | queue | `backend/api/queue.py:17` |
| today | `/api/today` | today | `backend/api/today.py:41` |
| applications_export | `/api/applications` | applications | `backend/api/applications_export.py:25` |
| applications | `/api/applications` | applications | `backend/api/applications.py:24` |
| documents | `/api/documents` | documents | `backend/api/documents.py:24` |
| settings | `/api/settings` | settings | `backend/api/settings.py:72` |
| analytics | `/api/analytics` | analytics | `backend/api/analytics.py:17` |
| gmail_auth | `/api/gmail` | gmail | `backend/api/gmail_auth.py:27` |
| gmail | `/api/gmail` | gmail | `backend/api/gmail.py:12` |
| correspondence | `/api/correspondence` | correspondence | `backend/api/correspondence.py:16` |
| ws | _(none)_ | — | `backend/api/ws.py:39` |

Most routers set `redirect_slashes=False`, so trailing-slash variants are not auto-redirected.

## Dependency injection

DI is intentionally minimal. `backend/api/deps.py` exposes a **single** public symbol:

```python
DBSession = Annotated[AsyncSession, Depends(get_db)]
```

`get_db` (`backend/database.py:121`) yields an `AsyncSession` from the shared `AsyncSessionLocal` pool. Route handlers take `db: DBSession` to get a request-scoped session.

The older `get_session_manager` / `get_apply_engine` / `get_cv_pipeline` / `get_scraping_orchestrator` / `get_batch_runner` helpers were removed (see the note at `backend/api/deps.py:14-19`). Long-lived singletons are **not** injected via `Depends`; they are read directly off `request.app.state` inside each endpoint.

### `app.state` singletons

Wired during the lifespan startup (`backend/main.py:179-188`, `:259`):

| Attribute | Purpose |
|---|---|
| `app.state.gemini` | LLM generation client |
| `app.state.cv_pipeline` | CV tailoring pipeline |
| `app.state.letter_pipeline` | Cover-letter pipeline |
| `app.state.adzuna` | Adzuna API client |
| `app.state.adaptive_scraper` | Adaptive scraping engine |
| `app.state.session_manager` | Browser session manager |
| `app.state.scraping_orchestrator` | Scraping orchestrator |
| `app.state.matcher` | Job/profile matcher |
| `app.state.apply_engine` | Auto-apply engine |
| `app.state.batch_runner` | Batch run-loop (holds `running` + `last_status`) |
| `app.state.gmail_token_manager` | Gmail OAuth token manager |

Endpoints reach these via `request: Request` + `getattr(request.app.state, "...", None)`, returning `503` when a singleton is missing (e.g. `backend/api/gmail.py:57`).

---

## Jobs — `/api/jobs`

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/jobs` | `JobListOut` | List/paginate scraped jobs |
| GET | `/api/jobs/{job_id}` | `JobOut` | Fetch a single job |
| POST | `/api/jobs/search` | `SearchResponse` | Trigger a scrape/search run (`search_jobs`, `backend/api/jobs.py:163`) |
| GET | `/api/jobs/{job_id}/score` | `JobScoreOut` | Fit/ATS score for a job |

## Queue — `/api/queue`

The queue holds tailored `Match` candidates awaiting review/apply.

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/queue` | `QueueOut` | List queued matches |
| GET | `/api/queue/status` | `BatchStatusOut` | Current batch-run status |
| GET | `/api/queue/source-health` | _(dict)_ | Per-source scraper health (see `SourceHealthTracker`) |
| POST | `/api/queue/refresh` | `RefreshResponse \| PreviewResponse` | Refresh queue; preview vs. commit |
| GET | `/api/queue/{match_id}` | `QueueMatchOut` | One match |
| PATCH | `/api/queue/{match_id}/skip` | `MatchStatusUpdateOut` | Skip a match |
| PATCH | `/api/queue/{match_id}/status` | `MatchStatusUpdateOut` | Update match status |
| POST | `/api/queue/{match_id}/enrich-description` | `EnrichmentResponse` | Fetch/enrich full job description |

## Today — `/api/today`

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/today` | `TodayOut` | Dashboard "today" summary |

## Applications — `/api/applications`

Split across two routers (`applications_export.py` mounted first).

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/applications/export` | `StreamingResponse` (CSV) | Stream all applications as CSV download; `?format=csv` required, else `400` (`backend/api/applications_export.py:65`) |
| POST | `/api/applications` | `ApplicationOut` (201) | Create an application record |
| GET | `/api/applications` | `ApplicationListOut` | List applications |
| GET | `/api/applications/limit-status` | `LimitStatusOut` | Daily apply-limit status |
| GET | `/api/applications/{application_id}` | `ApplicationOut` | One application |
| PATCH | `/api/applications/{application_id}` | `ApplicationOut` | Update an application |
| POST | `/api/applications/{application_id}/events` | `ApplicationEventOut` (201) | Append a status/event to the timeline |
| GET | `/api/applications/{job_id}/review-state` | _(dict)_ | Review state for a job |
| POST | `/api/applications/{match_id}/apply` | `ApplicationResult` | Run the auto-apply engine for a match |

## Documents — `/api/documents`

Generated CVs, cover letters, and template validation. PDF routes return `FileResponse`; LaTeX failures surface as `422` via the global handler (see below).

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/documents` | `list[DocumentOut]` | List generated documents |
| POST | `/api/documents/validate-template` | `ValidateTemplateResponse` | Validate a LaTeX template |
| POST | `/api/documents/compile-test` | `CompileTestResponse` | Test-compile a template |
| GET | `/api/documents/{match_id}/cv/pdf` | `FileResponse` | Download tailored CV PDF |
| GET | `/api/documents/{match_id}/letter/pdf` | `FileResponse` | Download cover-letter PDF |
| GET | `/api/documents/{match_id}/diff` | `CVDiffResponse` | Diff of tailored vs. base CV |
| POST | `/api/documents/{match_id}/regenerate` | `RegenerateResponse` | Re-tailor the CV |
| POST | `/api/documents/{match_id}/letter/regenerate` | `LetterRegenerateResponse` | Regenerate the cover letter |

## Settings — `/api/settings`

Profile, search config, sources, credentials, custom sites, and the onboarding/setup status probe.

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/settings/profile` | `ProfileOut` | Read user profile |
| PUT | `/api/settings/profile` | `ProfileOut` | Update profile |
| GET | `/api/settings/search` | `SearchSettingsOut` | Read search settings |
| PUT | `/api/settings/search` | `SearchSettingsOut` | Update search settings |
| GET | `/api/settings/sources` | `SourcesOut` | Read enabled job sources |
| PUT | `/api/settings/sources` | `SourcesUpdateResponse` | Update sources |
| GET | `/api/settings/status` | `SetupStatus` | Onboarding/setup completeness |
| POST | `/api/settings/profile/cv-upload` | `CvUploadResponse` | Upload base CV (multipart `UploadFile`) |
| GET | `/api/settings/sites` | `list[SiteOut]` | List configured sites |
| PUT | `/api/settings/sites/{site_name}` | `SiteToggleResponse` | Enable/disable a site |
| GET | `/api/settings/credentials` | `list[CredentialOut]` | List site credentials (email masked) |
| PUT | `/api/settings/credentials/{site_name}` | `CredentialSaveResponse` | Save site credentials |
| DELETE | `/api/settings/credentials/{site_name}/session` | `SessionClearResponse` | Clear a saved browser session |
| GET | `/api/settings/custom-sites` | `list[CustomSiteOut]` | List custom (lab-website) sources |
| POST | `/api/settings/custom-sites` | `CustomSiteOut` | Add a custom source |
| DELETE | `/api/settings/custom-sites/{site_id}` | `CustomSiteDeleteResponse` | Delete a custom source |

## Analytics — `/api/analytics`

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/analytics/summary` | `AnalyticsSummary` | Aggregate counts/metrics |
| GET | `/api/analytics/trends` | `AnalyticsTrends` | Time-series trends (`DailyTrend` rows) |

## Gmail OAuth — `/api/gmail` (`gmail_auth.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/gmail/oauth/start` | Begin OAuth flow (redirect to Google) |
| GET | `/api/gmail/oauth/callback` | OAuth redirect handler; exchanges code, stores token |
| POST | `/api/gmail/disconnect` | Revoke/forget the connected account |

## Gmail sync — `/api/gmail` (`gmail.py`)

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/gmail/status` | `GmailStatusOut` | Connection state, account, last sync, message count |
| POST | `/api/gmail/sync` | `SyncOut` | Force a sync pass (uses `app.state.gmail_token_manager`; `404` if no account, `503` if integration uninitialised) |

## Correspondence — `/api/correspondence`

Links inbound Gmail threads to applications.

| Method | Path | Response model | Purpose |
|---|---|---|---|
| GET | `/api/correspondence/unlinked` | `UnlinkedListOut` | Messages not yet linked to an application |
| GET | `/api/correspondence/{application_id}` | `CorrespondenceThreadOut` | Thread for one application |
| POST | `/api/correspondence/link` | `CorrespondenceLinkOut` (201) | Link a message/thread to an application |
| DELETE | `/api/correspondence/{link_id}` | _(204, no body)_ | Remove a link |

---

## Health — `GET /api/health`

Defined inline at `backend/main.py:335`. It actively pings the DB (`SELECT 1` through `AsyncSessionLocal`), checks for a `tectonic` binary, and checks whether `GOOGLE_API_KEY` is configured. On DB failure it returns **HTTP 503** with `status="degraded"` so k8s/Docker probes can react; exception text is never leaked — only a short `db_error_code` is surfaced and the traceback is logged.

`HealthOut` schema (`backend/main.py:30`):

| Field | Type | Notes |
|---|---|---|
| `status` | `"ok" \| "degraded"` | `degraded` only when the DB ping fails |
| `version` | `str` | currently `"0.1.0"` |
| `timestamp` | `datetime` | UTC |
| `db` | `"ok" \| "error"` | DB ping result |
| `tectonic` | `bool` | LaTeX engine present |
| `gemini_key_set` | `bool` | `GOOGLE_API_KEY` configured |
| `tectonic_hint` | `str \| null` | install hint when tectonic missing |
| `db_error_code` | `str \| null` | e.g. `"db_unreachable"` |

`tectonic` and `gemini_key_set` are advisory only — their absence does **not** flip overall status.

## Global error handlers

Registered in `backend/main.py:400-464`. They normalize internal exceptions into JSON `{"error", "code"}` bodies:

| Exception | Status | `code` |
|---|---|---|
| `LaTeXCompilationError` | 422 | `latex_compile_error` |
| `GeminiJSONError` (LLM JSON validation) | 500 | `gemini_json_error` |
| `GeminiRateLimitError` | 429 | `rate_limit` |
| any unhandled `Exception` | 500 | `internal_error` |

The Gemini exception aliases now map to provider-neutral `LLM*` exceptions (`backend/llm/base.py`), so the handlers cover any LLM provider. Non-API paths fall through to the SPA fallback (`SPAStaticFiles`, `backend/main.py:468`) which serves `index.html`.

---

## WebSocket — `/ws`

Endpoint `websocket_endpoint` (`backend/api/ws.py:105`) plus the `ConnectionManager` (`backend/api/ws.py:43`) implement a JSON push channel for live batch-run narration. Message schemas live in `backend/api/ws_models.py`.

### Connection lifecycle

1. Client connects to `/ws`; `manager.connect()` assigns a `client_id`.
2. **Resume replay**: if `app.state.batch_runner` is `running` and has a `last_status`, the server immediately sends it so reconnecting clients catch up (`backend/api/ws.py:113-119`).
3. Server loops on `receive_text()`; each frame is JSON-decoded and dispatched by its `type` field.
4. On `WebSocketDisconnect` the loop breaks and `manager.disconnect(client_id)` runs in `finally`.

### Dispatch & encoding

`ConnectionManager` (`backend/api/ws.py:43`) holds the connection set and a `_message_handlers: Dict[str, handler]` registry populated via `register_handler(msg_type, handler)`. Outgoing messages are serialized by the static `_encode()` and pushed to all peers via `broadcast()`. Module-level helpers wrap broadcasts: `broadcast_status`, `broadcast_job_assessment`, `broadcast_gmail_sync_status`, `broadcast_gmail_message_received` (`backend/api/ws.py:149+`).

`type` is the discriminator on every message. Unknown inbound types are logged and ignored; `{"type":"ping"}` is answered with `Pong` directly in the loop (`backend/api/ws.py:134`).

### Server → client messages (`WSMessage`, `ws_models.py:154`)

Discriminated union (Pydantic `Field(discriminator="type")`):

| Class | `type` wire value | Key fields |
|---|---|---|
| `Status` | `status` | `message`, `progress` |
| `JobAssessment` | `job_progress` (legacy discriminator kept) | `match_id`, `ats_score`, `gap_severity`, `decision`, `covered: list[str]`, `gaps: list[SkillGap]` |
| `ScrapingStatus` | `scraping_status` | `message`, `source`, `progress` |
| `MatchingStatus` | `matching_status` | `count` |
| `TailoringStatus` | `tailoring_status` | `job_id`, `progress` |
| `ApplyReview` | `apply_review` | `job_id`, `filled_fields: dict[str,str]`, `screenshot_base64?` |
| `ApplyResult` | `apply_result` | `job_id`, `status`, `method` |
| `LoginRequired` | `login_required` | `site`, `browser_window_title` |
| `LoginConfirmed` | `login_confirmed` | `site` |
| `CaptchaDetected` | `captcha_detected` | `site`, `job_id?`, `message` |
| `CaptchaResolved` | `captcha_resolved` | `job_id?` |
| `GmailSyncStatus` | `gmail_sync_status` | `last_history_id?`, `messages_synced`, `progress` |
| `GmailMessageReceived` | `gmail_message_received` | `gmail_message_id`, `from_address`, `subject?`, `category?`, `category_confidence?`, `linked_application_id?`, `link_confidence?` |
| `Pong` | `pong` | _(reply to `ping`)_ |
| `ErrorMessage` | `error` | `message`, `code` |

`SkillGap` (`ws_models.py:36`) is a nested payload inside `JobAssessment`: `{ skill: str, criticality: float }` — `criticality` is a float weight on the wire.

### Client → server messages (`ClientMessage`, `ws_models.py:208`)

| Class | `type` | Key fields |
|---|---|---|
| `ConfirmSubmit` | `confirm_submit` | `job_id` |
| `CancelApply` | `cancel_apply` | `job_id` |
| `PatchFields` | `patch_fields` | `job_id`, `fields: dict[str,str]` (selector → corrected value, sent before `confirm_submit`) |
| `LoginDone` | `login_done` | `site` |
| `LoginCancel` | `login_cancel` | `site` |

Plus the bare `{"type":"ping"}` handled inline. Inbound types other than `ping` must have a handler registered via `register_handler`, or they are logged as unknown.

> Compatibility note: `ws_models.py` defines a fallback `BaseModel`/`Field`/`confloat` shim (`ws_models.py:7-25`) so the module imports even if Pydantic is unavailable.

---

## Related

- [Architecture](architecture.md) — how the routers, `app.state` singletons, and pipelines fit together
- [File Map](file-map.md) — backend file-by-file index
- [Frontend File Map](frontend-file-map.md) — Svelte client that consumes this API + `/ws`
- [User Guide](user-guide.md) — running the app end to end
- [Custom Templates](custom-templates.md) — LaTeX templates used by the documents routes
- [Docs index](index.md) · [README](../README.md)
