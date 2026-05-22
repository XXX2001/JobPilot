# NM-02 — Drop the `jobpilot_*` brand prefix on config fields

> Category: naming · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Internal `Settings` fields carry the product brand: `settings.jobpilot_data_dir`, `jobpilot_host`, `jobpilot_port`, `jobpilot_log_level`, `jobpilot_scraper_headless` — referenced ~35 times. Brand-prefixing every internal field is redundant and leaks the product name into every path-building call site.

## Why it matters (ship)
Cleaner, idiomatic config access (`settings.data_dir` vs `settings.jobpilot_data_dir`) reads more professionally and matches how pydantic-settings is normally used.

## Locations
- Definitions: `backend/config.py:49-53` (+`DATA_DIR` at `:89`, DB URL `database.py:43`)
- Sample refs: `applier/assisted_apply.py:178`, `applier/captcha_handler.py:39-40`, `applier/form_filler.py:122,302`, `applier/auto_apply.py:311`, `scheduler/morning_batch.py:265,403`, `scraping/adaptive_scraper.py:137,146,225`, `scraping/scrapling_fetcher.py:105,168,176`, `scraping/session_manager.py:78-79`, `api/settings.py:688`, `api/applications.py:547`; tests `test_windows_playwright.py:202,257`, `test_morning_batch.py:97`, `test_config_scraper_headless.py:18-26`, `test_smoke.py:10`

## Proposed change
Rename the Python attributes: `jobpilot_data_dir → data_dir`, `jobpilot_host → host`, `jobpilot_port → port`, `jobpilot_log_level → log_level`, `jobpilot_scraper_headless → scraper_headless`. **Keep the `env=` bindings unchanged.**

## Acceptance criteria
- [ ] No `settings.jobpilot_` access remains
- [ ] `env="JOBPILOT_HOST"` / `JOBPILOT_PORT` / `JOBPILOT_LOG_LEVEL` / `JOBPILOT_SCRAPER_HEADLESS` / `JOBPILOT_DATA_DIR` strings **unchanged** (env contract preserved)
- [ ] `tests/test_config_scraper_headless.py` + `test_smoke.py` pass; app boots reading the same env vars

## Blast radius & risk
~15 files, ~35 refs. **Do NOT change**: the `env=` strings (env-var contract); the SQLite filename `jobpilot.db` in `database.py:43` (persisted user data); the `logging.getLogger("jobpilot")` channel; installer scripts; LaTeX `JOBPILOT:` markers — those are legitimate brand usage.

## Dependencies
None.
