# Code Review: Production Readiness

## Summary
- Total findings: 38 (Critical: 6, High: 11, Medium: 12, Low: 9)
- Overall assessment: JobPilot has a solid architectural foundation with good separation of concerns, Pydantic validation at API boundaries, and a working prompt-injection sanitizer. However, several serious issues remain: a wildcard CORS + credentials configuration that is dangerous in any deployed context, an untracked fire-and-forget task that silently swallows batch errors, blocking synchronous I/O called inside async functions without an executor, multiple resource leaks in browser automation paths, and a CV-upload flow that writes an attacker-controlled path directly to the user profile without server-side validation.

---

## Critical — Security & Data Loss Risks

**[CRIT-1]** `backend/main.py:170-176` — **Wildcard CORS combined with `allow_credentials=True`**
`CORSMiddleware` is configured with `allow_origins=["*"]` and `allow_credentials=True`. The CORS specification prohibits this combination; browsers will reject credentialed cross-origin requests when the reflected `Access-Control-Allow-Origin` is a wildcard. More importantly, if the server ever migrates to cookie-based auth or the user's local network is accessible from a malicious page, this configuration allows any website to make credentialed requests to the API on behalf of the user.
*Fix:* Replace `allow_origins=["*"]` with an explicit allowlist (e.g. `["http://localhost:5173", "http://localhost:5174"]` for dev). For production, read the list from an environment variable. Remove `allow_credentials=True` unless cookies/auth headers are genuinely needed.

**[CRIT-2]** `backend/api/settings.py:130-166` — **User-controlled file path written to DB without validation (path traversal)**
`PUT /api/settings/profile` accepts `base_cv_path` as a free-form string and writes it directly to the `UserProfile` record. The scheduler then calls `Path(profile_row.base_cv_path)` and reads the file. An attacker (or a confused frontend) can set `base_cv_path` to an arbitrary path such as `../../../../etc/passwd`, causing the LaTeX pipeline to read files outside the data directory. The frontend in `cv/+page.svelte:63-64` also sends `uploads/${fileName}` without any server-side verification that the file exists at that path.
*Fix:* Validate that `base_cv_path`, when set, resolves to a path inside `settings.jobpilot_data_dir`. Reject any path that contains `..` or that does not start with the data directory after `Path.resolve()`.

**[CRIT-3]** `backend/api/queue.py:119` — **Untracked `asyncio.create_task` with no error surfacing**
`POST /api/queue/refresh` creates an asyncio task with `asyncio.create_task(_run())`. The inner `_run()` coroutine catches exceptions and logs them but the task itself is never stored in a variable or tracked. If the event loop shuts down while the task is in flight, the exception is silently discarded and may appear as an `asyncio: Task was destroyed but it is pending!` warning. More critically, there is no way for the caller to discover that the batch failed.
*Fix:* Store the task reference (`task = asyncio.create_task(_run())`) and attach a done callback via `task.add_done_callback(...)` that re-logs any unhandled exception. Consider using a bounded queue or a dedicated background task manager instead of raw fire-and-forget tasks.

**[CRIT-4]** `backend/applier/form_filler.py:318` — **Browser context intentionally leaked on `fill_only` success path**
In `PlaywrightFormFiller.fill_only` (line 318), when the fill succeeds the method sets `context = None` to prevent the `finally` block from closing the browser, but `pw` (the Playwright instance) is **not** set to `None`. The `finally` block therefore calls `await pw.stop()` while the context is still open. This destroys the underlying browser process without closing the context, leaking the Chromium process and its associated resources. Each assisted-apply call spawns a new persistent context that is never fully cleaned up.
*Fix:* Either store a separate flag to decide whether to close the context, or document a separate cleanup path that the caller must invoke when the user eventually closes the browser.

**[CRIT-5]** `backend/llm/gemini_client.py:64-68` — **Lambda capture bug causes all retry attempts to use the same model name**
Inside `generate_text`, the retry loop uses `lambda: self._client.models.generate_content(model=self._model_name, contents=prompt)`. Because Python closures capture variables by reference, every invocation of the lambda reads `self._model_name` at call time — which is mutated by the outer loop at line 61 (`self._model_name = self._candidates[model_try]`). This is coincidentally correct for sequential retries, but it also means `self._model_name` is mutated as a side effect on the shared `GeminiClient` instance. If two concurrent async requests are in flight (which is possible since `run_in_executor` is used), they will race on `self._model_name`, causing one request to use a model chosen by the other's retry path.
*Fix:* Capture the model name in the loop body as a local variable and use it in the lambda: `model = self._candidates[model_try]; lambda: self._client.models.generate_content(model=model, contents=prompt)`.

**[CRIT-6]** `backend/api/settings.py:472-508` — **Credential `site_name` not validated against `SITE_CONFIGS` before Fernet decryption**
`PUT /api/settings/credentials/{site_name}` does validate the site name against `SITE_CONFIGS` (line 475-478), but `GET /api/settings/credentials` iterates only over `login_sites` (sites with `requires_login=True`). A site credential row in the DB with a `site_name` not in `SITE_CONFIGS` (inserted e.g. by direct DB manipulation) would cause a `KeyError` inside the loop at line 445. More critically, the `CREDENTIAL_KEY` is a symmetric Fernet key stored in `.env` with no validation at startup — if the key is empty or malformed, decryption silently falls back to `"***@***"` (line 456-458) rather than erroring, masking a misconfiguration.
*Fix:* Validate that `CREDENTIAL_KEY`, when non-empty, is a valid base64-encoded 32-byte key at startup in `Settings`. Add a `field_validator` in `config.py` for `CREDENTIAL_KEY`.

---

## High — Architecture & Reliability

**[HIGH-1]** `backend/scraping/scrapling_fetcher.py:186` — **Blocking synchronous HTTP fetch called inside `run_in_executor` without a dedicated thread pool**
`ScraplingFetcher.fetch_page` calls `asyncio.get_event_loop().run_in_executor(None, _fetch_sync)`, passing `None` for the executor, which uses the default `ThreadPoolExecutor`. The Scrapling `StealthyFetcher.fetch()` and `Fetcher.fetch()` calls are blocking and can take 10-60 seconds each. With the default thread pool (typically `min(32, os.cpu_count() + 4)` threads), several concurrent scraping calls can saturate all threads, starving the event loop of workers for other blocking operations (DB queries via aiosqlite also use the default executor).
*Fix:* Create a dedicated `ThreadPoolExecutor` with an appropriate bound (e.g. `max_workers=4`) and pass it to `run_in_executor`. Alternatively, use an async HTTP library instead of the synchronous Scrapling API where possible.

**[HIGH-2]** `backend/applier/auto_apply.py:178` — **`browser-use` `Browser` object leaks when `agent.run()` hangs past timeout**
In `_browser_use_apply`, `asyncio.wait` is used with a `timeout=1800` to wait for user confirmation. If the wait completes (user confirms or cancels), the browser is stopped. However, during the initial `agent.run()` call at line 185, there is **no timeout**. If the browser-use agent hangs indefinitely (e.g. network issue, infinite loop), `agent.run()` never returns, the confirmation wait is never reached, and the `Browser` object created at line 178 is never stopped until the process exits or an exception occurs in an unrelated path.
*Fix:* Wrap `agent.run()` in `asyncio.wait_for(agent.run(), timeout=300)` (5 minutes) with a `finally` block that calls `await browser.stop()`.

**[HIGH-3]** `backend/scheduler/morning_batch.py:135-137` — **`_run_batch_task` uses `asyncio.ensure_future` which silently drops exceptions**
When APScheduler calls `_run_batch_task` synchronously, it creates a task via `asyncio.ensure_future(self.run_batch())`. `run_batch` already catches its own exceptions (line 150), but `asyncio.ensure_future` returns a future that is never stored. If the future is cancelled by the event loop during shutdown, the cancellation exception is silently swallowed. This duplicates the issue in CRIT-3.
*Fix:* Same pattern as CRIT-3 — store the future and attach a done callback.

**[HIGH-4]** `backend/api/jobs.py:66-94` — **N+1 query in `list_jobs` endpoint**
`GET /api/jobs` fetches up to 200 jobs (line 57) and then issues one additional `SELECT` per job to fetch the latest match score (lines 68-75). With a limit of 200, this generates up to 201 DB queries per request.
*Fix:* Use a single `LEFT OUTER JOIN` or a correlated subquery to fetch the latest match score alongside each job row. SQLAlchemy supports this via `outerjoin` with a subquery.

**[HIGH-5]** `backend/applier/form_filler.py:89-225` — **Playwright context not closed on `RuntimeError("Confirmation timed out")` path**
In `fill_and_submit`, if `asyncio.wait` times out at line 203, `RuntimeError("Confirmation timed out after 30 minutes")` is raised. The `finally` block at line 216 does close the context and stop playwright in this case, which is correct. However, if `page.click(submit_sel, ...)` at line 211 raises an exception, the `RuntimeError` propagates and the finally block correctly handles cleanup. This specific path is actually handled, but the `pw.stop()` call at line 223 can itself raise if `context.close()` already raised, because `finally` blocks do not nest exception handling. If `context.close()` raises, the `pw.stop()` at line 223 is still reached (correct), but the original exception is masked by the context-close exception.
*Fix:* Wrap `pw.stop()` in its own `try/except` block (already done at line 222-225), which is correct. The actual bug is that `pw` is not stopped if the `await async_playwright().start()` at line 89 succeeds but the `launch_persistent_context` at line 92 raises — in that case `context` is `None`, the `if context:` guard skips context cleanup, but `pw` is still stopped. This path is correctly handled. The `fill_only` method has the real resource leak described in CRIT-4.

**[HIGH-6]** `backend/scraping/session_manager.py:84-131` — **`BrowserSessionManager.get_or_create_session` returns a stopped `Browser` object**
After saving the session state at line 127 (`await browser.stop()`), the method returns the `browser` instance (line 131). The caller (`orchestrator.py`) does not use the returned browser object (it passes `None` to the scraping call), but returning a stopped browser object is misleading. If any future caller tries to use the returned value to create pages, it will fail silently.
*Fix:* Return `None` explicitly after `browser.stop()` at line 131, or change the return type to `None` and update the docstring.

**[HIGH-7]** `backend/applier/captcha_handler.py:189-191` — **`CaptchaResolved` broadcast on timeout with no `job_id` check**
At line 189, `CaptchaResolved(job_id=job_id)` is broadcast even when the block resolution timed out. The frontend would interpret this as the CAPTCHA being solved successfully, causing it to proceed with the application while the page is still blocked. The `resolved` return value is `False` but the WS message says otherwise.
*Fix:* Only broadcast `CaptchaResolved` on the success path (line 185). On timeout, broadcast a dedicated error or omit the broadcast entirely so the UI remains in the CAPTCHA-pending state.

**[HIGH-8]** `backend/api/ws.py:55-56` — **`websocket.accept()` exception is silently swallowed**
In `ConnectionManager.connect`, the `await websocket.accept()` call at line 54 is wrapped in a bare `try/except Exception: pass`. If `accept()` raises (e.g. the client disconnected before the server could accept), the connection is added to `active_connections` anyway with a non-accepted socket. Any subsequent `broadcast` call will then fail on `ws.send_text(payload)`, which is caught and triggers a disconnect, but this wastes an iteration through the broadcast loop for every future broadcast.
*Fix:* Raise the exception from `accept()` instead of swallowing it, and let the `websocket_endpoint` handler catch it and not register the client.

**[HIGH-9]** `backend/scheduler/morning_batch.py:146` — **DB session created outside a context manager**
In `run_batch` at line 146, `db = self._db_factory()` creates a session that is explicitly closed in the `finally` block at line 152. However, `AsyncSessionLocal()` returns an `AsyncSession` that is designed to be used as an async context manager. If `_run_batch_inner` commits successfully but the `db.close()` call raises, the exception is propagated without any rollback. The session is not guaranteed to be in a clean state.
*Fix:* Use `async with self._db_factory() as db:` instead of manual open/close.

**[HIGH-10]** `backend/llm/gemini_client.py:64` — **`run_in_executor` called with deprecated `get_event_loop()`**
`asyncio.get_event_loop().run_in_executor(...)` is called in `generate_text`. In Python 3.10+, `get_event_loop()` emits a `DeprecationWarning` when called without a running loop, and in Python 3.12 it raises a `RuntimeError` in certain contexts. The correct pattern is `asyncio.get_running_loop().run_in_executor(...)`.
*Fix:* Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` on lines 64 and 186 in `scrapling_fetcher.py`.

**[HIGH-11]** `backend/applier/assisted_apply.py:109-131` — **`Browser` instance leaks on successful `agent.run()` path**
In `AssistedApplyStrategy._browser_use_apply` (Tier 2 path), when `agent.run()` completes without exception the method returns `ApplicationResult(status="assisted", ...)` at line 125 **without stopping the browser**. The `browser` object created at line 109 is abandoned. Only in the exception path (line 120-123) is `browser.stop()` attempted.
*Fix:* Move the `ApplicationResult` return into a `finally` block that calls `await browser.stop()`, or restructure the try/finally to always close the browser before returning.

---

## Medium — Code Quality

**[MED-1]** `backend/llm/prompts.py:33` — **Hardcoded candidate-specific CV profile in the job analyzer prompt**
The `JOB_ANALYZER_PROMPT` at line 33 contains hardcoded domain knowledge about a specific candidate: `"The candidate's CV is in Food Science / Laboratory domain. Their known skills include: cell culture techniques, XTT assays, HACCP, GMP..."`. This means every user of the system gets their jobs analyzed against a fixed Food Science candidate profile, which is incorrect for any other user.
*Fix:* Remove the hardcoded profile block. Pass the relevant candidate skills as a template variable `{candidate_skills}` populated from the user's profile or CV text at call time in `JobAnalyzer.analyze`.

**[MED-2]** `backend/api/applications.py:68-72` — **`UpdateApplicationRequest.status` accepts any string (no enum validation)**
`UpdateApplicationRequest` defines `status: Optional[str] = None`. The `PATCH /api/applications/{id}` endpoint sets `app.status = body.status` without checking whether it is a valid value. Any arbitrary string can be written to the DB, while `CreateApplicationRequest.status` at line 64 uses a proper `Literal` type.
*Fix:* Change `status: Optional[str]` to `status: Optional[Literal["pending", "applied", "cancelled", "failed", "interview", "offer", "rejected"]]` in `UpdateApplicationRequest`.

**[MED-3]** `backend/database.py:65-67` — **`get_db` dependency does not commit or rollback**
The `get_db` FastAPI dependency yields a session but never commits or rolls back changes. Any route that modifies data through this dependency and forgets to call `await db.commit()` will silently drop changes when the session closes. Contrast with `db_session()` which commits on success and rolls back on exception.
*Fix:* Replace the bare `yield session` pattern with try/commit/except rollback inside `get_db`, consistent with `db_session`. Alternatively, add a commit to the `finally` block with a fallback rollback on exception.

**[MED-4]** `backend/main.py:116-117` — **Singleton init failures are non-fatal and silently degrade the app**
If any singleton fails to initialise (line 116-117), the application starts with `app.state` missing attributes such as `apply_engine`, `morning_scheduler`, etc. Any API endpoint that calls `getattr(request.app.state, "apply_engine", None)` and receives `None` raises HTTP 503, but the root cause is never surfaced to the operator at startup.
*Fix:* Log each singleton init failure separately with `exc_info=True`. Consider adding a startup health check that lists which singletons failed to initialize.

**[MED-5]** `backend/applier/engine.py:44` — **`model` parameter has no type annotation**
`ApplicationEngine.__init__` defines `model: str = None` (line 44). The default is `None` but the type hint says `str`. This is a `None` assigned to a `str`-typed parameter, which is a type error that pyright and mypy will flag. The actual intent is `Optional[str] = None`.
*Fix:* Change to `model: Optional[str] = None` and add `from typing import Optional`.

**[MED-6]** `backend/scheduler/morning_batch.py:261-268` — **`_load_settings` returns a detached, non-persisted `SearchSettings` default**
When no `SearchSettings` row exists in the DB, `_load_settings` constructs a bare `SearchSettings(id=1, keywords=["python", "machine learning"], ...)` and returns it without adding it to the session or committing it. The hardcoded `["python", "machine learning"]` default keywords will silently drive the batch run if setup is incomplete, potentially producing irrelevant job matches.
*Fix:* Either raise an exception with a clear message ("Search settings not configured — please complete setup"), or document this default behavior explicitly. Do not use hardcoded domain-specific keywords as defaults.

**[MED-7]** `backend/api/documents.py:165-200` — **`regenerate_documents` endpoint queues regeneration but does nothing**
The `POST /api/documents/{match_id}/regenerate` handler at line 165 deletes existing documents if `force=True`, logs "Regeneration queued", and returns `{"status": "queued"}` — but does not actually trigger any regeneration task. The background tasks parameter is injected but never used (line 169). This is a stub that silently misleads users.
*Fix:* Either wire up a real background task that calls `cv_pipeline.generate_tailored_cv(...)`, or return HTTP 501 Not Implemented with a message that the feature is not yet available.

**[MED-8]** `backend/scraping/adaptive_scraper.py:170-188` — **Job detail extraction prompt contains user-controlled `job_url` without sanitization**
In `scrape_job_details`, the `prompt` string at line 170 directly interpolates `{job_url}` without calling `sanitize_url` or `sanitize_for_prompt`. While `sanitize_url` is used in `json_utils.py` when parsing LLM output, the URL passed *into* the agent prompt could be crafted to contain prompt-injection payloads.
*Fix:* Apply `sanitize_url(job_url)` before constructing the prompt at line 170.

**[MED-9]** `backend/applier/form_filler.py:399-443` — **Applicant PII (full name, email, phone) inserted into LLM prompt without sanitization**
`_build_fill_prompt` inlines `full_name`, `email`, `phone`, and `location` directly into the Gemini prompt string at lines 405-408 without calling `sanitize_for_prompt`. A user-supplied name like `"John\nIGNORE ALL PREVIOUS INSTRUCTIONS\nDoe"` would pass through unfiltered.
*Fix:* Apply `sanitize_for_prompt(full_name, 200, "full_name")` (and similarly for email, phone, location) before inserting these values into the prompt.

**[MED-10]** `backend/api/analytics.py:77` — **`except Exception: pass` silently swallows avg match score errors**
At line 77, `except Exception: pass` suppresses any database or import error when computing the average match score. If `JobMatch` or the DB query fails for any reason, the analytics response is returned with `avg_match_score=None` and the error is invisible to operators.
*Fix:* Replace `except Exception: pass` with `except Exception as exc: logger.warning("Failed to compute avg_match_score: %s", exc)`.

**[MED-11]** `backend/latex/compiler.py:72-77` — **`asyncio.create_subprocess_exec` has no timeout**
The tectonic compilation subprocess at line 72 has no timeout. A malformed `.tex` file that causes tectonic to loop indefinitely will hang the event loop's executor thread forever. With multiple concurrent CV generations (semaphore cap = 3 in `morning_batch.py`), three such hangs saturate the semaphore.
*Fix:* Wrap `proc.communicate()` in `asyncio.wait_for(proc.communicate(), timeout=120)`, and kill the process on timeout.

**[MED-12]** `frontend/src/routes/cv/+page.svelte:59-67` — **CV "upload" only registers a local filename path, never uploads file content**
`handleFileUpload` constructs a fake server-side path `uploads/${fileName}` and calls `PUT /api/settings/profile` with `base_cv_path: "uploads/${fileName}"`. The actual file content is never sent to the server. The server cannot access `uploads/myfile.tex` because no upload endpoint exists. This is a broken feature that silently succeeds (no error is shown) but leaves the user with a non-functional CV template path.
*Fix:* Implement a `POST /api/settings/cv-upload` multipart endpoint that saves the `.tex` file to `data/templates/` and returns the real server-side path. Update the frontend to upload the file content.

---

## Low — Style & Polish

**[LOW-1]** `backend/models/job.py:13` / `backend/models/user.py:13` / `backend/models/document.py:12` / `backend/models/application.py:12` — **`datetime.utcnow()` is deprecated in Python 3.12**
All four model files use `datetime.utcnow()` as a column default factory. `datetime.utcnow()` returns a naive datetime and is deprecated since Python 3.12; `datetime.now(timezone.utc)` should be used instead.
*Fix:* Replace `def _now(): return datetime.utcnow()` with `def _now(): return datetime.now(timezone.utc)` in each model file and add `from datetime import timezone`.

**[LOW-2]** `backend/main.py:87-88` — **Magic number `daily_limit=10` hardcoded in `ApplicationEngine` constructor**
`ApplicationEngine` is instantiated at line 85-88 with `daily_limit=10` hardcoded. The actual daily limit is stored in `SearchSettings.daily_limit` and loaded at batch time, but the engine's own limit is fixed at 10 regardless of user configuration. If a user sets `daily_limit=20` in settings, the engine still enforces 10.
*Fix:* Read the daily limit from settings at engine construction time, or pass it dynamically when calling `engine.apply()`.

**[LOW-3]** `backend/scraping/orchestrator.py:284` — **`list.index()` O(n) call inside a loop**
`browser_sources.index(source)` at line 284 is called inside the browser sources loop to determine whether to add a delay. `list.index()` is O(n), making the loop O(n²) for large source lists. For the typical handful of browser sources this is inconsequential, but it is a code smell.
*Fix:* Use `enumerate(browser_sources)` and compare the index directly: `for i, source in enumerate(browser_sources): ... if i < len(browser_sources) - 1: ...`.

**[LOW-4]** `backend/api/settings.py:319-323` — **`_mask_email` helper is defined but never called in production paths**
`_mask_email` is defined as a module-level function but is only used inside `get_credentials` (line 455), which imports and calls it inline. The function is not exported in `__all__` and has no docstring.
*Fix:* Add a docstring. Ensure it is consistently used for all email masking — currently the masking at line 457 (`"***@***"`) uses a different format than `_mask_email` would produce.

**[LOW-5]** `backend/models/user.py:50` — **Missing blank line before class definition (PEP 8)**
There is no blank line between the end of `SearchSettings` and the start of `SiteCredential` at line 51.
*Fix:* Add a blank line between the two class definitions.

**[LOW-6]** `backend/applier/form_filler.py:318` — **`context = None` used as an escape hatch instead of a control flag**
Setting `context = None` at line 318 to prevent the `finally` block from closing it is an unusual and fragile pattern. If the code above line 318 is refactored, the intent is easily lost.
*Fix:* Use an explicit boolean flag `keep_open = True` and check `if not keep_open and context:` in the finally block.

**[LOW-7]** `frontend/src/routes/jobs/[id]/+page.svelte:70-77` — **`salary` is a derived function wrapped in `$derived()`, called as `salary()` in template**
`const salary = $derived(() => { ... })` makes `salary` a function wrapped in a derived rune. In the template at line 169, it is called as `salary()`. While this works, the idiomatic Svelte 5 pattern is `const salary = $derived(...)` where `salary` is a value, not `$derived(() => ...)` where `salary` is a callable. The difference matters if `$derived` caches results differently for functions vs values.
*Fix:* Change to `const salary = $derived(computeSalary(job))` where `computeSalary` is a regular function, or use `$derived.by(() => { ... })` if lazy evaluation is needed.

**[LOW-8]** `backend/scraping/site_prompts.py` — **Inconsistent indentation (mixed tabs/spaces and extra blank lines)**
The `SITE_PROMPTS` dict and `SITE_CONFIGS` dict use inconsistent indentation styles — some blocks use 4-space indentation with extra blank lines between key-value pairs (lines 308-478), while the rest of the codebase uses compact 4-space blocks. This is purely cosmetic but makes the file harder to maintain.
*Fix:* Reformat the file with `ruff format` or `black`.

**[LOW-9]** `backend/applier/daily_limit.py:35` — **`date.today()` uses local timezone, not UTC**
`DailyLimitGuard.remaining_today` computes `today = date.today()` (local time) and compares it against `Application.applied_at` which is stored as `datetime.utcnow()` (UTC). If the server runs in a timezone ahead of UTC (e.g. UTC+5), applications submitted before midnight local time but after midnight UTC would be attributed to the wrong day, causing the limit guard to miscalculate remaining slots.
*Fix:* Use `date.today()` consistently with UTC: `today = datetime.utcnow().date()`.
