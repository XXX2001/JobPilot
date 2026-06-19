# Job scraping & sources

How JobPilot discovers job postings: a tiered orchestrator that fans out across an API source (Adzuna), a fast HTTP + single-LLM-call tier, and a full browser-use agent tier, then dedupes and tracks per-source health.

## Overview

All scraping lives in `backend/scraping/`. One `ScrapingOrchestrator` (`backend/scraping/orchestrator.py:76`) coordinates a single "batch" — one scan triggered by a user or the scheduler. It receives all collaborators by constructor injection (so the app can wire real ones and tests can mock them), runs three phases, then post-filters by date and deduplicates.

| File | Role |
|------|------|
| `orchestrator.py` | Coordinates all sources for one batch; owns the 3-phase pipeline and tier selection. |
| `adzuna_client.py` | Phase 1 — structured REST API source (`AdzunaClient`). |
| `scrapling_fetcher.py` | Tier 1 — HTTP fetch + single LLM extraction call (`ScraplingFetcher`). |
| `adaptive_scraper.py` | Tier 2 — browser-use agent that works on any site (`AdaptiveScraper`). |
| `session_manager.py` | Persists per-site login storage state across runs (`BrowserSessionManager`). |
| `deduplicator.py` | Collapses duplicate jobs by normalized (company, title, location) hash. |
| `source_health.py` | In-memory per-source health tracker feeding the UI status pills. |
| `site_prompts.py` | Per-site prompts, content selectors, domain maps, and `SITE_CONFIGS`. |
| `json_utils.py` | Robust JSON extraction + `RawJob` parsing/sanitization, shared by both tiers. |

Every source produces `RawJob` objects (`backend/models/schemas.py`). The orchestrator returns a deduplicated `list[RawJob]`.

## The orchestrator: three phases

`ScrapingOrchestrator.scrape_batch()` (`orchestrator.py:106`) takes keywords, optional `JobFilters`, a list of `JobSource` ORM records, a `location` string, `countries`, `max_results_per_source`, and `max_age_days`. It partitions sources by `source.type` and runs:

### Phase 1 — API sources (fast, parallel)

Sources with `type == "api"` are dispatched to `AdzunaClient.search()` concurrently via `asyncio.gather` (`orchestrator.py:147-179`). Each source's `config["country"]` is normalized to a 2-letter Adzuna code by `_normalize_country` (`orchestrator.py:52`), which maps common place names ("paris" → "fr", "london" → "gb") via `LOCATION_TO_COUNTRY` (`orchestrator.py:32`).

If no explicit API source is configured but Adzuna and keywords exist, the orchestrator still runs a **default** Adzuna search (`orchestrator.py:180-200`), picking the country from `countries[0]`, then `location`, then falling back to `"fr"`.

### Phase 2 — Browser sources (sequential, human-like delay)

Sources with `type == "browser"` are processed one at a time (`orchestrator.py:205-364`) with randomized delays (`asyncio.sleep(random.uniform(1, 3))` between sites, 1-2 s between keyword searches) to avoid hammering servers. For each source:

1. If the site `requires_login` (`SITE_CONFIGS`) and a session manager is present, `session_mgr.get_or_create_session()` is invoked first.
2. A prompt template is chosen: `source.prompt_template` if set, else `SITE_PROMPTS[source.name]`, else `SITE_PROMPTS["generic"]`.
3. Keywords are searched **one at a time** (`orchestrator.py:245`) so combined queries don't over-narrow results; `per_kw_max` distributes the per-source budget across keywords (min 5 each).
4. A pagination budget `max_pages` is read from `source.config` and hard-capped to 1-5 (`orchestrator.py:242-243`).

This phase is where the **tiered strategy** lives (see below).

### Phase 3 — Custom (lab) URL sources (parallel)

Sources with `type == "lab_url"` need no login, so they run concurrently through `AdaptiveScraper` with the `SITE_PROMPTS["lab_website"]` template (`orchestrator.py:369-403`).

### Post-processing

After all phases:

- **Date filter** (`orchestrator.py:408-415`): if `max_age_days` is set, jobs older than the cutoff are dropped. Jobs with no `posted_at` are kept (can't be filtered) — a safety net for sources that lack URL-level date filtering.
- **Dedup** (`orchestrator.py:420-424`): `JobDeduplicator.deduplicate()` collapses duplicates.

Throughout, progress is streamed to the frontend via `broadcast_status(...)` (WebSocket), and every source attempt is recorded in `SourceHealthTracker`.

## The tiered scraping strategy (Phase 2)

For each `(source, keyword)` pair the orchestrator tries cheap-first:

```
Tier 1 (ScraplingFetcher)  →  if 0 jobs / error  →  Tier 2 (AdaptiveScraper)
```

Tier 1 is only attempted for sites in `TIER1_SITES` (`orchestrator.py:84`):

```python
TIER1_SITES = frozenset({"linkedin", "indeed", "google_jobs", "welcome_to_the_jungle", "glassdoor"})
```

The fallback logic (`orchestrator.py:251-315`):

- If `scrapling_fetcher` exists and the site is in `TIER1_SITES`, Tier 1 runs page-by-page up to `max_pages`, stopping early when a page returns `< per_kw_max` jobs (avoids spending LLM calls on dead pages).
- If Tier 1 returned no jobs (empty or raised), and `adaptive_scraper` exists, **Tier 2 runs as fallback**. For non-Tier-1 sites, Tier 2 is the primary path.
- Any job missing a `country` is back-filled with the source's resolved country code.

### Tier 1 — `ScraplingFetcher`

`scrapling_fetcher.py:41`. The point of Tier 1 is cost: it reduces "~20 LLM API calls per site/keyword to 1" and cuts wall-clock from 60-180 s to ~10-30 s. The flow of `scrape_job_listings()` (`scrapling_fetcher.py:56`):

1. **Build a search URL** — `_build_search_url()` (`scrapling_fetcher.py:205`) turns keywords/location/country/age/page into a real per-site search URL. Each adapter is hand-written:
   - LinkedIn — `?keywords=&location=&f_TPR=r{seconds}&sortBy=DD`, pagination `&start={(page-1)*25}`.
   - Indeed — regional domain via `indeed_domain(cc)`, `&fromage={days}`, pagination `&start={(page-1)*10}`.
   - Google Jobs — `udm=8` SERP via `google_domain(cc)`, `&chips=date_posted:...`. **Known gap:** the SERP exposes no clean pagination, so `page` is accepted but ignored.
   - Welcome to the Jungle — Algolia `&page={N}` (1-indexed) + country refinement.
   - Glassdoor — `_IP{N}.htm` page suffix (brittle); date filtering deferred to the post-scrape filter.
   - Unknown site → returns `base_url` unchanged.
2. **Fetch HTML** — `fetch_page()` (`scrapling_fetcher.py:129`) runs Scrapling's synchronous fetchers in an executor. Anti-bot sites (`_STEALTHY_SITES` = linkedin, indeed, glassdoor, google_jobs) use `StealthyFetcher` (Patchright-based); others use plain `Fetcher`. Saved Playwright `state.json` cookies are loaded if present (with a workaround that strips object-valued `partitionKey`). Visibility honors `JOBPILOT_SCRAPER_HEADLESS`.
3. **Clean HTML** — `_clean_html()` (`scrapling_fetcher.py:303`) parses with lxml, optionally scopes to a `SITE_CONTENT_SELECTORS` container, strips noise tags (script/style/nav/footer/svg/iframe…), keeps only `href`/`class`/`data-job-id`/`data-entity-urn`, converts to markdown via `markdownify`, collapses whitespace, and truncates to `MAX_SCRAPLING_CONTENT_CHARS`. A repeated selector miss is counted per site (`_selector_miss_counts`) and falls back to the full page. For Google Jobs it promotes `data-share-url` to an `href` so the link survives markdown conversion.
4. **Extract** — `_extract_jobs()` (`scrapling_fetcher.py:404`) formats the cleaned content into a site-specific (or `"default"`) template from `EXTRACTION_PROMPTS` and makes a **single** `self._llm.generate_text(prompt)` call.
5. **Parse** — `extract_json_from_text` + `parse_jobs_from_json` turn the raw response into sanitized `RawJob`s.

Any failure at any step returns `[]`, signaling the orchestrator to fall back to Tier 2.

### Tier 2 — `AdaptiveScraper`

`adaptive_scraper.py:26`. A full [browser-use](https://github.com/browser-use/browser-use) `Agent` that navigates and extracts without hardcoded selectors — it works on any site, at the cost of many LLM calls. `scrape_job_listings()` (`adaptive_scraper.py:44`):

- Builds the task prompt via `format_prompt(site, ...)` for known templates, or a best-effort `.format()` for custom user templates.
- Launches a `Browser` with `headless=settings.jobpilot_scraper_headless`, optional `executable_path` from `get_chromium_executable()`, and an optional saved `storage_state` (login session).
- Runs the `Agent` with `max_steps=20` under a 180 s `asyncio.wait_for`, with **2 attempts** and exponential backoff. The browser is always `stop()`-ed in `finally`.
- Parses the agent result through `_parse_agent_result()` (handles `final_result()`, `extracted_content`, or raw string) → `json_utils`.

`scrape_job_details()` (`adaptive_scraper.py:151`) is a sibling that opens a single posting (`max_steps=8`, 90 s timeout) and returns a `JobDetails`. Both methods degrade gracefully — returning `[]` / `None` if `browser_use` isn't installed or the agent fails.

## Provider-agnostic LLM

Both tiers route their LLM calls through the LLM factory (`backend/llm/factory.py`), selected by config and applied on restart.

- **Tier 1** gets an `LLMClient` injected at construction. `backend/main.py:141,153` builds it with `make_llm_client()` (driven by `LLM_PROVIDER` = `openai` | `anthropic`) and passes it to `ScraplingFetcher(llm_client=gen_client)`. The fetcher only calls `self._llm.generate_text(...)`, so it is agnostic to the underlying provider.
- **Tier 2** builds its model lazily in `AdaptiveScraper._make_llm()` (`adaptive_scraper.py:34`), which calls `make_browser_llm()`. That factory returns a browser-use `Chat*` for `BROWSER_LLM_PROVIDER`: `ChatOpenAI` (with `BROWSER_LLM_BASE_URL`) for OpenAI / OpenAI-compatible endpoints (an OpenAI-compatible `base_url` also reaches other providers this way). Anthropic requires an OpenAI-compatible `base_url` because browser-use ships no `ChatAnthropic` (`factory.py:48-62`).

If `browser_use` isn't importable, `_make_llm()` returns `None` and Tier 2 becomes a no-op.

## The `SCRAPLING_ENABLED` flag

`SCRAPLING_ENABLED: bool = True` (`backend/config.py:67`) is the master switch for Tier 1. At startup `main.py:153` does:

```python
scrapling = ScraplingFetcher(llm_client=gen_client) if settings.SCRAPLING_ENABLED else None
```

When `False`, the orchestrator receives `scrapling_fetcher=None`, so Phase 2 skips Tier 1 entirely and every browser source goes straight to the more expensive Tier 2 `AdaptiveScraper`. (A sibling flag, `APPLY_TIER1_ENABLED`, mirrors this for the application/filler path.)

## Supporting components

### `BrowserSessionManager` (`session_manager.py`)

Persists login state so a user logs in once per site. Storage state lives at `data/browser_profiles/{site}/state.json` (with backward-compat for the old flat `data/browser_sessions/{site}_state.json`). `get_or_create_session()` (`session_manager.py:58`):

- If a saved state exists, returns immediately (the scrapers load `state.json` directly).
- Otherwise tries `_attempt_auto_login()` using encrypted `SiteCredential`s (Fernet-decrypted with `CREDENTIAL_KEY`) for LinkedIn / Indeed.
- If auto-login fails, opens a **headful** browser, broadcasts a `login_required` WebSocket message, navigates to the site's `login_url`, and waits up to 10 minutes for the frontend to call `confirm_login(site)` (or `cancel_login(site)`). On confirmation `browser.stop()` writes the storage state to disk.

`list_sessions()` / `clear_session()` manage saved sessions for the settings UI.

### `JobDeduplicator` (`deduplicator.py`)

Keys each job on `md5(normalized "company|title|location")`. On collision it keeps the job with the **longer description**.

### `SourceHealthTracker` (`source_health.py`)

In-memory only — lives for the lifetime of the orchestrator singleton on `app.state`. `record(source, outcome=...)` logs each attempt as `ok` (≥1 job), `empty` (0 jobs, no error), or `error`, tracking a rolling history window of 5, consecutive failures, and the last error. `SourceRecord.status()` maps consecutive failures to `healthy` / `degraded` (≥1 non-ok) / `down` (≥3 non-ok) / `unknown`. `snapshot()` is served by `GET /api/queue/source-health` so the queue sidebar can render a green/yellow/red pill per source.

### `json_utils.py`

Shared by both tiers. `extract_json_from_text()` tries four strategies (direct parse, ```json fences, first `[...]`, first `{...}`). `parse_jobs_from_json()` normalizes a dict-with-`jobs`-key or bare list into `RawJob`s, resolves relative URLs against the source origin, parses fuzzy `posted_date` strings ("2 days ago", "yesterday", ISO, FR/EN relative forms via `_parse_posted_date`), and runs every field through `sanitize_for_prompt` / `sanitize_url` (`backend/security/sanitizer.py`). It never raises — malformed items are skipped.

### `site_prompts.py`

Single source of truth for site metadata:

- `INDEED_DOMAINS` / `GOOGLE_DOMAINS` + `indeed_domain()` / `google_domain()` — per-country domains used by **both** Tier 1's `_build_search_url` and Tier 2's `format_prompt`, so the two tiers can't drift.
- `SITE_PROMPTS` — Tier 2 navigation prompts (e.g. LinkedIn's "DO NOT CLICK ANYTHING" guardrails), plus `generic` and `lab_website` fallbacks. `format_prompt(site, **kwargs)` (`site_prompts.py:516`) substitutes with safe defaults for every variable.
- `EXTRACTION_PROMPTS` — Tier 1 parse-only prompts (`default` + per-site), each ending in `{cleaned_content}`.
- `SITE_CONTENT_SELECTORS` — CSS selectors used to scope HTML before cleaning in Tier 1.
- `SITE_CONFIGS` — per-site `requires_login`, `apply_method`, `country_codes`, `base_url`, `login_url`, etc.

## Configuration reference

| Setting | Default | Effect |
|---------|---------|--------|
| `SCRAPLING_ENABLED` | `True` | Master switch for Tier 1. |
| `LLM_PROVIDER` | `openai` | Provider for Tier 1 generation (`openai`/`anthropic`). |
| `BROWSER_LLM_PROVIDER` | `openai` | Provider for the Tier 2 browser agent. |
| `BROWSER_LLM_BASE_URL` | `""` | Required when the browser provider is Anthropic / OpenAI-compatible. |
| `JOBPILOT_SCRAPER_HEADLESS` | — | Browser visibility for both Scrapling and browser-use. |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | — | Adzuna credentials (250 free calls/day). |
| `CREDENTIAL_KEY` | — | Fernet key for decrypting stored site credentials (auto-login). |

## Related

- [Architecture](architecture.md) — overall system design.
- [API reference](api-reference.md) — including `GET /api/queue/source-health`.
- [File map](file-map.md) — backend module layout.
- [User guide](user-guide.md) — running scans and managing sources.
- [Index](index.md) / [README](../README.md)
