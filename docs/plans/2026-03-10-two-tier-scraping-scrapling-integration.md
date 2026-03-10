# Two-Tier Scraping: Scrapling Integration Plan

**Date:** 2026-03-10
**Scope:** Replace browser-use agent loop with Scrapling fetch + single Gemini call for known sites
**Goal:** ~90% reduction in Gemini API calls, ~5x speed improvement for Phase 2 browser sources

---

## Summary

Currently every browser scrape runs a full browser-use Agent loop (~20 Gemini API calls per site
per keyword). For known job boards (LinkedIn, Indeed, Google Jobs, WTTJ, Glassdoor), the page
structure is predictable — the LLM doesn't need to "discover" how to navigate. We can fetch the
page with Scrapling, clean the HTML down to essential content, and extract jobs with a single
Gemini call. Unknown sites (lab URLs, custom sources) keep the current browser-use approach.

### Cost/Speed Impact

| Metric | Current (browser-use) | New (Scrapling + 1 call) |
|--------|-----------------------|--------------------------|
| Gemini calls per site/keyword | ~20 | 1 |
| Time per site/keyword | ~60-180s | ~10-30s |
| Free tier (15 RPM) capacity | ~1 site/min | ~15 sites/min |
| Browser instances | Full Playwright per agent | HTTP or lightweight Patchright |

---

## Architecture

```
Phase 2 (known sites):
  ScraplingFetcher.scrape_job_listings()
    ├── fetch_page() → raw HTML (Scrapling StealthyFetcher or Fetcher)
    ├── _clean_html() → stripped markdown (~5-15KB)
    ├── _extract_jobs() → single GeminiClient.generate_text() call
    ├── _parse_and_sanitize() → list[RawJob]
    └── on failure → fallback to AdaptiveScraper (Tier 2)

Phase 3 (lab/unknown sites):
  AdaptiveScraper.scrape_job_listings() → unchanged (browser-use + Gemini agent)
```

---

## Pillar 1: New Module — `backend/scraping/scrapling_fetcher.py`

### Task 1.1 — ScraplingFetcher class

**New file.** Core Tier 1 scraper. No browser-use dependency.

```python
class ScraplingFetcher:
    def __init__(self, gemini_client: GeminiClient) -> None: ...

    async def scrape_job_listings(
        self,
        url: str,
        keywords: list[str],
        max_jobs: int = 20,
        site: str | None = None,
        location: str = "",
        country_code: str = "",
    ) -> list[RawJob]: ...

    async def fetch_page(
        self,
        url: str,
        site: str,
        storage_state: str | None = None,
    ) -> str: ...
```

**Dependencies:** `scrapling`, `markdownify`, `backend.llm.gemini_client.GeminiClient`

**Fetcher tier selection per site:**

| Site | Fetcher | Reason |
|------|---------|--------|
| linkedin | `StealthyFetcher` | Auth cookies + anti-bot |
| indeed | `StealthyFetcher` | Anti-bot (Cloudflare) |
| glassdoor | `StealthyFetcher` | Anti-bot |
| google_jobs | `Fetcher` | Public, minimal protection |
| welcome_to_the_jungle | `Fetcher` | Public, minimal protection |

**Storage state reuse:**
- Reads existing Playwright state from `{data_dir}/browser_profiles/{site}/state.json`
- Scrapling's `StealthyFetcher` uses Patchright (Playwright fork) — same storage format
- No changes to `BrowserSessionManager` needed

### Task 1.2 — HTML cleaning pipeline (`_clean_html`)

**Private method on ScraplingFetcher.** Reduces raw HTML to LLM-friendly content.

Steps:
1. Parse HTML with `lxml` (already a Scrapling dependency)
2. Remove tags: `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<noscript>`, `<svg>`, `<iframe>`
3. If a site-specific content selector exists (see Task 2.2), scope to that container first
4. Strip all attributes except `href`, `class`, `data-job-id`, `data-entity-urn` (keeps structure + job IDs)
5. Convert to markdown via `markdownify` (compact, token-efficient)
6. Collapse excessive whitespace
7. Truncate to 30,000 chars max (safety cap for Gemini context)

**Expected reduction:** ~500KB raw HTML → ~5-15KB cleaned markdown

### Task 1.3 — Single-call extraction (`_extract_jobs`)

Uses `GeminiClient.generate_text()` with an extraction-only prompt (no navigation instructions).

**Prompt structure:**
```
You are a job listing extractor. Extract all job listings from the page content below.

Return a JSON array. Each job object must have:
- title: job title
- company: company name
- location: location string
- salary: salary text or null
- posted_date: posting date or null
- description_preview: first 200 chars of description or null
- apply_url: direct URL to the job posting

Page content:
{cleaned_markdown}
```

**Parsing:** Reuse existing `_extract_json_from_text()` from `adaptive_scraper.py`.
Move this function to a shared utility (`backend/scraping/json_utils.py`) so both
`ScraplingFetcher` and `AdaptiveScraper` can use it without import cycles.

### Task 1.4 — Result parsing and sanitization (`_parse_and_sanitize`)

Same logic as `AdaptiveScraper._parse_agent_result()`:
- Normalize JSON (handle `{jobs: [...]}` wrappers)
- Build `RawJob` objects
- Sanitize all fields via `sanitize_for_prompt()` and `sanitize_url()`
- Set `source_name = "scrapling"`

Extract the shared parsing logic into a standalone function in `backend/scraping/json_utils.py`:

```python
# New file: backend/scraping/json_utils.py

def extract_json_from_text(text: str) -> Any: ...  # moved from adaptive_scraper.py

def parse_jobs_from_json(parsed: Any, source_url: str = "", source_name: str = "browser") -> list[RawJob]: ...
```

Then both `AdaptiveScraper._parse_agent_result()` and `ScraplingFetcher._parse_and_sanitize()`
call these shared functions. `AdaptiveScraper` keeps its method but delegates internally.

---

## Pillar 2: Changes to Existing Files

### Task 2.1 — `backend/scraping/orchestrator.py`

**Changes:**
1. Add `scrapling_fetcher: ScraplingFetcher | None = None` to `__init__` params
2. In Phase 2 loop, add tier routing logic before the existing `adaptive_scraper` call:

```python
# Tier 1 sites that Scrapling can handle
TIER1_SITES = {"linkedin", "indeed", "google_jobs", "welcome_to_the_jungle", "glassdoor"}

# In Phase 2 loop, per source:
if self.scrapling_fetcher and source.name in TIER1_SITES:
    try:
        jobs = await self.scrapling_fetcher.scrape_job_listings(...)
        if jobs:  # success — skip Tier 2
            source_jobs.extend(jobs)
            continue
        # 0 jobs — fall through to Tier 2
    except Exception as exc:
        logger.warning("Tier 1 failed for %s: %s — falling back to browser-use", source.name, exc)

# Existing Tier 2 code (AdaptiveScraper) runs as fallback
jobs = await self.adaptive_scraper.scrape_job_listings(...)
```

3. Phase 3 (lab sources) — unchanged, stays on `adaptive_scraper`

**No other changes to orchestrator logic.** Delays, progress broadcasts, deduplication all stay.

### Task 2.2 — `backend/scraping/site_prompts.py`

**Add two new dictionaries** (existing `SITE_PROMPTS` and `SITE_CONFIGS` stay untouched):

```python
# CSS selectors to scope content before cleaning — reduces HTML size dramatically
SITE_CONTENT_SELECTORS: dict[str, str] = {
    "linkedin": ".jobs-search-results-list, .scaffold-layout__list",
    "indeed": "#mosaic-jobResults, .jobsearch-ResultsList",
    "google_jobs": ".gws-plugins-horizon-jobs__tl-lvc",
    "welcome_to_the_jungle": "[data-testid='search-results-list-item-wrapper']",
    "glassdoor": ".JobsList_wrapper",
}

# Extraction-only prompts — no navigation, just "read this content"
EXTRACTION_PROMPTS: dict[str, str] = {
    "default": """...""",  # generic extraction prompt
    "linkedin": """...""",  # LinkedIn-specific hints (job IDs in data attributes)
}
```

**SITE_CONFIGS addition** — add a `tier` field to each config:

```python
"linkedin": {
    ...existing fields...
    "tier": 1,  # Scrapling fetch + single LLM call
},
"lab_website": {
    ...existing fields...
    "tier": 2,  # Full browser-use agent
},
```

### Task 2.3 — `backend/scraping/adaptive_scraper.py`

**Minimal refactor:**
1. Move `_extract_json_from_text()` to `backend/scraping/json_utils.py`
2. Import it back: `from backend.scraping.json_utils import extract_json_from_text`
3. Extract shared job-parsing logic to `parse_jobs_from_json()` in same file
4. `_parse_agent_result()` delegates to the shared function

**No behavioral changes.** AdaptiveScraper continues to work exactly as before.

### Task 2.4 — `backend/config.py`

Add one setting:

```python
SCRAPLING_ENABLED: bool = True  # Feature flag: enable Tier 1 Scrapling fetcher
```

### Task 2.5 — App startup / dependency injection

**File:** `backend/main.py` (or wherever `ScrapingOrchestrator` is instantiated)

Add `ScraplingFetcher` to the dependency injection:

```python
from backend.scraping.scrapling_fetcher import ScraplingFetcher

# During app startup:
if settings.SCRAPLING_ENABLED:
    scrapling_fetcher = ScraplingFetcher(gemini_client=gemini_client)
else:
    scrapling_fetcher = None

orchestrator = ScrapingOrchestrator(
    adzuna_client=adzuna_client,
    adaptive_scraper=adaptive_scraper,
    session_mgr=session_mgr,
    deduplicator=deduplicator,
    scrapling_fetcher=scrapling_fetcher,  # NEW
)
```

### Task 2.6 — `pyproject.toml`

Add dependencies:

```toml
dependencies = [
    ...existing...
    "scrapling[fetchers]>=0.4",
    "markdownify>=0.14",
]
```

**Post-install:** Run `scrapling install` to download browser binaries (for StealthyFetcher).

---

## Pillar 3: Shared Utilities Extraction

### Task 3.1 — `backend/scraping/json_utils.py`

**New file.** Shared JSON extraction and job parsing used by both tiers.

```python
"""Shared JSON extraction and job-parsing utilities for all scraper tiers."""

from typing import Any
from backend.models.schemas import RawJob

def extract_json_from_text(text: str) -> Any:
    """Robustly extract JSON from LLM output. Moved from adaptive_scraper.py."""
    ...

def parse_jobs_from_json(
    parsed: Any,
    source_url: str = "",
    source_name: str = "browser",
) -> list[RawJob]:
    """Convert parsed JSON (list or dict) into sanitized RawJob objects.
    Extracted from AdaptiveScraper._parse_agent_result() for reuse."""
    ...
```

---

## Pillar 4: Playwright Version Compatibility

### Task 4.1 — Verify dependency compatibility

Before implementation, verify that `scrapling[fetchers]` can coexist with `browser-use`:

```bash
pip install scrapling[fetchers] --dry-run
```

**If conflict exists** (pinned playwright versions clash):
- Option A: Pin a compatible playwright version that both accept
- Option B: Use `scrapling` without `[fetchers]` extra — use only `Fetcher` (HTTP/curl_cffi, no browser) for sites that don't need JS rendering. For JS-required sites, keep browser-use as Tier 2.
- Option C: Install scrapling in isolation, use only its HTML parsing/cleaning capabilities, and do the fetching ourselves with existing Playwright

**Decision:** Resolve at implementation time based on actual version output. Document the chosen path.

---

## Implementation Order

Execute in this sequence (each step is independently testable):

| Step | Task | Files | Depends On |
|------|------|-------|------------|
| 1 | Verify Playwright compatibility | pyproject.toml | — |
| 2 | Add scrapling + markdownify deps | pyproject.toml | Step 1 |
| 3 | Extract shared JSON utils | json_utils.py (new), adaptive_scraper.py | — |
| 4 | Add content selectors + extraction prompts | site_prompts.py | — |
| 5 | Add SCRAPLING_ENABLED setting | config.py | — |
| 6 | Implement ScraplingFetcher | scrapling_fetcher.py (new) | Steps 2, 3, 4 |
| 7 | Wire into orchestrator | orchestrator.py | Step 6 |
| 8 | Wire into app startup | main.py | Step 7 |
| 9 | Test Tier 1 with one site (e.g., WTTJ) | manual test | Step 8 |
| 10 | Test all Tier 1 sites + fallback | manual test | Step 9 |
| 11 | Add tier field to SITE_CONFIGS | site_prompts.py | Step 10 |

Steps 3, 4, 5 can be done in parallel (no dependencies between them).

---

## Testing Strategy

### Manual verification (priority)
- Run morning batch with `SCRAPLING_ENABLED=True`
- Compare job counts and quality vs old approach (run both, diff results)
- Verify fallback triggers when Scrapling fetch fails (simulate by passing bad URL)

### Unit tests
- `test_clean_html`: verify HTML stripping produces expected markdown
- `test_extract_json_from_text`: already tested implicitly, add explicit cases for shared util
- `test_parse_jobs_from_json`: verify sanitization, field mapping
- `test_scrapling_fetcher_fallback`: mock fetch failure → returns empty list (triggers Tier 2)

### Integration test
- `test_tier_routing`: mock orchestrator with both fetchers, verify Tier 1 sites go to ScraplingFetcher, lab sites go to AdaptiveScraper

---

## Rollback

- Set `SCRAPLING_ENABLED=False` in config → entire system reverts to current behavior
- No database changes, no schema migrations, no data format changes
- ScraplingFetcher is additive — removing it leaves the codebase functional

---

## Files Changed Summary

| File | Action | Description |
|------|--------|-------------|
| `backend/scraping/scrapling_fetcher.py` | **NEW** | Tier 1 scraper: Scrapling fetch + single Gemini call |
| `backend/scraping/json_utils.py` | **NEW** | Shared JSON extraction + job parsing |
| `backend/scraping/site_prompts.py` | MODIFY | Add `EXTRACTION_PROMPTS`, `SITE_CONTENT_SELECTORS`, `tier` field |
| `backend/scraping/adaptive_scraper.py` | MODIFY | Delegate to shared `json_utils` functions |
| `backend/scraping/orchestrator.py` | MODIFY | Add `scrapling_fetcher` param, tier routing in Phase 2 |
| `backend/config.py` | MODIFY | Add `SCRAPLING_ENABLED` setting |
| `backend/main.py` | MODIFY | Instantiate and inject `ScraplingFetcher` |
| `pyproject.toml` | MODIFY | Add `scrapling[fetchers]`, `markdownify` deps |

**No changes:** session_manager.py, gemini_client.py, deduplicator.py, database.py, models/, security/
