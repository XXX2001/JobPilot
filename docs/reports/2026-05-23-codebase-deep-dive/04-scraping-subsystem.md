# 04 — Scraping Subsystem

> Scope: `backend/scraping/*` and `backend/utils/*` (scraping-adjacent).
> Total: 9 Python files, ~2,470 LOC, plus 1 util (`browser_path.py`).
> Date: 2026-05-23.

---

## 1. Purpose

The scraping subsystem is the **discovery half** of JobPilot. Its single job is to take the user's saved keywords + filters + enabled sources (LinkedIn, Indeed, Welcome to the Jungle, Glassdoor, Google Jobs, Adzuna API, plus arbitrary "lab URL" career pages) and return a deduplicated list of `RawJob` objects to the rest of the pipeline.

The output is consumed exclusively by the `BatchRunner` in [scheduler/batch_runner.py:194](backend/scheduler/batch_runner.py#L194), which:

1. Calls `ScrapingOrchestrator.scrape_batch(...)` once per user-triggered scan.
2. Ranks the returned raw jobs through `JobMatcher`.
3. Persists survivors as `Job` + `JobMatch` rows.
4. Pre-generates tailored CVs for the top-N of those matches.

There is **one entry point** into scraping — `scrape_batch` — invoked from exactly one HTTP route: `POST /api/queue/refresh` in [api/queue.py:132](backend/api/queue.py#L132). No cron, no on-page event hooks, no other consumers.

The subsystem also owns persistent browser sessions (`browser_profiles/<site>/state.json`) so the user only has to log in to LinkedIn / Indeed once.

---

## 2. Architecture

```
                                    POST /api/queue/refresh
                                              │
                                              ▼
                              BatchRunner.run_batch()              [scheduler/batch_runner.py]
                                              │
                                              ▼
                           ScrapingOrchestrator.scrape_batch()      [scraping/orchestrator.py]
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              ▼                               ▼                               ▼
     Phase 1: API sources         Phase 2: Browser sources         Phase 3: Lab URL sources
       (parallel asyncio)          (sequential, human delay)         (parallel asyncio)
              │                               │                               │
              ▼                               ▼                               ▼
         AdzunaClient        ┌── Tier 1: ScraplingFetcher ──┐         AdaptiveScraper
       (httpx REST API)      │  • StealthyFetcher / Fetcher │     (browser-use + Gemini)
                             │  • _clean_html → markdown    │
                             │  • 1 Gemini extraction call  │
                             └──────── if empty/error ──────┘
                                              │
                                              ▼
                             Tier 2: AdaptiveScraper (full browser-use Agent loop)

                                              │
                                              ▼
                                BrowserSessionManager.get_or_create_session()
                                  (only for sites with requires_login=True)
                                              │
                                              ▼
                              data/browser_profiles/<site>/state.json
```

**Core classes** (with line refs):

| Class | File | LOC |
|---|---|---|
| `ScrapingOrchestrator` | [scraping/orchestrator.py:75](backend/scraping/orchestrator.py#L75) | 373 |
| `ScraplingFetcher` (Tier 1) | [scraping/scrapling_fetcher.py:36](backend/scraping/scrapling_fetcher.py#L36) | 384 |
| `AdaptiveScraper` (Tier 2) | [scraping/adaptive_scraper.py:26](backend/scraping/adaptive_scraper.py#L26) | 295 |
| `BrowserSessionManager` | [scraping/session_manager.py:28](backend/scraping/session_manager.py#L28) | 434 |
| `AdzunaClient` | [scraping/adzuna_client.py:18](backend/scraping/adzuna_client.py#L18) | 77 |
| `JobDeduplicator` | [scraping/deduplicator.py:9](backend/scraping/deduplicator.py#L9) | 29 |

All scraping singletons are constructed once at app startup in [main.py:127-138](backend/main.py#L127) and stored on `app.state`; the orchestrator is reused across batch runs.

**Scrapling integration** lives only in `ScraplingFetcher`. The library is loaded lazily inside a thread-pool `_fetch_sync` closure ([scraping/scrapling_fetcher.py:169](backend/scraping/scrapling_fetcher.py#L169)) — Scrapling exposes a synchronous API, so it's wrapped via `loop.run_in_executor()`. `StealthyFetcher` (Patchright-backed) is used for the four anti-bot sites in `_STEALTHY_SITES = {"linkedin", "indeed", "glassdoor", "google_jobs"}` ([scraping/scrapling_fetcher.py:31](backend/scraping/scrapling_fetcher.py#L31)); the plain `Fetcher` (httpx-like) handles WTTJ and unknown sites.

---

## 3. Site Adapters

There are **no first-class adapter classes** — adapters are encoded as data: an entry in `SITE_CONFIGS`, a search-URL builder branch in `ScraplingFetcher._build_search_url`, an optional content-scope CSS selector in `SITE_CONTENT_SELECTORS`, and a Gemini prompt in `EXTRACTION_PROMPTS` (Tier 1) or `SITE_PROMPTS` (Tier 2). This is mostly OK but makes per-source testing very hard (see §9).

### 3.1 Adzuna (API, Phase 1)

- **Endpoint:** `https://api.adzuna.com/v1/api/jobs/{country}/search/{page}` ([scraping/adzuna_client.py:21](backend/scraping/adzuna_client.py#L21))
- **Auth:** `app_id` + `app_key` from `settings.ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (env). Free tier = 250 calls/day.
- **Pagination:** request only ever asks for `page=1` — [scraping/adzuna_client.py:33](backend/scraping/adzuna_client.py#L33). No pagination is implemented. `results_per_page` defaults to 20.
- **Selectors / params:** `what` (keywords), `where` (`filters.locations[0]`), `salary_min`, `full_time=1` (only if `"full-time" in filters.job_types`), `max_days_old`.
- **Politeness:** none — single GET per country. 30 s httpx timeout.
- **Normalization to `RawJob`:** [scraping/adzuna_client.py:64](backend/scraping/adzuna_client.py#L64) `_parse_job` maps Adzuna fields → `RawJob(source_name="adzuna", ...)`. `country` is force-set after parse at [scraping/adzuna_client.py:60-61](backend/scraping/adzuna_client.py#L60).

### 3.2 LinkedIn (browser, Tier 1 → Tier 2 fallback)

- **Endpoint:** `https://www.linkedin.com/jobs/search/?keywords=…&location=…&f_TPR=r{seconds}&sortBy=DD` ([scraping/scrapling_fetcher.py:224](backend/scraping/scrapling_fetcher.py#L224))
- **Auth:** `requires_login=True` ([site_prompts.py:316](backend/scraping/site_prompts.py#L316)). Session lives in `data/browser_profiles/linkedin/state.json`. Optional auto-login via stored `SiteCredential` ([session_manager.py:330](backend/scraping/session_manager.py#L330)).
- **Pagination:** none — only first page.
- **Selectors:** Tier 1 scopes to `.jobs-search-results-list, .scaffold-layout__list` ([site_prompts.py:585](backend/scraping/site_prompts.py#L585)). Tier 1 Gemini prompt instructs the LLM to construct `apply_url` from job IDs in `data-job-id` / `data-entity-urn` ([site_prompts.py:615](backend/scraping/site_prompts.py#L615)).
- **Rate limiting:** 1-2 s sleep between keyword searches ([orchestrator.py:286](backend/scraping/orchestrator.py#L286)), 1-3 s between sites ([orchestrator.py:298](backend/scraping/orchestrator.py#L298)). That's it.
- **Normalization:** Tier 1 → `parse_jobs_from_json(..., source_name="scrapling")` ([scrapling_fetcher.py:110](backend/scraping/scrapling_fetcher.py#L110)); Tier 2 → `parse_jobs_from_json(..., source_name="browser")` ([adaptive_scraper.py:259](backend/scraping/adaptive_scraper.py#L259)).

### 3.3 Indeed (browser, Tier 1 → Tier 2 fallback)

- **Endpoint:** `https://{fr,uk,de,…}.indeed.com/jobs?q=…&l=…&fromage=…` ([scraping/scrapling_fetcher.py:234](backend/scraping/scrapling_fetcher.py#L234)). Per-country domain mapping is duplicated between `_INDEED_DOMAINS` in `scrapling_fetcher.py:213` and `site_prompts.py:508` — see Critique §9.
- **Auth:** `requires_login=False` ([site_prompts.py:342](backend/scraping/site_prompts.py#L342)) but `_attempt_auto_login` has a flow ([session_manager.py:355](backend/scraping/session_manager.py#L355)). Login is optional.
- **Pagination:** none.
- **Selectors:** `#mosaic-jobResults, .jobsearch-ResultsList` ([site_prompts.py:586](backend/scraping/site_prompts.py#L586)).
- **Rate limiting:** same shared inter-keyword/inter-site sleeps.
- **Normalization:** identical to LinkedIn.

### 3.4 Welcome to the Jungle (browser, Tier 1 → Tier 2)

- **Endpoint:** `https://www.welcometothejungle.com/en/jobs?query=…&refinementList[offices.country_reference_code][0]={CC}` ([scraping/scrapling_fetcher.py:257](backend/scraping/scrapling_fetcher.py#L257))
- **Auth:** none required.
- **Pagination:** none.
- **Selectors:** `[data-testid='search-results-list-item-wrapper']` ([site_prompts.py:588](backend/scraping/site_prompts.py#L588)). Uses plain `Fetcher` (not Stealthy) — WTTJ doesn't aggressively block.
- **Date filter:** not URL-supported; relies on post-scrape filter in orchestrator ([orchestrator.py:334](backend/scraping/orchestrator.py#L334)).
- **Normalization:** standard via `parse_jobs_from_json`.

### 3.5 Glassdoor France (browser, Tier 1 → Tier 2)

- **Endpoint:** `https://www.glassdoor.fr/Emploi/emplois.htm?typedKeyword=…&locT=N&locId=0` ([scraping/scrapling_fetcher.py:264](backend/scraping/scrapling_fetcher.py#L264)). Hard-coded `.fr` — `country_codes: ["fr"]` only in `SITE_CONFIGS` ([site_prompts.py:422](backend/scraping/site_prompts.py#L422)).
- **Auth:** no login required; signup modal dismissed in the prompt.
- **Pagination:** none. URL has no date param — recency handled by post-scrape filter (acknowledged inline at [scrapling_fetcher.py:266](backend/scraping/scrapling_fetcher.py#L266)).
- **Selectors:** `[data-test='jobListing'], .react-job-listing, .JobsList_jobListItem, .JobsList_wrapper` ([site_prompts.py:590](backend/scraping/site_prompts.py#L590)) — four fallbacks, suggesting selectors break often.
- **Normalization:** dedicated Gemini extraction prompt enforces absolute URLs ([site_prompts.py:635](backend/scraping/site_prompts.py#L635)).

### 3.6 Google Jobs (browser, Tier 1 → Tier 2)

- **Endpoint:** `https://{google_domain}/search?q={kw}+emplois+{loc}&udm=8` ([scraping/scrapling_fetcher.py:241](backend/scraping/scrapling_fetcher.py#L241)). The `udm=8` parameter is Google's jobs vertical; the literal `emplois` keyword is hard-coded — works in French / Latin-language SERPs but biases English-speaking queries.
- **Auth:** none.
- **Date filter:** `&chips=date_posted:today|3days|week|month` ([scrapling_fetcher.py:247-254](backend/scraping/scrapling_fetcher.py#L247)).
- **Selectors:** `#search, #rso, .MjjYud` ([site_prompts.py:587](backend/scraping/site_prompts.py#L587)). Google class names rotate constantly — very brittle.
- **Special handling:** `_clean_html` promotes `data-share-url` → `href` at [scrapling_fetcher.py:335-340](backend/scraping/scrapling_fetcher.py#L335) so markdownify retains the share link.
- **Coverage:** the only per-source test in the repo is `tests/test_google_jobs_scraping.py` (12.6 KB) — Google Jobs is the canary site.

### 3.7 Lab / custom URLs (Phase 3)

- **Endpoint:** whatever the user pastes into `JobSource.url` (`type='lab_url'`).
- **Pipeline:** skips Tier 1 entirely — goes straight to `AdaptiveScraper.scrape_job_listings()` with the `lab_website` prompt ([site_prompts.py:235](backend/scraping/site_prompts.py#L235)).
- **Auth:** none. **Pagination:** none. **Selectors:** none (LLM-driven).
- **Concurrency:** all lab sources run in parallel via `asyncio.gather` ([orchestrator.py:309-320](backend/scraping/orchestrator.py#L309)).

---

## 4. Browser Session Management

[session_manager.py:28](backend/scraping/session_manager.py#L28) — `BrowserSessionManager`.

**Storage layout (two layouts, both supported):**

- Canonical: `data/browser_profiles/<site>/state.json` (Playwright storage_state JSON: cookies + localStorage).
- Legacy: `data/browser_sessions/<site>_state.json` — read for backward compat, never written. `list_sessions()` walks both ([session_manager.py:166-197](backend/scraping/session_manager.py#L166)).

**Flow for a known site** ([session_manager.py:75-79](backend/scraping/session_manager.py#L75)):
If `state.json` exists → return `None` and let `AdaptiveScraper` / `ScraplingFetcher` load the file directly. No `Browser` instance is created up-front. This is a deliberate optimisation — saves a process spawn — but means the "session" abstraction is mostly a file-existence check.

**Flow for an unknown site** ([session_manager.py:84-136](backend/scraping/session_manager.py#L84)):

1. Try `_attempt_auto_login()` using stored `SiteCredential` (only LinkedIn/Indeed have flows, [session_manager.py:266](backend/scraping/session_manager.py#L266)).
2. If auto-login fails or unavailable, open a headful browser (`headless=False` hard-coded at [session_manager.py:102](backend/scraping/session_manager.py#L102)).
3. Navigate to the site's `login_url` from `SITE_CONFIGS`.
4. Broadcast a `LoginRequired` WS message ([session_manager.py:228](backend/scraping/session_manager.py#L228)).
5. Block on an `asyncio.Event` waiting for the frontend to POST `login_done`, up to **10 minutes** ([session_manager.py:244](backend/scraping/session_manager.py#L244)).
6. On `confirm_login(site)` ([session_manager.py:138](backend/scraping/session_manager.py#L138)): call `browser.stop()` — the browser-use watchdog dispatches `SaveStorageStateEvent` which writes `state.json`.
7. On `cancel_login(site)`: same event fires but `_cancelled_logins` flag causes `_request_login` to raise `RuntimeError` ([session_manager.py:253-257](backend/scraping/session_manager.py#L253)).

**Headless config:** controlled by `settings.jobpilot_scraper_headless` (consumed by `ScraplingFetcher` at [scrapling_fetcher.py:139](backend/scraping/scrapling_fetcher.py#L139) and `AdaptiveScraper` at [adaptive_scraper.py:120](backend/scraping/adaptive_scraper.py#L120)). Manual-login and auto-login flows force `headless=False` regardless, as they require user interaction or aim to evade headless fingerprinting.

**Cookie loading:** `ScraplingFetcher.fetch_page` reads `state.json`, strips Playwright-style object `partitionKey` (Scrapling/Patchright would reject it) at [scrapling_fetcher.py:155-164](backend/scraping/scrapling_fetcher.py#L155), and feeds the cleaned cookie list to `StealthyFetcher.fetch(url, cookies=…)`.

**Chromium binary resolution:** [utils/browser_path.py:16](backend/utils/browser_path.py#L16) `get_chromium_executable()` — spawns a `subprocess.run([sys.executable, "-c", …])` once per process to ask patchright/playwright where their bundled Chromium lives. Result is cached. Reasonable but heavyweight on first call.

---

## 5. Orchestration

[scraping/orchestrator.py:101](backend/scraping/orchestrator.py#L101) `scrape_batch()` runs a strict **three-phase** pipeline with **no inter-phase cancellation**:

**Phase 1 — API (parallel).** One `asyncio.create_task` per `JobSource` of `type='api'`, gathered with `return_exceptions=True`. If no API sources are explicitly configured but `keywords` exist, falls back to a single default Adzuna call ([orchestrator.py:164-178](backend/scraping/orchestrator.py#L164)). Progress: `broadcast_status(..., progress=0.1 → 0.3)`.

**Phase 2 — Browser (sequential).** Iterates `browser_sources` one at a time. For each source:

1. Lazily kick the session manager (only if `requires_login`) at [orchestrator.py:193](backend/scraping/orchestrator.py#L193).
2. Loop over keywords **one at a time**, splitting `max_results_per_source` across them at [orchestrator.py:214](backend/scraping/orchestrator.py#L214). Comment justifies this: "to avoid overly-specific combined queries that return 0 results."
3. **Tier 1 → Tier 2 cascade** at [orchestrator.py:222-273](backend/scraping/orchestrator.py#L222): try `ScraplingFetcher` for `TIER1_SITES`; if it returns `[]` or raises, fall back to `AdaptiveScraper`. Tier 2 is the only path for non-Tier-1 sites.
4. Sleep `random.uniform(1, 2)` between keywords, `random.uniform(1, 3)` between sites.

Progress: `0.35 → 0.5`. A single failing keyword/site is logged and skipped — never re-raised.

**Phase 3 — Lab URLs (parallel).** `asyncio.gather` across all `type='lab_url'` sources, all routed through Tier 2 `AdaptiveScraper` ([orchestrator.py:309-320](backend/scraping/orchestrator.py#L309)). Progress: `0.6 → 0.75`.

**Post-scrape:**
- Date filter sweep at [orchestrator.py:334-341](backend/scraping/orchestrator.py#L334) — removes jobs with `posted_at < now - max_age_days`. Jobs with `posted_at=None` are kept (cannot be filtered).
- `JobDeduplicator.deduplicate(all_jobs)` ([orchestrator.py:346-348](backend/scraping/orchestrator.py#L346)).
- Final progress broadcast `0.8` (the remaining 0.2 belongs to ranking + CV pre-gen in `BatchRunner`).

**Concurrency model summary:**
- API: unbounded parallel `asyncio.gather`.
- Browser: **fully sequential** (each site, each keyword).
- Lab URLs: unbounded parallel `asyncio.gather`.
- No semaphore, no concurrency cap. With 5 browser sources × 3 keywords each and Tier 1 ≈ 15 s + Tier 2 ≈ 60–180 s per keyword as fallback, **a full Phase 2 can legitimately take 5–15 minutes**.

---

## 6. Scrapling vs raw httpx vs browser-use

| Tool | Where | When | Why |
|---|---|---|---|
| **httpx** | `AdzunaClient` ([adzuna_client.py:52](backend/scraping/adzuna_client.py#L52)) | Adzuna REST API | Pure JSON API — no rendering needed, ~250 ms per call. |
| **Scrapling `Fetcher`** | `ScraplingFetcher.fetch_page` (non-stealthy branch, [scrapling_fetcher.py:178](backend/scraping/scrapling_fetcher.py#L178)) | WTTJ + unknown Tier 1 sites | Lightweight httpx-like HTTP fetch, no browser. |
| **Scrapling `StealthyFetcher`** | `ScraplingFetcher.fetch_page` (stealthy branch, [scrapling_fetcher.py:171-176](backend/scraping/scrapling_fetcher.py#L171)) | LinkedIn, Indeed, Glassdoor, Google Jobs | Patchright-patched Chromium under the hood; loads cookies; evades headless / WebDriver fingerprints. Synchronous API, wrapped via `run_in_executor`. |
| **browser-use `Agent` + Gemini** | `AdaptiveScraper.scrape_job_listings` ([adaptive_scraper.py:48](backend/scraping/adaptive_scraper.py#L48)) | Tier 2 fallback for known sites + all lab URLs | LLM-driven navigation; no selectors. Capped at `max_steps=20`, 180 s timeout, 2 retries with exponential backoff (2 s, 4 s). Each Agent step is one Gemini call. |
| **browser-use raw `Browser`** | `BrowserSessionManager` ([session_manager.py:105](backend/scraping/session_manager.py#L105), [session_manager.py:314](backend/scraping/session_manager.py#L314)) | Manual login flow + auto-login flow | Persists `state.json` on graceful `stop()`. |

**Decision flow per site/keyword in Phase 2** ([orchestrator.py:222-273](backend/scraping/orchestrator.py#L222)):

```
site in TIER1_SITES?
  ├── yes: ScraplingFetcher
  │       returns >0 jobs? ──► done
  │       returns 0 or raises? ──► AdaptiveScraper (Tier 2 fallback)
  └── no:  AdaptiveScraper (Tier 2 only)
```

The cost trade-off (per file docstring at [scrapling_fetcher.py:1-8](backend/scraping/scrapling_fetcher.py#L1)): Tier 1 reduces ~20 Gemini calls per keyword down to 1, and ~60–180 s wall-clock down to ~10–30 s. Tier 2 fallback exists because Scrapling sometimes hits captchas / 0-result pages on LinkedIn.

---

## 7. Data Normalization & Dedup

**Schema target:** `RawJob` (pydantic, in `backend.models.schemas`).

**Three sites of normalization:**

1. **Adzuna** — direct mapping in [adzuna_client.py:64-77](backend/scraping/adzuna_client.py#L64). `source_name="adzuna"`. Country forced from request param.
2. **Tier 1 / Tier 2 browser** — both funnel through [`json_utils.parse_jobs_from_json()`](backend/scraping/json_utils.py#L103) at [json_utils.py:103](backend/scraping/json_utils.py#L103). This is the single point where:
   - Relative `apply_url` is `urljoin`-ed with `source_url` ([json_utils.py:139-144](backend/scraping/json_utils.py#L139)).
   - URLs are passed through `sanitize_url()` and text fields through `sanitize_for_prompt()` (from `backend.security.sanitizer`) — defending against prompt-injection downstream.
   - `posted_date` is parsed via `_parse_posted_date()` ([json_utils.py:18-57](backend/scraping/json_utils.py#L18)) — handles ISO formats, "X days/weeks/hours ago" in English and French ("jour", "semaine", "heure"), and "today/yesterday".
   - Source name is set per-tier (`"scrapling"` vs `"browser"`).
3. **Country backfill** — `orchestrator.py:277-278` (Phase 2) and `orchestrator.py:322-324` (Phase 3) post-fill `job.country` from the source's config / location string when the LLM didn't return one.

**Dedup key** — [deduplicator.py:12-17](backend/scraping/deduplicator.py#L12):

```python
key = md5(f"{normalized_company}|{normalized_title}|{normalized_location}")
```

Where `_norm` lowercases and collapses whitespace. **MD5 of (company, title, location).** No URL, no description, no posted_at. Hashing collisions are extremely unlikely for the input space but the strategy is fragile (see §9).

**Conflict handling** — [deduplicator.py:25-28](backend/scraping/deduplicator.py#L25): when the same key reappears, the job with the **longer description** wins, on the assumption that a richer payload came from a detail-page render rather than a sparse search-card extraction. Salary, posted_at, apply_url are silently overwritten by the winner; there's no field-level merge.

**Cross-batch dedup** — happens later in `BatchRunner._store_matches` ([batch_runner.py:453-490](backend/scheduler/batch_runner.py#L453)) using the **same MD5 hash** stored as `Job.dedup_hash`. Existing rows are updated with longer description / non-empty apply_url ([batch_runner.py:472-475](backend/scheduler/batch_runner.py#L472)).

---

## 8. Error Handling & Resilience

**Per-site failure isolation: GOOD.** `_flatten_results` ([orchestrator.py:63-71](backend/scraping/orchestrator.py#L63)) explicitly discards `Exception` items from `asyncio.gather(..., return_exceptions=True)` and only extends with `list` results. Phase 2's outer `try/except` at [orchestrator.py:294-295](backend/scraping/orchestrator.py#L294) catches anything that escapes the inner per-keyword try. **One bad source never breaks a batch.**

**Empty-result handling: SILENT.** When Scrapling returns 0 jobs, `ScraplingFetcher.scrape_job_listings` logs at INFO level and returns `[]` — caller falls through to Tier 2. When Tier 2 also returns `[]`, the source is just absent from `all_jobs`. There is **no per-source success counter**, **no alarm on "site X returned 0 jobs N runs in a row"**, **no error broadcast to the UI** beyond the cumulative `f"Phase 2: {len(source_jobs)} jobs from {source.name}"` status message. The deleted `source_health.py` (verified absent below) was presumably meant to fill exactly this gap.

**Timeouts:**
- Adzuna httpx: 30 s ([adzuna_client.py:52](backend/scraping/adzuna_client.py#L52)).
- Scrapling fetch: no explicit timeout — relies on Patchright defaults.
- AdaptiveScraper Agent: 180 s `asyncio.wait_for` for listings ([adaptive_scraper.py:133](backend/scraping/adaptive_scraper.py#L133)), 90 s for details ([adaptive_scraper.py:220](backend/scraping/adaptive_scraper.py#L220)).
- Manual login wait: 600 s ([session_manager.py:244](backend/scraping/session_manager.py#L244)) — note this **blocks the whole batch** for up to 10 minutes per uncached login site.

**Retries:**
- `AdaptiveScraper` retries the agent **twice** on any exception with 2 s + 4 s backoff ([adaptive_scraper.py:103-145](backend/scraping/adaptive_scraper.py#L103)).
- `AdzunaClient`: no retries — a single non-200 raises `AdzunaAPIError` (caught by orchestrator).
- `ScraplingFetcher`: no retries inside the class; Tier 2 acts as the retry.

**Block / captcha / 403 detection:** **NONE.** A captcha HTML page goes through `_clean_html` → markdown → Gemini, which usually returns `[]` "no jobs found" — silently — and we fall through to Tier 2, which sees the same captcha and also returns `[]` after burning 20 Gemini calls. The system has no signal that it was blocked.

---

## 9. Critique

### Severity legend
- **[CRIT]** — likely to fail a real user, or open a security hole
- **[HIGH]** — material quality issue, will bite during scaling/changes
- **[MED]** — pragmatic concern, fix when nearby
- **[LOW]** — polish

---

**[CRIT] No cancellation support for a "Scan for Jobs" run that locks the UI.**
Confirmed: there is **no abort endpoint, no `asyncio.Task` handle stored on `app.state`, no `CancelledError` plumbing**. The `POST /api/queue/refresh` route ([api/queue.py:147-153](backend/api/queue.py#L147)) does:

```python
async def _run():
    await runner.run_batch()
asyncio.create_task(_run())     # fire-and-forget — handle is discarded
return RefreshResponse(...)
```

The frontend ([queue/+page.svelte:115-134](frontend/src/routes/queue/+page.svelte#L115)) just sets a 5-minute `setTimeout` that flips a local `refreshing = false` flag — **the backend keeps scraping regardless**. A user who fires a scan and changes their mind has no recourse short of restarting the server. Worse: clicking refresh again returns `409 Conflict` from [api/queue.py:144-145](backend/api/queue.py#L144) and `runner.running` stays `True` until the batch actually finishes (5–15 min). The 10-minute manual-login `asyncio.Event.wait` ([session_manager.py:244](backend/scraping/session_manager.py#L244)) can extend this to **20+ minutes** of frozen state. Fix: keep `task = asyncio.create_task(...)` on `app.state`, expose `POST /api/queue/cancel` that calls `task.cancel()`, propagate `CancelledError` through `scrape_batch` (asyncio already handles task cancellation cleanly — what's missing is the handle).

**[CRIT] Brittle CSS selectors with no fall-back signal.**
Every Tier-1 site lists 1–4 hand-picked selectors in `SITE_CONTENT_SELECTORS` ([site_prompts.py:584-591](backend/scraping/site_prompts.py#L584)). When a selector fails to match, `_clean_html` silently falls through to the **full page** ([scrapling_fetcher.py:303-320](backend/scraping/scrapling_fetcher.py#L303)) and trusts Gemini to find jobs in 500 KB of nav/footer/ads. Google's `.MjjYud` and Glassdoor's `.JobsList_jobListItem` class names have notoriously short half-lives — when LinkedIn / Indeed reshuffle their DOM the user just sees "0 jobs found" and Gemini quota burned. No telemetry distinguishes "selector matched, page returned 0 cards" from "selector matched 200 cards but extraction failed".

**[CRIT] Sites silently returning 0 with no alarm.**
The orchestrator broadcasts `f"Phase 2: 0 jobs from glassdoor"` to the WS the same way it broadcasts a healthy `f"Phase 2: 20 jobs from glassdoor"`. No DB persistence of per-source success rate, no "this source has been broken for 3 runs in a row" flag, no email/notification. The deleted `source_health.py` would have been the natural home — see dead-code note below.

**[HIGH] Tier 2 fallback amplifies Gemini cost on broken sources.**
When Tier 1 fails (e.g. Glassdoor selector breakage → 0 jobs), the orchestrator immediately invokes Tier 2 `AdaptiveScraper` ([orchestrator.py:253](backend/scraping/orchestrator.py#L253)), which spins up a real browser and burns up to 20 Gemini calls per keyword (`max_steps=20`). With the free Gemini tier at 15 RPM, a fully-broken LinkedIn + 3 keywords could consume the rate-limit window all on its own. Combined with the lack of empty-result detection, **a broken site is more expensive than a working one**.

**[HIGH] Rate limiting is naive bordering on absent.**
Inter-keyword sleep is `random.uniform(1, 2)`; inter-site is `random.uniform(1, 3)`. There is no token-bucket, no per-domain throttle, no respect for `robots.txt`, no `User-Agent` rotation visible in code, no exponential back-off on 429. The presence of `StealthyFetcher` papers over this for the four anti-bot sites, but Glassdoor in particular has been known to soft-ban IPs that scrape too quickly. The previous `backend/utils/retry.py` (now deleted) presumably held this logic.

**[HIGH] Pagination is non-existent across the board.**
Adzuna, LinkedIn, Indeed, WTTJ, Glassdoor, Google — every adapter hits page 1 and stops. `max_results_per_source=20` is enforced by *truncating the LLM output*, not by fetching multiple pages. A user looking for senior roles in Paris with 3 keywords gets at most 60 jobs across all of LinkedIn — a small slice of what's available.

**[HIGH] Per-source test coverage is essentially zero.**
The only meaningful adapter test is `tests/test_google_jobs_scraping.py`. The other five adapters (Adzuna, LinkedIn, Indeed, WTTJ, Glassdoor) have zero per-adapter tests; `tests/test_scraping.py` and `tests/test_adzuna_client.py` exercise the *orchestrator* with mocks. The dedup-hash collision logic in `BatchRunner._store_matches` ([batch_runner.py:453](backend/scheduler/batch_runner.py#L453)) reuses the dedup MD5 inline rather than calling `JobDeduplicator._make_key` — so a future change to the hash strategy could trivially desync the two paths without any test catching it.

**[HIGH] Copy-paste between adapters (URL-builder duplication).**
The Indeed and Google domain maps are duplicated **between two files**:
- `_INDEED_DOMAINS` in [scrapling_fetcher.py:213-217](backend/scraping/scrapling_fetcher.py#L213) (9 countries) vs. [site_prompts.py:508-514](backend/scraping/site_prompts.py#L508) (13 countries — superset).
- `_GOOGLE_DOMAINS` in [scrapling_fetcher.py:219-222](backend/scraping/scrapling_fetcher.py#L219) (6 countries) vs. [site_prompts.py:517-523](backend/scraping/site_prompts.py#L517) (13 countries).

Adding a country requires editing two lists in two files. There is no `_build_search_url` plug-in seam; the function is one big `if site == "linkedin": ... elif site == "indeed": ...` chain with knowledge of every site's URL idioms in one place. A per-site adapter class with a single `build_search_url` / `content_selector` / `extraction_prompt` triple would collapse this nicely.

**[MED] Dedup key is fragile.**
MD5 of `(company, title, location)` ([deduplicator.py:16](backend/scraping/deduplicator.py#L16)) — but "Senior Engineer" / "Senior Software Engineer" at the same company are obviously the same posting and will not collapse. Similarly "Paris, France" vs "Paris (75)" will be treated as separate. No fuzzy match, no Jaccard / token-set ratio. Field-level merge would also keep salary/posted_at from a richer-but-shorter description (currently they're overwritten wholesale).

**[MED] `apply_url` reliability depends on the LLM.**
Tier 1's LinkedIn prompt ([site_prompts.py:618-628](backend/scraping/site_prompts.py#L618)) tells Gemini to *construct* `https://www.linkedin.com/jobs/view/{JOBID}/`. If the LLM hallucinates a number or omits it, the user clicks through to a 404. There's no validation regex against the URL pattern in `parse_jobs_from_json`.

**[MED] Manual login flow can stall the entire batch for 10 min.**
[session_manager.py:244](backend/scraping/session_manager.py#L244): `asyncio.wait_for(event.wait(), timeout=600)`. If the user starts a batch, then leaves their laptop, the whole Phase 2 sits idle for 10 minutes per first-time login site. Should at minimum be opt-in ("skip sites needing login this run") and run in parallel with other sources.

**[MED] WS broadcast as side-effect inside scrape_batch.**
`broadcast_status(...)` is called eight times directly from [orchestrator.py](backend/scraping/orchestrator.py#L125) (lines 144, 163, 166, 185, 291, 308, 327, 352). The orchestrator is supposed to be a pure pipeline — it shouldn't know about WS. Either pass a `progress: Callable[[str, float], Awaitable[None]]` callback in the constructor or emit events that `BatchRunner` translates. Currently the orchestrator can't be re-used in any non-WS context (CLI, tests check this with mock manager).

**[MED] `scrape_job_listings` silently disables itself when browser-use isn't installed.**
[adaptive_scraper.py:71-74](backend/scraping/adaptive_scraper.py#L71): `ImportError → returns []`. In dev environments where browser-use install failed, the user sees "0 jobs from linkedin" with no indication that the entire Tier 2 layer is non-functional. This should be a hard startup-time check.

**[LOW] Credentials handling — actually OK, with one nit.**
`SiteCredential.encrypted_email` / `encrypted_password` are Fernet-encrypted at rest with `settings.CREDENTIAL_KEY` ([session_manager.py:296](backend/scraping/session_manager.py#L296), [api/settings.py:609](backend/api/settings.py#L609)). `CREDENTIAL_KEY` is auto-generated and persisted to `.env` if missing ([config.py:113-131](backend/config.py#L113)). No plaintext passwords in code, logs, or DB. `ADZUNA_APP_KEY` is a `SecretStr` ([config.py:19](backend/config.py#L19)) and the debug log at [adzuna_client.py:51](backend/scraping/adzuna_client.py#L51) explicitly strips `app_key` before logging params — good hygiene. **Nit:** the manual-login flow opens a headful browser with the user's credentials being typed in by the user, but the credentials in DB are typed in via the settings UI — that flow should be audited separately (out of scope for this report). No plaintext anywhere in `backend/scraping/`.

**[LOW] Dead code — confirmed removed.**
`backend/utils/retry.py` and `backend/utils/source_health.py` are **both absent** from `backend/utils/` (verified by `ls`: only `__init__.py` and `browser_path.py` remain). `grep` confirms no imports referencing these modules — the only "retry" hits live in [llm/gemini_client.py](backend/llm/gemini_client.py) (Gemini-specific 429 retry) and a single `retrying in %ds` log line in [adaptive_scraper.py:139](backend/scraping/adaptive_scraper.py#L139). The deletion looks clean. **However**, the absence is a tell: there is now *no shared retry primitive and no source-health primitive*, which directly explains the two **[CRIT]** items above (silent 0-results, naive rate limiting). These weren't replaced — they were deleted.

**[LOW] `_extract_json_from_text` is duplicated.**
[adaptive_scraper.py:23](backend/scraping/adaptive_scraper.py#L23) keeps a backward-compat alias to `json_utils.extract_json_from_text` for the tests in `tests/test_scraping.py:8`. Once the test file is updated to import from `json_utils` directly, this alias can go.

**[LOW] Tier 1 detection lives in two places.**
`TIER1_SITES` is a `frozenset` in [orchestrator.py:83](backend/scraping/orchestrator.py#L83); `_STEALTHY_SITES` is a `set` in [scrapling_fetcher.py:31](backend/scraping/scrapling_fetcher.py#L31). They overlap ("linkedin", "indeed", "glassdoor", "google_jobs") but differ ("welcome_to_the_jungle" is Tier 1 but not stealthy). The relationship isn't documented — moving these into `SITE_CONFIGS` (which already has a `tier` field at [site_prompts.py:322](backend/scraping/site_prompts.py#L322)) would centralise the taxonomy.

---

## 10. Inventory

| File | LOC | Role |
|---|---:|---|
| [scraping/__init__.py](backend/scraping/__init__.py) | 1 | Package marker. |
| [scraping/orchestrator.py](backend/scraping/orchestrator.py) | 373 | `ScrapingOrchestrator.scrape_batch()` — three-phase pipeline (API → browser → lab URL), country normalisation, post-scrape date filter, dedup. Sole entry point. |
| [scraping/scrapling_fetcher.py](backend/scraping/scrapling_fetcher.py) | 384 | Tier 1 fast path: builds per-site search URLs, fetches via Scrapling (Stealthy or plain), cleans HTML to markdown, runs a single Gemini extraction call. Used for LinkedIn/Indeed/Glassdoor/Google Jobs/WTTJ. |
| [scraping/adaptive_scraper.py](backend/scraping/adaptive_scraper.py) | 295 | Tier 2 fallback: full browser-use Agent + Gemini, no selectors. Also exposes `scrape_job_details()` for single-job enrichment. 2 retries, 180 s timeout. |
| [scraping/session_manager.py](backend/scraping/session_manager.py) | 434 | `BrowserSessionManager` — persistent Playwright storage_state per site. Manual-login WS dance, auto-login via stored `SiteCredential` for LinkedIn/Indeed, legacy + canonical storage layouts. |
| [scraping/adzuna_client.py](backend/scraping/adzuna_client.py) | 77 | Adzuna REST API client (httpx, 30 s timeout, no pagination, no retry). |
| [scraping/deduplicator.py](backend/scraping/deduplicator.py) | 29 | MD5(`company|title|location`) dedup; on collision keeps the job with the longer description. |
| [scraping/site_prompts.py](backend/scraping/site_prompts.py) | 688 | Static config: `SITE_PROMPTS` (Tier 2 prompts), `SITE_CONFIGS` (per-site metadata), `SITE_CONTENT_SELECTORS` (Tier 1 CSS scopes), `EXTRACTION_PROMPTS` (Tier 1 Gemini prompts), `format_prompt()` helper with default per-country Indeed/Google domains. |
| [scraping/json_utils.py](backend/scraping/json_utils.py) | 185 | `extract_json_from_text()` (4 strategies: direct / fenced / first `[...]` / first `{...}`), `parse_jobs_from_json()` (single normalization point for all browser tiers), `_parse_posted_date()` (ISO + relative EN/FR). |
| [utils/browser_path.py](backend/utils/browser_path.py) | 57 | `get_chromium_executable()` — resolves patchright/playwright bundled Chromium path via subprocess, cached. |
| [utils/__init__.py](backend/utils/__init__.py) | 1 | Package marker. |
| ~~`utils/retry.py`~~ | — | **DELETED.** Confirmed absent. |
| ~~`utils/source_health.py`~~ | — | **DELETED.** Confirmed absent. |

