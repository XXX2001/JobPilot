# Module: Scraping

## Purpose

The `backend/scraping/` module is the job-discovery engine for JobPilot. Its
responsibility is to collect raw job listings from every configured source —
REST APIs, major job boards, and arbitrary research-lab career pages — and
deliver a deduplicated list of `RawJob` records ready for LLM scoring and DB
persistence. The module implements a **two-tier scraping system** to balance
cost, speed, and coverage. **Tier 1** uses the Scrapling HTTP fetcher
(`ScraplingFetcher`): it fetches the page's HTML over a regular (or stealthy
Patchright-based) HTTP request, cleans the HTML down to ~5–15 KB of
LLM-friendly markdown, and calls Gemini once for structured extraction — total
cost is roughly 1 Gemini API call and 10–30 seconds per site/keyword pair.
**Tier 2** uses the `browser-use` LLM agent (`AdaptiveScraper`): it opens a
full Playwright browser, lets Gemini drive it step-by-step (up to 20 steps),
and is used as a fallback when Tier 1 returns zero results or when the site is
unknown. Tier 1 handles five known job boards (LinkedIn, Indeed, Google Jobs,
Welcome to the Jungle, Glassdoor); every other source — including user-supplied
lab/company URLs — goes directly to Tier 2.

---

## Key Components

### `orchestrator.py`

The top-level coordinator. `ScrapingOrchestrator` is instantiated once at
application startup with all dependencies injected (Adzuna client, scraper
instances, session manager, deduplicator). Its `run_morning_batch()` method
drives the full pipeline in three sequential phases:

- **Phase 1** — API sources (Adzuna): runs all `type="api"` sources in
  parallel via `asyncio.gather`.
- **Phase 2** — Browser sources: iterates each `type="browser"` source
  sequentially with a 1–3 s human-like inter-site delay. For every source +
  keyword combination it attempts Tier 1 first, falling back to Tier 2 on
  empty result or exception. Keywords are searched one at a time to avoid
  zero-result combined queries.
- **Phase 3** — Lab URL sources: runs all `type="lab_url"` sources in
  parallel using only Tier 2 (AdaptiveScraper).

After all phases, results are passed to `JobDeduplicator`. WebSocket status
messages are broadcast at each phase boundary.

### `adaptive_scraper.py`

Tier 2 implementation. `AdaptiveScraper` creates a `browser-use` `Agent` with
a Gemini LLM backend, navigates to the target URL, and returns a structured
JSON list of jobs. It retries once on failure with exponential backoff (2 s,
then 4 s). Agent steps are capped at 20 for listing pages and 8 for detail
pages. The browser is always stopped in a `finally` block to prevent leaks.
Also provides `scrape_job_details()` for fetching a single job's full
description. JSON parsing is delegated to `json_utils`.

### `scrapling_fetcher.py`

Tier 1 implementation. `ScraplingFetcher` wraps the synchronous Scrapling
library in `asyncio.get_event_loop().run_in_executor()` to avoid blocking.
It builds a keyword-aware search URL per site, fetches HTML via
`StealthyFetcher` (Patchright, for LinkedIn/Indeed/Glassdoor) or plain
`Fetcher`, cleans the HTML into markdown using lxml + markdownify + cssselect,
and calls `GeminiClient.generate_text()` once with a compact extraction prompt.
Cleaned content is capped at 30,000 characters before being sent to Gemini.

### `session_manager.py`

Manages persistent Playwright browser sessions (cookies + localStorage) so
users only log in once per site. On first use it either attempts **auto-login**
(for LinkedIn and Indeed, using credentials stored encrypted in the DB) or
falls back to a **manual login** flow: opens a headful browser, broadcasts a
`login_required` WebSocket event, and waits up to 10 minutes for the frontend
to confirm via a `login_done` WS message. Sessions are saved as
`data/browser_profiles/{site}/state.json`. A legacy flat-file path
(`data/browser_sessions/{site}_state.json`) is read for backward compatibility.

### `site_prompts.py`

Central configuration store. Contains four exported dictionaries:

- `SITE_PROMPTS` — browser-use navigation + extraction prompts for each named
  site and a `"generic"` fallback.
- `SITE_CONFIGS` — metadata dict per site: display name, prompt keys, login
  requirement, apply method, source type, supported country codes, base URL,
  login URL.
- `SITE_CONTENT_SELECTORS` — CSS selectors used by `ScraplingFetcher._clean_html()`
  to scope the HTML tree to the job-results container before noise removal.
- `EXTRACTION_PROMPTS` — Tier 1 Gemini prompts (navigation-free, parse-only)
  with site-specific variants for LinkedIn and Glassdoor.

Also exports `format_prompt(site, **kwargs)` which performs safe template
substitution with sensible defaults for all variables (country_code,
country_domain, google_domain, etc.).

### `adzuna_client.py`

Thin async wrapper around the Adzuna REST API v1. Builds query parameters from
`JobFilters`, performs a single `httpx.AsyncClient.get()` call, and maps the
response `results` list to `RawJob` objects. Raises `AdzunaAPIError` on
non-200 responses. The API rate limit is 250 free calls/day (noted in the
docstring).

### `deduplicator.py`

Single-class utility. `JobDeduplicator.deduplicate()` hashes each job by a
normalized (lowercased, whitespace-collapsed) composite key of
`company + "|" + title + "|" + location` using MD5. When two jobs share the
same hash, the one with the longer `description` string wins.

### `json_utils.py`

Shared parsing utilities used by both scraper tiers. `extract_json_from_text()`
applies four sequential strategies to recover valid JSON from arbitrary LLM
output (direct parse → fenced code block → `[…]` scan → `{…}` scan).
`parse_jobs_from_json()` normalizes the parsed value (bare list, dict with a
`jobs`/`results`/`listings`/`data` key, or single-job dict), resolves
relative URLs against the source origin, sanitizes all string fields via
`backend.security.sanitizer`, and returns a list of `RawJob` objects. Both
functions never raise; malformed input yields an empty list.

### `__init__.py`

Empty package marker (no re-exports).

---

## Public Interface

### `ScraplingFetcher`

```python
class ScraplingFetcher:
    def __init__(self, gemini_client: GeminiClient) -> None
```

**`scrape_job_listings`**

```python
async def scrape_job_listings(
    self,
    url: str,
    keywords: list[str],
    max_jobs: int = 20,
    site: str | None = None,
    location: str = "",
    country_code: str = "",
) -> list[RawJob]
```

Fetches and extracts job listings for a known job board. Builds a
keyword-aware search URL, fetches HTML (stealthy or plain), cleans to
markdown, calls Gemini once. Returns `[]` on any failure so the caller can
fall back to Tier 2.

**`fetch_page`**

```python
async def fetch_page(
    self,
    url: str,
    site: str = "",
    storage_state: str | None = None,
) -> str
```

Raw page fetch. Resolves a Playwright storage-state path from
`data/browser_profiles/{site}/state.json` if present. Loads cookies from that
file and passes them to `StealthyFetcher` (anti-bot sites) or `Fetcher`
(others). Runs synchronous Scrapling in a thread executor. Returns raw HTML
string.

---

### `AdaptiveScraper`

```python
class AdaptiveScraper:
    def __init__(self, gemini_api_key: str | None = None) -> None
```

**`scrape_job_listings`**

```python
async def scrape_job_listings(
    self,
    url: str,
    keywords: list[str],
    max_jobs: int = 20,
    prompt_template: str | None = None,
    site: str | None = None,
    location: str = "",
    country_code: str = "",
) -> list[RawJob]
```

Launches a `browser-use` Agent with up to 20 steps, 180 s timeout, and 2
attempts (backoff: 2 s, 4 s). Loads Playwright storage-state if found on disk.
Returns `[]` on exhausted retries.

**`scrape_job_details`**

```python
async def scrape_job_details(
    self,
    job_url: str,
    site: str | None = None,
) -> JobDetails | None
```

Navigates to a single job posting with up to 8 steps, 90 s timeout (no
retry). Returns a `JobDetails` object or `None` on failure.

**`_parse_agent_result`** (public for tests)

```python
def _parse_agent_result(
    self,
    result: Any,
    source_url: str = "",
) -> list[RawJob]
```

Extracts text from a `browser-use` `AgentHistoryList` (tries `final_result()`,
`extracted_content`, then `str()`), then delegates to `json_utils`.

**`_parse_job_details`** (public for tests)

```python
def _parse_job_details(self, result: Any, job_url: str = "") -> JobDetails | None
```

Same text extraction, then maps dict keys to a `JobDetails` instance.

---

### `BrowserSessionManager`

```python
class BrowserSessionManager:
    def __init__(self) -> None
```

Session directories are derived from `settings.jobpilot_data_dir`.

**`get_or_create_session`**

```python
async def get_or_create_session(self, site: str) -> Any | None
```

Returns `None` immediately if a state file already exists (scrapers load it
directly). Otherwise tries auto-login for LinkedIn/Indeed, then falls back to
manual login flow (broadcasts `login_required` WS event, opens headful browser,
waits up to 600 s). Saves session state on completion.

**`confirm_login`**

```python
def confirm_login(self, site: str) -> None
```

Called by the WS handler on receipt of a `login_done` message. Sets the
internal `asyncio.Event` that unblocks `get_or_create_session`.

**`cancel_login`**

```python
def cancel_login(self, site: str) -> None
```

Called by the WS handler on receipt of a `login_cancel` message. Marks the
site as cancelled and unblocks the waiter, which then raises `RuntimeError`.

**`list_sessions`**

```python
def list_sessions(self) -> list[SessionInfo]
```

Returns `SessionInfo` records for all state files found in both the new
profile-dir layout and the legacy flat-file layout, ordered by file path.

**`clear_session`**

```python
def clear_session(self, site: str) -> None
```

Deletes the profile directory and/or legacy flat file for a site.

**`SessionInfo` dataclass**

| Field | Type | Description |
|---|---|---|
| `site` | `str` | Site name key |
| `storage_path` | `str` | Absolute path to state.json |
| `exists` | `bool` | Always `True` for listed sessions |
| `last_used_at` | `datetime \| None` | File mtime |

---

### `ScrapingOrchestrator`

```python
class ScrapingOrchestrator:
    TIER1_SITES: frozenset[str]  # {"linkedin","indeed","google_jobs","welcome_to_the_jungle","glassdoor"}

    def __init__(
        self,
        adzuna_client: AdzunaClient | None = None,
        adaptive_scraper: AdaptiveScraper | None = None,
        session_mgr: BrowserSessionManager | None = None,
        deduplicator: JobDeduplicator | None = None,
        scrapling_fetcher: ScraplingFetcher | None = None,
    ) -> None
```

All constructor parameters are optional to allow partial injection and testing.

**`run_morning_batch`**

```python
async def run_morning_batch(
    self,
    keywords: list[str] | None = None,
    filters: JobFilters | None = None,
    sources: list[JobSource] | None = None,
    location: str = "",
    countries: list[str] | None = None,
) -> list[RawJob]
```

Orchestrates all three phases and deduplication. Broadcasts WebSocket progress
messages at: 10% (Phase 1 start), 30% (Phase 1 done), 35% (Phase 2 start),
50% (per-source done), 60% (Phase 3 start), 75% (Phase 3 done), 80%
(deduplication done).

---

### `AdzunaClient`

```python
class AdzunaClient:
    BASE_URL = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self) -> None  # reads settings.ADZUNA_APP_ID / ADZUNA_APP_KEY
```

**`search`**

```python
async def search(
    self,
    keywords: list[str],
    filters: JobFilters,
    country: str = "gb",
    page: int = 1,
    results_per_page: int = 20,
) -> list[RawJob]
```

Maps `JobFilters.locations[0]` to Adzuna `where`, `JobFilters.salary_min` to
`salary_min`, and `"full-time"` in `job_types` to `full_time=1`. Raises
`AdzunaAPIError` on non-200.

---

### `JobDeduplicator`

```python
class JobDeduplicator:
    def deduplicate(self, jobs: list[RawJob]) -> list[RawJob]
```

Deduplicates in-order. First occurrence wins unless a later duplicate has a
longer `description`. Key: MD5 of `"{company_norm}|{title_norm}|{location_norm}"`.

---

### `extract_json_from_text` / `parse_jobs_from_json` (json_utils)

```python
def extract_json_from_text(text: str) -> Any
```

Four-strategy JSON extractor. Returns parsed Python value or `None`.

```python
def parse_jobs_from_json(
    parsed: Any,
    source_url: str = "",
    source_name: str = "browser",
) -> list[RawJob]
```

Normalizes parsed value to a list, resolves relative URLs, sanitizes fields
via `backend.security.sanitizer`, returns `list[RawJob]`. Never raises.

---

### `format_prompt` (site_prompts)

```python
def format_prompt(site: str, **kwargs) -> str
```

Looks up `SITE_PROMPTS[site]` (falls back to `"generic"`), merges caller
kwargs over a full set of defaults (including computed `country_domain` and
`google_domain`), and calls `str.format(**merged)`. Returns the raw template
on `KeyError`.

---

## Data Flow

```
Morning batch trigger
        │
        ▼
┌───────────────────┐
│  Phase 1: API     │  asyncio.gather across api-type sources
│  AdzunaClient     │──► search(keywords, filters, country)
│                   │          │
└───────────────────┘          ▼
                         list[RawJob] (source_name="adzuna")

┌──────────────────────────────────────────────────────────┐
│  Phase 2: Browser sources (sequential per source)        │
│                                                          │
│  For each source × keyword:                             │
│                                                          │
│  ┌─ Tier 1 eligible? (site in TIER1_SITES) ───────────┐ │
│  │  ScraplingFetcher.scrape_job_listings()             │ │
│  │    _build_search_url()  → keyword-aware URL         │ │
│  │    fetch_page()         → raw HTML (Scrapling)      │ │
│  │    _clean_html()        → markdown ≤30 000 chars    │ │
│  │    _extract_jobs()      → GeminiClient.generate_text│ │
│  │    extract_json_from_text() + parse_jobs_from_json()│ │
│  │    → list[RawJob] (source_name="scrapling")         │ │
│  └─────────────────────────────────────────────────────┘ │
│         │ empty / exception                               │
│         ▼                                                 │
│  ┌─ Tier 2 fallback (or direct for non-TIER1 sites) ──┐  │
│  │  AdaptiveScraper.scrape_job_listings()              │  │
│  │    format_prompt(site, ...) → task string           │  │
│  │    browser-use Agent.run()  → AgentHistoryList      │  │
│  │    _parse_agent_result()                            │  │
│  │    extract_json_from_text() + parse_jobs_from_json()│  │
│  │    → list[RawJob] (source_name="browser")           │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                          │
│  country code applied to jobs missing job.country        │
└──────────────────────────────────────────────────────────┘

┌───────────────────┐
│  Phase 3: Lab URLs │  asyncio.gather across lab_url-type sources
│  AdaptiveScraper  │──► Tier 2 only (no Tier 1 for arbitrary URLs)
└───────────────────┘

        │
        ▼
┌───────────────────┐
│  Deduplication    │  JobDeduplicator.deduplicate(all_jobs)
│                   │  MD5(company|title|location) → keep longest description
└───────────────────┘
        │
        ▼
  list[RawJob]  →  morning_batch.py  →  LLM scoring  →  DB storage
```

**Session handling** (pre-Phase 2): for sites with `requires_login: True`,
`BrowserSessionManager.get_or_create_session(site)` is called before the first
keyword loop. If no state file exists, an auto-login or manual-login flow runs.
The resulting `state.json` is read by both `ScraplingFetcher.fetch_page()` and
`AdaptiveScraper.scrape_job_listings()` on subsequent calls.

---

## Configuration

### Adzuna API

| Setting | Source | Description |
|---|---|---|
| `ADZUNA_APP_ID` | `backend.config.settings` | Adzuna application ID |
| `ADZUNA_APP_KEY` | `backend.config.settings` | Adzuna application key |

Free tier: 250 API calls/day. Country must be a 2-letter ISO code.

### Browser / Scraper

| Setting | Source | Default | Description |
|---|---|---|---|
| `JOBPILOT_SCRAPER_HEADLESS` | env / settings | `True` | Headless mode for Scrapling StealthyFetcher and browser-use Agent |
| `GOOGLE_API_KEY` | env / settings | — | Gemini API key used by both AdaptiveScraper and ScraplingFetcher |
| `GOOGLE_MODEL` | env / settings | `"gemini-2.0-flash"` | Gemini model identifier for browser-use ChatGoogle |
| `jobpilot_data_dir` | env / settings | — | Base directory for browser profiles and session files |
| `CREDENTIAL_KEY` | env / settings | — | Fernet symmetric key for decrypting stored site credentials |

### Session / Profile Paths

| Path | Description |
|---|---|
| `{data_dir}/browser_profiles/{site}/state.json` | Canonical Playwright storage-state (new layout) |
| `{data_dir}/browser_sessions/{site}_state.json` | Legacy flat-file (backward compat, read-only) |

### Site Configurations (`SITE_CONFIGS`)

| Site key | Type | Tier | Login required | Apply method | Country codes |
|---|---|---|---|---|---|
| `linkedin` | browser | 1 | Yes | auto (Easy Apply) | gb, us, de, fr, nl, ca, au |
| `indeed` | browser | 1 | No | manual | gb, us, ca, au, de, fr |
| `google_jobs` | browser | 1 | No | manual | gb, us, de, fr, nl |
| `welcome_to_the_jungle` | browser | 1 | No | manual | fr, gb, de, nl |
| `glassdoor` | browser | 1 | No | manual | fr only |
| `adzuna` | api | — | No | manual | gb, us, au, de, fr, nl, ca |
| `lab_website` | lab_url | 2 | No | manual | (any) |

### CSS Content Selectors (`SITE_CONTENT_SELECTORS`)

Used by `ScraplingFetcher._clean_html()` to scope the lxml tree before noise
removal:

| Site | Selector |
|---|---|
| `linkedin` | `.jobs-search-results-list, .scaffold-layout__list` |
| `indeed` | `#mosaic-jobResults, .jobsearch-ResultsList` |
| `google_jobs` | `.gws-plugins-horizon-jobs__tl-lvc` |
| `welcome_to_the_jungle` | `[data-testid='search-results-list-item-wrapper']` |
| `glassdoor` | `[data-test='jobListing'], .react-job-listing, .JobsList_jobListItem, .JobsList_wrapper` |

### Tier 1 Content Limit

`_MAX_CONTENT_CHARS = 30_000` — markdown content is truncated to this length
before being sent to Gemini.

### browser-use Agent Limits

| Context | `max_steps` | `timeout` | Retries |
|---|---|---|---|
| Job listings (Tier 2) | 20 | 180 s | 2 (backoff 2 s, 4 s) |
| Job details (Tier 2) | 8 | 90 s | 1 (no retry) |

### Manual Login Timeout

`BrowserSessionManager._request_login()` waits up to **600 seconds** (10
minutes) for a `login_done` WS message before raising `TimeoutError`.

### Location / Country Normalization

`_normalize_country()` in `orchestrator.py` maps free-form city/country strings
to 2-letter Adzuna codes using the `LOCATION_TO_COUNTRY` dict. The hardcoded
default fallback is `"fr"` (France).

### Inter-request Delays (Phase 2)

- Between keywords on the same site: `random.uniform(1, 2)` seconds.
- Between sites: `random.uniform(1, 3)` seconds.

---

## Known Limitations / TODOs

### Hardcoded Values and Defaults

- `_normalize_country()` defaults to `"fr"` when no match is found. This is
  described in a comment as "default to fr for this user" — it is a
  user-specific hardcode, not a configurable setting.
- `ScraplingFetcher._build_search_url()` for Glassdoor is hardcoded to
  `glassdoor.fr` with a comment explaining that `glassdoor.com` always
  redirects to the generic French homepage regardless of keyword.
- The Glassdoor URL template in the Tier 2 `SITE_PROMPTS` still points to
  `glassdoor.com` (not `glassdoor.fr`), creating an inconsistency with the
  Tier 1 URL builder.
- `_INDEED_DOMAINS` and `_GOOGLE_DOMAINS` maps are duplicated between
  `scrapling_fetcher.py` (`_build_search_url`) and `site_prompts.py`
  (`format_prompt`). They are not shared via a single source of truth.
- `AdzunaClient.search()` is hardcoded to `page=1` and
  `results_per_page=20` — there is no pagination support.

### Site-Specific Hacks

- LinkedIn `apply_url` construction in the Tier 1 extraction prompt includes
  literal escaped braces (`{{jobId}}`) alongside normal template variables,
  requiring the LLM to resolve the job ID itself rather than building it
  programmatically.
- `scrape_job_details()` in `AdaptiveScraper` is always headless (`headless=True`)
  regardless of `settings.jobpilot_scraper_headless`. The listing scraper
  respects the setting; the details scraper does not.
- `_attempt_auto_login()` in `BrowserSessionManager` only supports LinkedIn and
  Indeed. Auto-login for any other `requires_login` site silently falls back to
  manual flow.

### Missing Features

- No retry or pagination for Adzuna (single page, 20 results per keyword).
- No Tier 1 implementation for `lab_website` sources — all lab URLs go
  through the full browser-use agent even when their pages are simple static
  HTML.
- `ScraplingFetcher` does not implement `scrape_job_details()`; that path
  always uses the Tier 2 `AdaptiveScraper`.
- `BrowserSessionManager.list_sessions()` and `clear_session()` are exposed
  but there is no API endpoint or UI surface to call them directly (sessions
  can only be cleared by stopping the server and deleting files manually, or
  via code).
- `JobDeduplicator` is in-memory only. There is no cross-batch deduplication
  against jobs already stored in the database; that check must be done
  downstream in the morning batch.
- The `__init__.py` exports nothing — all imports in other modules use full
  dotted paths (`from backend.scraping.orchestrator import ScrapingOrchestrator`).

### Scrapling / Dependency Constraints

- `StealthyFetcher` is a Patchright-based synchronous fetcher, run in a thread
  executor. Long-running pages can tie up the executor thread for the full
  duration of the fetch.
- `lxml`, `markdownify`, and `cssselect` are required by `ScraplingFetcher._clean_html()`.
  If any of these is missing, the method falls back to raw HTML truncation and
  logs an error, silently degrading extraction quality.
- `browser_use` is an optional dependency throughout; both `AdaptiveScraper`
  and `BrowserSessionManager` degrade gracefully (returning `[]` / `None` /
  raising `ImportError`) when it is not installed.
