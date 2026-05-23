# ST-06 — Move hardcoded job-board URLs into the site-config registry

> Category: structure · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Search/login/base URLs are duplicated between `scrapling_fetcher.py`, `session_manager.py`, and the `SITE_CONFIGS` registry in `site_prompts.py`. The Adzuna base URL appears in both `adzuna_client.py` and `site_prompts.py`. Duplicated endpoints drift when a board changes its URL scheme.

## Why it matters (ship)
A board URL change should be a one-line edit, not a hunt across modules; drift here silently breaks scraping/login.

## Locations
- `scraping/scrapling_fetcher.py:264` (LinkedIn search URL), `:291` (WTTJ search URL)
- `scraping/session_manager.py:379,405` (LinkedIn/Indeed login URLs)
- `scraping/adzuna_client.py:39` (`BASE_URL`) duplicated at `scraping/site_prompts.py:469`
- Further base/login URLs at `site_prompts.py:349-469`

## Proposed change
Make `SITE_CONFIGS` (or a dedicated registry) the single source for `base_url` / `login_url` / search-URL templates; have the fetcher and session manager read from it. Move shared endpoints to the registry rather than `defaults.py` if they're board-specific.

## Acceptance criteria
- [ ] Each board's URLs defined once in the registry
- [ ] Fetcher + session manager + adzuna client read from the single source
- [ ] URL templates (`{keywords}`, `{jobId}`) preserved; scraping/login tests pass

## Blast radius & risk
Medium — touches scraping/login flows. Preserve template substitution exactly.

## Dependencies
Coordinate with NM-03 (`SITE_CONFIGS` → `SOURCE_CONFIGS` rename) — do the rename and this together or sequence them.
