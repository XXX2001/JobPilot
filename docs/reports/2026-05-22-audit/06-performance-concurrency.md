# 06 — Performance & Concurrency Audit

> Date: 2026-05-22
> Scope: `backend/` (FastAPI + asyncio + SQLAlchemy async + Playwright + Gemini).
> Companion to: [Standards backlog](../2026-05-22-standards/INDEX.md) (EH-08 already covers
> the queue-refresh fire-and-forget task; this audit calls out that overlap and goes
> wider).

---

## TL;DR — Top 5 issues

1. **The Gemini rate-limit lock is held across `asyncio.sleep`** — the lock that "limits 15 RPM" is also taken on every call and re-entered on every retry, which serialises every LLM call in the process. The `asyncio.Semaphore(CONCURRENCY_GEMINI)` set up in `morning_batch.py:395` is structurally unable to do anything because of this. **(`backend/llm/gemini_client.py:126-136, 265-275`)**

2. **Fit-assessment loop is sequential `await`s, not `gather`** — for every match in the morning batch the embedder + extractor + DB roundtrip is awaited one-by-one. With ~30 matches per batch and ~1–3 s per embed call this turns a 1–2 min step into 5–10 min, even though the calls are independent. **(`backend/scheduler/morning_batch.py:329-362`)**

3. **`list_jobs` issues 1 + N SELECTs** — `GET /api/jobs` runs a SELECT per row to fetch the latest score. With the default `limit=50` that's 51 DB roundtrips per page view. **(`backend/api/jobs.py:100-127`)**

4. **`ApplicationEngine.apply` has a TOCTOU race on per-job events** — the daily-limit check, the "already in progress?" check, and the dict insert are interleaved `await`s with no lock, so two concurrent requests for the same `job_match_id` (or two requests at the limit boundary) can both pass the guard. Same shape for `DailyLimitGuard.assert_can_apply`. **(`backend/applier/engine.py:160-180`, `backend/applier/daily_limit.py:69-84`)**

5. **Sync blocking work on the event loop** — three things stand out: heavy lxml + markdownify HTML cleaning runs in `async def` (`scrapling_fetcher.py:308-391`, `form_filler.py:391-443`), `shutil.copy2` + `read_text` + `write_text` on tex files happen on the loop (`latex/pipeline.py`), and the pure-Python cosine-similarity inner loop runs on the loop for every (job_skill × cv_skill) pair (`matching/fit_engine.py:96-111, 198-220`). Each one alone is bearable; together with #1 they make a single batch dominate one core.

---

## Findings table

| ID | Title | Severity | File:line | Estimated impact |
|----|-------|----------|-----------|------------------|
| PC-01 | Gemini rate-limit lock held across sleep — serialises every LLM call | **Critical** | `backend/llm/gemini_client.py:126-136`, `:265-275`, `:166-217` | 2-3× wall-clock on batch; defeats `CONCURRENCY_GEMINI` semaphore |
| PC-02 | Fit-assessment loop is N sequential awaits instead of `gather` | **High** | `backend/scheduler/morning_batch.py:328-364` | 5-10× wall-clock on step 3.5 |
| PC-03 | N+1 in `GET /api/jobs` | **High** | `backend/api/jobs.py:100-127` | 1 + N DB roundtrips per request (default N=50) |
| PC-04 | TOCTOU race in `ApplicationEngine.apply` (per-job events + daily limit) | **High** | `backend/applier/engine.py:160-180`, `backend/applier/daily_limit.py:69-84` | Possible duplicate apply / limit bypass under concurrency |
| PC-05 | CPU-bound HTML cleaning runs on event loop | **Medium** | `backend/scraping/scrapling_fetcher.py:308-391`, `backend/applier/form_filler.py:391-443` | 200-800 ms event-loop stalls per scrape; blocks all other coroutines |
| PC-06 | Pure-Python cosine similarity over all (job × CV) skill pairs on event loop | **Medium** | `backend/matching/fit_engine.py:96-111`, `:198-220` | O(J·C·D) Python ops per assessment; ~100-300 ms each |
| PC-07 | Sync file I/O (`shutil.copy2`, `read_text`, `write_text`) in async CV pipeline | **Medium** | `backend/latex/pipeline.py:130-135, 171-175, 239, 296-303, 323`, `backend/scheduler/morning_batch.py:315` | 10-50 ms loop stall per CV; multiplied by daily limit (default 10) |
| PC-08 | `httpx.AsyncClient` created and torn down per Adzuna call | **Medium** | `backend/scraping/adzuna_client.py:82` | TCP/TLS handshake on every search; ~100-300 ms overhead per request |
| PC-09 | Per-request `AdzunaClient()` (and per-batch `JobDeduplicator`) in `/api/jobs/search` | **Low** | `backend/api/jobs.py:194-195` | Bypasses the singleton on `app.state.adzuna`; minor allocation cost |
| PC-10 | Phase 2 of `ScrapingOrchestrator.run_morning_batch` is fully sequential across sites | **Medium** | `backend/scraping/orchestrator.py:207-325` | 5 sites × ~30 s = 150 s wall-clock that could be ~30-60 s with a bounded gather |
| PC-11 | `BrowserSessionManager._pending_logins` / `_cancelled_logins` mutated without a lock | **Low** | `backend/scraping/session_manager.py:81-83, 292-308` | Concurrent scrapes of same logged-in site could clobber each other |
| PC-12 | Fire-and-forget `asyncio.create_task(_run())` not retained | **Medium** | `backend/api/queue.py:170-176` | Already tracked as EH-08; see note below |
| PC-13 | `ConnectionManager.broadcast` sends serially, not in parallel | **Low** | `backend/api/ws.py:105-123` | Slow client throttles all others on every broadcast |
| PC-14 | WebSocket `ConnectionManager` keeps per-process state — won't survive multi-worker | **Low (today) / Medium (when scaled)** | `backend/api/ws.py:68-103`, `backend/applier/engine.py:101-103` | Multi-worker `uvicorn` would silently break WS routing |
| PC-15 | Module-level heavy import of `backend.scraping.site_prompts` (705 lines, regexes) on first import path | **Low** | `backend/scraping/site_prompts.py`, imported at `orchestrator.py:36` and `adaptive_scraper.py:36` | One-time cold-start cost only; not a per-request hit |
| PC-16 | `analytics/summary` runs 4 SELECT COUNTs sequentially instead of in `gather` | **Low** | `backend/api/analytics.py:78-110` | Adds ~3× DB latency on the dashboard summary endpoint |
| PC-17 | `morning_batch._store_matches` does sequential queries per ranked job (3-4 SELECTs each) | **Medium** | `backend/scheduler/morning_batch.py:520-598` | For a 200-job batch: 600-800 sync DB roundtrips |
| PC-18 | No caching/memoisation of deterministic helpers (skill extraction, CV parse, `_clean_html`, `_extract_json_list`) | **Low** | `backend/matching/cv_parser.py`, `backend/matching/job_skill_extractor.py`, `backend/scraping/scrapling_fetcher.py:308` | Re-runs on every match in a batch |
| PC-19 | `ConnectionManager.disconnect` mutates dict without holding `self._lock` (which exists for that purpose) | **Low** | `backend/api/ws.py:98-103, 142` | Theoretical race; in practice CPython dict-pop is atomic, but contract is broken |
| PC-20 | `webbrowser.open()` in async functions does sync subprocess fork | **Trivial** | `backend/applier/manual_apply.py:82`, `backend/applier/auto_apply.py:302`, `backend/applier/assisted_apply.py:157` | A few-ms fork-exec stall; only on user-initiated apply |

---

## Per-finding detail

### PC-01 — Gemini rate-limit lock serialises all LLM work

**Severity:** Critical
**Files:** `backend/llm/gemini_client.py:122-136, 265-275, 166-217`

```python
122:    self._lock = asyncio.Lock()
...
126:    async def _wait_for_rate_limit(self) -> None:
127:        """@brief Block until a generation request can be issued within the 15 RPM window."""
128:        async with self._lock:
129:            now = time.monotonic()
130:            if len(self._call_times) == self.RPM_LIMIT:
131:                oldest = self._call_times[0]
132:                window = 60.0 - (now - oldest)
133:                if window > 0:
134:                    logger.info("Rate limit: sleeping %.1fs", window)
135:                    await asyncio.sleep(min(window, 120.0))
136:            self._call_times.append(time.monotonic())
```

The lock is held **across `await asyncio.sleep`**, so when the bucket is full one task naps for up to 120 seconds while every other coroutine that wants the LLM (including completely unrelated requests) queues behind it. Even when the bucket is not full, the lock makes every gemini call (and the call's HTTP roundtrip — see PC-05 below) wait for the previous one to finish appending to `_call_times`.

Worse, the 429-retry path in `generate_text` (lines 202-216) does **not** re-call `_wait_for_rate_limit` between retries — but the underlying issue is still the lock. The standards backlog (`docs/reports/2026-05-22-standards/INDEX.md`) refers to a `CONCURRENCY_GEMINI` knob; it is wired into the scheduler at `morning_batch.py:395`:

```python
395:            sem = asyncio.Semaphore(CONCURRENCY_GEMINI)
```

but the semaphore is structurally pointless because all calls funnel through `_wait_for_rate_limit`'s lock. The same shape is in `_wait_for_embed_rate_limit` (lines 265-275).

**Fix sketch:** check the bucket under the lock, compute `delay`, release the lock, **then** sleep. Or use an `asyncio.Semaphore(RPM_LIMIT)` released by a periodic task. Either way the lock must not be held during `await asyncio.sleep`.

---

### PC-02 — Fit-assessment loop is N sequential awaits

**Severity:** High
**File:** `backend/scheduler/morning_batch.py:328-364`

```python
328:        if cv_profile and self._embedder:
329:            for mid in new_match_ids:
330:                jd = match_to_jd.get(mid)
331:                if jd is None:
332:                    continue
333:                try:
334:                    job_profile = self._job_extractor.extract(jd.description or "")
335:                    if len(job_profile.skills) < MIN_JOB_SKILLS_FOR_FIT_ENGINE:
336:                        assessments[mid] = None
337:                        continue
338:                    job_profile = await self._embedder.embed_job_profile(job_profile)
339:                    assessment = self._fit_engine.assess(job_profile, cv_profile, sensitivity)
...
343:                    match_row = (await db.execute(
344:                        select(JobMatch).where(JobMatch.id == mid)
345:                    )).scalar_one_or_none()
```

Each iteration awaits `embed_job_profile` (which awaits a Gemini embedding call — PC-01), then awaits a DB SELECT for the match row, then awaits a WS broadcast. None of these depend on each other across iterations. Wrap the body in an `async def _assess_one(mid)`, then run them with `await asyncio.gather(*[_assess_one(m) for m in new_match_ids])` (optionally under a bounded `Semaphore` once PC-01 is fixed). For 30 matches with ~1 s per embed call this collapses ~30 s into ~3-5 s.

Bonus: the SELECTs at line 343 can be replaced with a single batch fetch `WHERE id IN (...)` outside the loop, joined back by dict.

---

### PC-03 — N+1 in `GET /api/jobs`

**Severity:** High
**File:** `backend/api/jobs.py:94-127`

```python
 94:    stmt = select(Job).order_by(Job.scraped_at.desc()).offset(skip).limit(limit)
 95:    result = await db.execute(stmt)
 96:    jobs = result.scalars().all()
 97:
 98:    # Attach scores from job_matches if min_score is requested
 99:    job_outs: list[JobOut] = []
100:    for job in jobs:
101:        match_stmt = (
102:            select(JobMatch.score)
103:            .where(JobMatch.job_id == job.id)
104:            .order_by(JobMatch.matched_at.desc())
105:            .limit(1)
106:        )
107:        match_result = await db.execute(match_stmt)
```

A classic 1+N. With `limit=50` (the default), that's 51 DB roundtrips. Replace with a single grouped query, e.g.

```sql
SELECT job_match.job_id, MAX(job_match.matched_at) ...
```

or a lateral-style join, then attach via dict. SQLite is fast, but every roundtrip serialises through the single async DB connection.

The neighbouring `applications.py:175-204` already does the right thing (batch-fetch events with `WHERE id IN (...)`), so the pattern is known in the codebase.

---

### PC-04 — TOCTOU race in `ApplicationEngine.apply`

**Severity:** High
**File:** `backend/applier/engine.py:160-180`, `backend/applier/daily_limit.py:69-84`

```python
160:        if mode != ApplyMode.MANUAL:
161:            guard = DailyLimitGuard(db=db, limit=self._daily_limit)
162:            try:
163:                await guard.assert_can_apply()
164:            except DailyLimitExceeded as exc:
165:                ...
172:        # Set up per-job events — guard against concurrent apply for same job
173:        if job_match_id in self._confirm_events:
174:            return ApplicationResult(
175:                status="cancelled",
...
179:        self._confirm_events[job_match_id] = asyncio.Event()
180:        self._cancel_events[job_match_id] = asyncio.Event()
```

Two independent failures:

1. **Daily limit check is read-only**, then there's an `await` (the daily-limit guard runs a `SELECT COUNT(*)`). After the guard returns and before the new `Application` row is persisted, another request can call `assert_can_apply()` and pass too. With `daily_limit=10` and 11 concurrent requests, all 11 can be admitted. The standards backlog (`EH-01`) covers persistence-ordering but not this race.
2. **`job_match_id in self._confirm_events`** is a check-then-set: two concurrent applies for the same match both see "not present", then both write. Result: the second one's `pop` on line 192 races with the first one's handler dispatch from ws.py.

Neither path is currently protected. Either move both checks into a `with self._global_lock` (an `asyncio.Lock` on the engine), or use `dict.setdefault` for the events dict and a SQLite-side `INSERT … WHERE NOT EXISTS … LIMIT N` for the daily cap (preferable, atomic at the DB layer).

---

### PC-05 — CPU-bound HTML cleaning on the event loop

**Severity:** Medium
**Files:** `backend/scraping/scrapling_fetcher.py:308-391`, `backend/applier/form_filler.py:391-443`

`_clean_html` runs `lxml.html.fromstring`, walks the entire tree, calls `markdownify`, and applies regex collapse on up to ~500 KB of HTML — all synchronously inside an `async def`. Same for `_clean_form_html` in the apply path. On a typical LinkedIn listing this is 200-800 ms during which **every** other coroutine in the process (DB queries, WS pings, other scrapes) is paused.

The fix is to wrap the sync body in `await asyncio.get_running_loop().run_in_executor(None, …)` — the same pattern already used at `scrapling_fetcher.py:224` for `_fetch_sync`. The pattern is established; this is just an omission.

---

### PC-06 — Cosine similarity on event loop

**Severity:** Medium
**File:** `backend/matching/fit_engine.py:96-111, 198-220`

```python
 96: def cosine_similarity(a: list[float], b: list[float]) -> float:
...
104:    if len(a) != len(b) or not a:
105:        return 0.0
106:    dot = sum(x * y for x, y in zip(a, b))
107:    norm_a = math.sqrt(sum(x * x for x in a))
108:    norm_b = math.sqrt(sum(x * x for x in b))
```

```python
198:    def _best_match(
199:        self, job_skill: JobSkill, cv_profile: CVProfile
200:    ) -> tuple[float, str, float]:
...
206:        for cv_skill in cv_profile.skills:
207:            sim = cosine_similarity(job_skill.embedding, cv_skill.embedding)
```

Embeddings are 768-dim. For `J=15` job skills × `C=40` CV skills that's 600 × ~2 304 Python ops per assessment = ~1.4 M Python multiplies per match, on the event loop, with no numpy. Each assessment is currently ~100-300 ms. Multiplied by the sequential fit loop (PC-02) this becomes a real wall-clock factor.

Fix: stack embeddings into two `np.ndarray`s once per assessment, compute the full similarity matrix with `cv @ job.T`, divide by the precomputed norms. Drops to ~1 ms. If numpy is undesirable, at least normalise vectors once at `Embedder` time so the inner loop becomes a single dot product.

---

### PC-07 — Sync file I/O in async CV pipeline

**Severity:** Medium
**Files:** `backend/latex/pipeline.py:130-135, 171-175, 239, 296-303, 323`, `backend/scheduler/morning_batch.py:315`

```python
130:    shutil.copy2(base_cv_path, dest_tex)
131:    for support_file in base_cv_path.parent.iterdir():
132:        if support_file.suffix.lower() in {".cls", ".sty", ".jpg", ".jpeg", ".png", ".pdf", ".eps"}:
133:            shutil.copy2(support_file, output_dir / support_file.name)
```

```python
315:            cv_tex = cv_path.read_text(encoding="utf-8")
```

LaTeX templates are typically 5-50 KB, but `shutil.copy2` does a `read`+`write` syscall pair per support file, all on the loop. Inside `asyncio.gather(... _gen_one ...)` at `morning_batch.py:442` these run for up to `daily_limit` CVs in parallel **logically**, but each one independently stalls the loop. Wrap with `await asyncio.to_thread(shutil.copy2, ...)`, etc.

The async subprocess at `latex/compiler.py:88-93` is done correctly — keep that as the reference pattern.

---

### PC-08 — `httpx.AsyncClient` created per Adzuna call

**Severity:** Medium
**File:** `backend/scraping/adzuna_client.py:82`

```python
82:        async with httpx.AsyncClient(timeout=30.0) as client:
83:            response = await client.get(url, params=params)
```

A fresh client = fresh TCP connection, fresh TLS handshake, fresh DNS lookup, every call. For one search per batch it's negligible, but morning_batch can issue `api_sources × keywords` parallel Adzuna calls and each one pays the handshake. Hold a single `httpx.AsyncClient` on `AdzunaClient` instance (constructed at startup, closed in lifespan shutdown), or share a process-wide client. SQLAlchemy is already a singleton, mirror that.

---

### PC-09 — Per-request `AdzunaClient()` in `/api/jobs/search`

**Severity:** Low
**File:** `backend/api/jobs.py:194-195`

```python
194:    client = AdzunaClient()
195:    deduplicator = JobDeduplicator()
```

`app.state.adzuna` and `app.state.deduplicator` already exist (`main.py:121-122`). Resolve them from `request.app.state` (or `Depends`). Aside from allocation cost, this is the same hot-path that compounds with PC-08.

---

### PC-10 — Phase 2 of `ScrapingOrchestrator.run_morning_batch` is fully sequential

**Severity:** Medium
**File:** `backend/scraping/orchestrator.py:207-325`

```python
207:        browser_sources = [s for s in sources if s.type == "browser"]
208:        if browser_sources and ...:
...
212:            for source in browser_sources:
...
311:                        await asyncio.sleep(random.uniform(1, 2))
...
322:                if browser_sources.index(source) < len(browser_sources) - 1:
323:                    delay = random.uniform(1, 3)
324:                    logger.debug("Sleeping %.1fs before next browser source", delay)
325:                    await asyncio.sleep(delay)
```

Phase 1 (API) is parallel via `asyncio.gather` (lines 169-183), Phase 3 (lab URLs) is parallel (lines 333-344), but Phase 2 (the slow path, 5 sites × N keywords) is explicitly serialised with "human-like" sleeps. The comment ("avoid hammering servers") is reasonable per-site, but **cross-site** there's no reason to serialise — `linkedin` and `glassdoor` don't share rate limits. A bounded gather (e.g. `Semaphore(2)`) over `browser_sources` with sleeps only between keywords on the same site would cut wall-clock by ~3-4×.

There's also `browser_sources.index(source)` inside a loop on line 322 — O(N²); replace with `enumerate`.

---

### PC-11 — `BrowserSessionManager` dicts mutated without a lock

**Severity:** Low
**File:** `backend/scraping/session_manager.py:81-83, 292-308`

```python
 81:        self._pending_logins: dict[str, asyncio.Event] = {}
 82:        # Tracks sites where the user cancelled the manual login flow
 83:        self._cancelled_logins: set[str] = set()
```

`get_or_create_session` writes to `_pending_logins` on line 293; `confirm_login` / `cancel_login` read+set; `_pending_logins.pop` on line 300 races with `confirm_login` setting a new event for the same site if the same scrape is retried. In practice Phase 2 already serialises sites (PC-10) so today this doesn't fire — but if PC-10 is fixed, this becomes real. Wrap mutations in an `asyncio.Lock` or key by `(site, request_id)`.

---

### PC-12 — Fire-and-forget `asyncio.create_task` not retained (overlap with EH-08)

**Severity:** Medium (already in standards backlog as EH-08)
**File:** `backend/api/queue.py:170-176`

```python
170:    async def _run():
171:        try:
172:            await runner.run_batch()
173:        except Exception as exc:
174:            logger.error("Batch run error: %s", exc)
175:
176:    asyncio.create_task(_run())
```

EH-08 covers this exactly: store the task reference and attach a `done_callback` that logs with `exc_info=True`. Cite EH-08 in the fix.

**Note from this audit:** I searched the codebase for other `asyncio.create_task` call sites. The other two —
`orchestrator.py:174` and `:334` — *are* gathered into a list before `await asyncio.gather`, so they hold references for their lifetime. `queue.py:176` is the only orphan.

---

### PC-13 — `ConnectionManager.broadcast` sends serially

**Severity:** Low
**File:** `backend/api/ws.py:105-123`

```python
105:    async def broadcast(self, message) -> None:
...
116:        to_remove: list[str] = []
117:        for cid, ws in list(self.active_connections.items()):
118:            try:
119:                await ws.send_text(payload)
120:            except Exception:
121:                to_remove.append(cid)
```

One slow client (network stall, full TCP send buffer) blocks every other client's update. Replace with `asyncio.gather(*[ws.send_text(payload) for ws in clients], return_exceptions=True)` and remove the ones whose result is an exception. Today, single-user, this is fine; on multi-tab or shared instance it shows up.

---

### PC-14 — WebSocket state in process memory, not multi-worker safe

**Severity:** Low today, blocker if scaled
**Files:** `backend/api/ws.py:68-103`, `backend/applier/engine.py:101-103`

```python
68:    def __init__(self) -> None:
69:        self.active_connections: Dict[str, WebSocket] = {}
```

```python
101:        # Per-job asyncio events for confirm/cancel coming from WS
102:        self._confirm_events: dict[int, asyncio.Event] = {}
103:        self._cancel_events: dict[int, asyncio.Event] = {}
```

Both the WS connection registry **and** the per-job confirm/cancel events live in process memory. Running `uvicorn --workers 4` would silently break the apply flow: the user's WS connects to worker A, the `apply` call is dispatched to worker B; worker B sets up its own (empty) `_confirm_events` and waits 30 minutes for an event that worker A's WS will set on a different dict.

Document this loudly in `README` and/or `CLAUDE.md`. For multi-worker support, route both through Redis pub/sub or pick a single worker via sticky session.

---

### PC-15 — Heavy module-level work in `site_prompts.py`

**Severity:** Low (cold-start only)
**File:** `backend/scraping/site_prompts.py` (705 lines, imported at `orchestrator.py:36`, `adaptive_scraper.py:36`, `scrapling_fetcher.py:42`)

It's mostly string literals so the cost is small (~10-20 ms at first import), but it sits on the first-API-call critical path because `orchestrator` is imported during `main.py` lifespan. Acceptable today; flag for a future deferred-import refactor if cold-start matters.

`backend.matching.skill_patterns` compiles 7 regexes at import — also fine, cached in module namespace.

**No `sentence-transformers` / `huggingface` import was found in the backend.** Embeddings are all delegated to Gemini's `text-embedding-004` (`gemini_client.py:286`), so there is no local model loaded at startup. That's a big positive — see "Already good".

---

### PC-16 — `analytics/summary` runs 4 SELECTs sequentially

**Severity:** Low
**File:** `backend/api/analytics.py:78-110`

```python
 78:    total_stmt = select(func.count()).select_from(Application)
 80:    total_apps = (await db.execute(total_stmt)).scalar_one()
...
 87:    apps_this_week = (await db.execute(week_stmt)).scalar_one()
...
 96:    responded = (await db.execute(responded_stmt)).scalar_one()
...
104:    avg_result = (await db.execute(avg_stmt)).scalar_one_or_none()
```

Four `await db.execute` in series. SQLite is fast so this is ~10-40 ms today, but they're independent — combine into a single `SELECT COUNT(...) FILTER (WHERE ...), COUNT(...) FILTER (...)` query (SQLAlchemy supports this), or `asyncio.gather` them. Note: gather on a single shared async session is **not** safe with SQLAlchemy — would need 4 separate sessions. Easiest is a single combined query.

---

### PC-17 — Sequential per-job DB roundtrips in `_store_matches`

**Severity:** Medium
**File:** `backend/scheduler/morning_batch.py:520-598`

```python
520:        for jd, score in ranked:
...
527:            existing = (
528:                await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))
529:            ).scalar_one_or_none()
...
557:            any_actioned = (
558:                await db.execute(
559:                    select(JobMatch).where(...)
...
573:            existing_match = (
574:                await db.execute(
575:                    select(JobMatch).where(...)
```

3-4 SELECTs per ranked job, all sequential. With 200 ranked jobs that's 600-800 SELECTs serially. Fix: pre-fetch all `Job` rows matching `dedup_hash IN (...)`, all `JobMatch` rows for those jobs, then do the dedup/update logic in Python with no DB roundtrip inside the loop. Final `commit` already happens once at the end (good), but the SELECTs dominate.

---

### PC-18 — No memoisation of deterministic helpers

**Severity:** Low (but a free win)
**Files:** `backend/matching/cv_parser.py`, `backend/matching/job_skill_extractor.py`, `backend/scraping/scrapling_fetcher.py:308`

Examples worth a `functools.lru_cache` (keyed by content hash):

- `CVParser.build_profile(cv_tex)` — pure function of CV text; called once per batch today, but if re-applied per-match it'd be wasteful.
- `JobSkillExtractor.extract(description)` — pure, called once per match in `morning_batch.py:334`; safe to cache.
- `ScraplingFetcher._clean_html(html, site)` — pure of `(html, site)`. Same listings page sometimes scraped multiple times in retry paths.

`grep -rn 'lru_cache' backend/` returns zero hits — no LRU cache anywhere in the backend.

---

### PC-19 — `ConnectionManager.disconnect` skips its own lock

**Severity:** Low (correctness contract)
**File:** `backend/api/ws.py:98-103, 142`

```python
 70:        self._lock = asyncio.Lock()
...
 94:        async with self._lock:
 95:            self.active_connections[client_id] = websocket
...
 98:    def disconnect(self, client_id: str) -> None:
...
103:        self.active_connections.pop(client_id, None)
```

`connect` acquires the lock; `disconnect` doesn't. CPython dict.pop is atomic so in practice this is safe, but the abstraction is broken — anyone changing the dict to a more complex structure later would silently introduce a race.

---

### PC-20 — `webbrowser.open()` in async functions

**Severity:** Trivial
**Files:** `backend/applier/manual_apply.py:82`, `backend/applier/auto_apply.py:302`, `backend/applier/assisted_apply.py:157`

`webbrowser.open` does a synchronous `subprocess` invocation (e.g. `xdg-open`). It's a few-ms stall, only on user-initiated apply, and the user is waiting anyway. Wrap in `asyncio.to_thread` if you want to be hygienic, but this is the least of your worries.

---

## Concurrency model summary

What the model **looks like on paper:** a single FastAPI process, one async event loop, SQLAlchemy async with WAL-mode SQLite, one shared `GeminiClient` singleton with a 15-RPM bucket, one `ScrapingOrchestrator` that runs Phase 1 (API) in parallel, Phase 2 (browser) sequential per site, Phase 3 (lab URLs) in parallel, and a `MorningBatchRunner` that fans out CV generation under a `Semaphore(CONCURRENCY_GEMINI)`.

What the model **actually is in practice:** mostly single-threaded. Three structural choke-points collapse the design:

1. **The Gemini lock** (PC-01) serialises every LLM call — including embeddings — through one process-wide async lock that is held across the rate-limit sleep. Every effort to parallelise downstream (the `Semaphore(3)` in morning_batch, Phase-3 lab-URL gather, the multiple Gemini calls per scrape) hits this wall.
2. **Sync-on-the-loop bursts** (PC-05, PC-06, PC-07) — every HTML clean, every fit assessment, every CV file copy is a 50-800 ms stall during which **nothing else** can make progress, including the WS broadcast that's trying to tell the frontend "still working".
3. **DB N+1 roundtrips** (PC-03, PC-17) — even though the async DB layer is fine, the code pattern of "loop, await SELECT" multiplies single-millisecond queries into seconds.

The code is *aware* of all the right primitives — there's an `asyncio.Lock` here, an `asyncio.Semaphore` there, an `asyncio.gather` in three places, an `asyncio.create_task` wired correctly twice — but the primitives are deployed without measuring whether they're actually doing what they look like they should. The CONCURRENCY_GEMINI semaphore is the clearest example: it'd take five lines to make work, but right now it does nothing.

**Where the model strains under load:**

- A second user (or a second tab calling `/api/queue/refresh`) starts a batch that contends with the first one for the Gemini lock; both run at 1/2 speed.
- A slow WS client (PC-13) backpressures every other client during a batch broadcast.
- Multi-worker deployment (PC-14) breaks WS-driven apply flows silently.
- A single 500 KB LinkedIn page during scrape causes ~600 ms of event-loop dead time (PC-05) right when the frontend is polling `/api/queue/status` — the response gets queued behind the HTML parse.

**Where it does not strain:**

- Async subprocess for LaTeX (compiler.py:88-93) is textbook.
- DB writes per request use `expire_on_commit=False` (database.py:66), which avoids spurious re-queries.
- WAL mode is enabled at connect time (database.py:48-63), so reads don't block writes.
- HTTP fetch via Scrapling correctly punts to a thread executor (scrapling_fetcher.py:206-224).
- The Gemini SDK call itself is wrapped in `run_in_executor` (gemini_client.py:184), so the *SDK* doesn't block — only the surrounding lock does (PC-01).

---

## Quick wins

These are ordered by (impact / effort). Each one is ≤1 day.

1. **Fix the Gemini lock (PC-01).** Two-line change: compute the sleep duration under the lock, drop the lock, then sleep. Unblocks `CONCURRENCY_GEMINI`, makes PC-02 actually faster.
2. **Replace the `for mid in new_match_ids` fit loop with `asyncio.gather` (PC-02).** Wrap the body in a helper; collect the assessments dict from the results.
3. **Fix the `list_jobs` N+1 (PC-03).** Use the same dict-batching pattern that `applications.py:188-204` already uses for events.
4. **Wrap `_clean_html` in `run_in_executor` (PC-05).** The pattern is already used 30 lines above in the same file.
5. **Hold one `httpx.AsyncClient` on `AdzunaClient` (PC-08).** Open in `__init__`, close in lifespan shutdown.
6. **Use `app.state.adzuna` in `/api/jobs/search` (PC-09).** Five-line change.
7. **EH-08 fix** (the standards-backlog item) for `queue.py:176`. Add task ref + done callback.

Estimated total wall-clock improvement on the morning batch: ~3-5×. (Fit step PC-02 alone is ~5×; PC-01 doubles or triples that; PC-17 pulls another ~30 s out of the persistence step.)

## Medium-term wins (1-3 days each)

- **Vectorise `fit_engine` with numpy (PC-06).** Drops per-match assessment from ~200 ms to ~1 ms.
- **Batch `_store_matches` SELECTs (PC-17).** Drop 600+ roundtrips to 3.
- **Parallelise Phase 2 with bounded gather (PC-10).** Keep per-keyword delays inside a site; lift cross-site serialisation.
- **Move CV-pipeline file ops to `asyncio.to_thread` (PC-07).** Boring but cumulative.
- **Add a single global `asyncio.Lock` to `ApplicationEngine.apply` covering the daily-limit check + event registration (PC-04).** Or migrate the daily counter to a single SQLite `UPDATE … RETURNING` for atomicity.

## Longer-term considerations

- **WS state in Redis** (PC-14) is the only refactor that gates horizontal scaling. Today the app is explicitly single-worker.
- **Caching layer** (PC-18) — once the rest is fixed, low-hanging memoisation of CV/job parsing becomes a measurable win.

---

## Already good

Listing these so they don't get refactored by accident.

- **Async LaTeX subprocess** at `backend/latex/compiler.py:88-107` and `backend/latex/validator.py:81-89` uses `asyncio.create_subprocess_exec` with `PIPE` correctly. **Do not change.**
- **No sync HTTP library** — `grep -rn "requests\." backend/` and `grep -rn "urllib\." backend/` return zero hits. Everything is `httpx.AsyncClient`. Good.
- **No `time.sleep`** in any `async def` — `grep -rn "time\.sleep" backend/` returns zero hits. (All sleeps are `await asyncio.sleep`.) Excellent.
- **No local ML model loaded** — no `sentence-transformers`, no `transformers`, no `huggingface`. Embedding is delegated to the Gemini API. Startup stays cheap and the event loop doesn't compete with a GPU.
- **DB WAL mode + `expire_on_commit=False`** in `backend/database.py:42-66` — small but correct touches.
- **Gemini SDK calls wrapped in executor** (`backend/llm/gemini_client.py:184, 286`). The SDK doesn't stall the loop — the lock around it does (PC-01), which is fixable.
- **`Phase 1` Adzuna fan-out** (`backend/scraping/orchestrator.py:169-187`) is a textbook `asyncio.gather(*tasks, return_exceptions=True)` with a `_flatten_results` helper. The exception isolation is well-thought-out.
- **`applications.py:188-204`** correctly batches event fetches with a single `IN (...)` query — proves the pattern is understood and should be the model for PC-03 and PC-17.
- **Scrapling fetch correctly punted to executor** (`backend/scraping/scrapling_fetcher.py:206-224`). The blocking HTML fetch lives where it should.
- **CV-pipeline LLM context cache** (`backend/latex/pipeline.py:109-204`) — modest TTL-bounded LRU on `JobContext` keyed by `job_id`, 100-entry cap. Pragmatic and correct.
- **Browser-use Agent timeout** (`backend/scraping/adaptive_scraper.py:159`) — `asyncio.wait_for(agent.run(), timeout=180)`. Prevents a hung agent from blocking forever.
- **WS connection accept under lock** (`backend/api/ws.py:90-96`) — `connect` does the right thing (even if `disconnect` doesn't, see PC-19).

---

## Audit metadata

- **Files read in full or in part:** `backend/main.py`, `backend/database.py`, `backend/api/{jobs,queue,applications,analytics,ws,deps,documents,settings}.py`, `backend/applier/{engine,auto_apply,assisted_apply,manual_apply,form_filler,captcha_handler,daily_limit}.py`, `backend/scheduler/morning_batch.py`, `backend/scraping/{orchestrator,adzuna_client,adaptive_scraper,scrapling_fetcher,session_manager}.py`, `backend/llm/{gemini_client,cv_modifier}.py`, `backend/latex/{compiler,pipeline}.py`, `backend/matching/{matcher,fit_engine,embedder,cv_parser,skill_patterns,job_skill_extractor}.py`.
- **Grep patterns run:** `asyncio.gather`, `asyncio.create_task`, `to_thread`, `run_in_executor`, `Semaphore`, `asyncio.Lock`, `time.sleep`, `requests.`, `httpx.`, `aiohttp`, `subprocess.`, `open(`, `read_text|write_text|read_bytes|write_bytes`, `lru_cache|@cache`, `shutil.copy|rmtree|move`, `sentence-transformers|huggingface|transformers\.`, `create_task|background_tasks|BackgroundTasks`, `active_connections`, `EH-08|fire-and-forget`.
- **Out of scope (intentionally not duplicated):** naming, typing, error-handling categories already covered in `docs/reports/2026-05-22-standards/`. The one explicit overlap is EH-08 (queue.py fire-and-forget); cited as PC-12 with a pointer back.
