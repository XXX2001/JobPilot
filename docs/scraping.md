# Job scraping & sources

JobPilot discovers postings on two paths:

- **The discovery monitor** (`backend/monitor/`, `backend/scraping/sources/`) — always on when enabled. Deterministic HTTP polls of job boards every minute or few, no LLM, no browser, logged out. Stores, scores and notifies. See [The discovery monitor](#the-discovery-monitor).
- **The batch scrape** (`ScrapingOrchestrator`) — on demand, started from `POST /api/queue/refresh`: an API source (Adzuna), an HTTP + single-LLM-call tier and a browser-use agent tier. The rest of this page from [Overview](#overview) on describes it.

## The discovery monitor

Off by default. Set `MONITOR_ENABLED=true` and restart: the app's APScheduler then runs `monitor_tick` every `MONITOR_TICK_SECONDS`.

### What one tick does

`monitor_tick` (`backend/monitor/poller.py`) selects the enabled `job_sources` rows that carry an adapter and are due (`next_poll_at` null or past), and polls them concurrently, at most `MONITOR_MAX_CONCURRENCY` at a time. For each source, `poll_source`:

1. Calls the adapter with the stored `etag` and `last_scraped_at`. A `304 Not Modified` ends the poll there.
2. `upsert_discovered` stores the jobs. Identity is `(source, external_id)`, with `dedup_hash` as the cross-source fallback; a job already stored is not inserted again.
3. `match_new` scores each new job with the owner's CV and search settings ([Matching](matching.md)) and creates one `new` match per job at or above `min_match_score`. A job has one match, ever.
4. Each new match is announced: a `new_match` WebSocket frame, and one POST to `NTFY_URL` when set (title `New match <score>`, body `<title> — <company> (<location>)`, click-through to the posting).
5. The source row records the outcome: `last_scraped_at`, `etag`, `fail_count`, `last_error`, `next_poll_at`.

The first successful poll of a source **seeds** it: every job is stored and matched, none is announced. For Indeed and LinkedIn, postings older than the seed that surface later are stored and matched but not announced either.

Each source has its own DB session and transaction. A failed poll rolls back whole, increments `fail_count` and doubles the wait (interval × 2^fails, capped at 6 h). After a success the next poll is one interval away, ±20% jitter. An authentication failure (Indeed's app key revoked) disables the source and sends one "Source disabled" notification; it is not retried.

### Sources

Adapters live in `backend/scraping/sources/` and register in `ADAPTERS`. All are logged out: no account, no cookies.

| Adapter | Request | Default interval | Descriptions | `config` keys |
|---|---|---|---|---|
| `greenhouse` | Conditional GET of the whole board | 60 s | yes | `slug` |
| `ashby` | Conditional GET of the whole board | 60 s | yes | `slug` |
| `smartrecruiters` | Conditional GET, first 100 postings, newest first | 60 s | no (cards) | `slug` |
| `indeed` | One GraphQL POST for the last N hours (vendored mobile-app query) | 420 s | yes | `what`, `where`, `country` (`FR`), `radius_km` (30), `window_hours` (2) |
| `linkedin_guest` | Guest search fragment, newest first, up to 5 pages of 10 cards | 300 s | no (cards) | `keywords`, `location` |

A source is a `job_sources` row with `type="monitor"` and `config = {"adapter": …, …}`. `poll_interval_s` overrides the default interval. Rows without an adapter belong to the batch scrape and are never polled by the monitor.

Cards without a description get a provisional, title-only score (see [Matching](matching.md)). When Indeed's window overflows one page, the source's window is halved for the next polls. LinkedIn's guest endpoint is not proven under sustained polling: anything that is not a card list counts as blocked and the source backs off.

### Adding sources

One source over the API (needs the local session, like every POST under `/api`):

```bash
curl -X POST http://localhost:8000/api/monitor/sources -H 'Content-Type: application/json' \
  -d '{"adapter": "linkedin_guest", "name": "linkedin:robotics:Paris",
       "config": {"keywords": "robotics", "location": "Paris"}}'
```

A company watchlist from a CSV (`company,sample_url,fr_jobs,relevant_jobs`; the board slug is read from `sample_url`):

```bash
env -u PYTHONPATH uv run python scripts/import_watchlist.py --ats greenhouse \
    --csv path/to/fr_greenhouse.csv [--title-regex 'robot|embedded|software'] [--limit 20]
```

Each board is polled once. It is kept, as `JobSource(name="<ats>:<slug>", type="monitor")`, when at least one of its jobs is in the owner's area (`SearchSettings.locations` / `countries`) and, with `--title-regex`, that job's title matches. Names that already exist are skipped without a request, so the script can be re-run. One request per second.

### Status API

| Route | Effect |
|---|---|
| `GET /api/monitor/status` | `{"enabled": bool, "sources": [...]}`; per source `id`, `name`, `adapter`, `enabled`, `interval_s`, `last_scraped_at`, `next_poll_at`, `fail_count`, `last_error`, `jobs_seen`, `median_lag_s` |
| `POST /api/monitor/sources` | Add a source: `{"adapter", "name", "config", "interval_s"?}`. `422` for an unknown adapter, `409` for a taken name |
| `POST /api/monitor/sources/{id}/pause` | Stop polling the source |
| `POST /api/monitor/sources/{id}/resume` | Resume; clears the backoff, so it is polled at the next tick |

`median_lag_s` is the median time from `posted_at` to first sighting over the last 7 days, the seeding poll left out.

### Settings

| Env var | Default | Effect |
|---|---|---|
| `MONITOR_ENABLED` | `false` | Schedules `monitor_tick`. Nothing polls when false |
| `MONITOR_TICK_SECONDS` | `30` | How often due sources are looked for. A source cannot be polled more often than this |
| `MONITOR_MAX_CONCURRENCY` | `8` | Sources polled at the same time within one tick |
| `NTFY_URL` | empty | ntfy topic URL (`https://ntfy.sh/<topic>` or self-hosted). Empty: WebSocket frame only |

Applied on restart.

### Measured

Live run, 2026-11-14, 27 minutes, 30 sources (18 Greenhouse, 5 Ashby, 5 SmartRecruiters, 1 Indeed, 1 LinkedIn guest): 3,447 jobs stored, no source in a failed state at the end, 13 ntfy notifications delivered. The LinkedIn source measured a median lag of 840 s from posting to first sighting; no other source published a posting during the run, so their lag is unmeasured.

Soak, 2026-11-14 16:00–18:00 UTC, 2 hours, same 30 sources, Saturday evening: no source in a failed state at the end, no SQLite lock errors, memory flat at 162–170 MB RSS, 32 s of CPU in total. The LinkedIn guest source went from 45 to 85 jobs seen (median lag 780 s), produced both notifications of the run, and returned an unparseable body 5 times, recovering each time. No company board published a posting during the window, so board lag is still unmeasured. The soak ran commit `ac5d2f1`, before the gazetteer scorer.

## Overview

The batch scrape lives in `backend/scraping/`. One `ScrapingOrchestrator` (`backend/scraping/orchestrator.py:76`) coordinates a single "batch" — one scan triggered by a user or the scheduler. It receives all collaborators by constructor injection (so the app can wire real ones and tests can mock them), runs three phases, then post-filters by date and deduplicates.

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
