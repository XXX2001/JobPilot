# NM-03 — Unify "site" vs "source" terminology (internal identifiers only)

> Category: naming · Effort: L · Risk: medium-high · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
One concept — a job board (LinkedIn, Indeed, WTTJ…) — is named three ways: `site` / `site_key` / `site_name` (~300 hits) vs `source` / `source_name` / `source_id` (DB model + schemas + utils). A reader can't tell that `site_key`, `site_name`, and `source_name` refer to the same thing.

## Why it matters (ship)
Inconsistent domain vocabulary is a top readability/maintainability smell and a frequent source of bugs when the two halves drift.

## Locations
- `_site_key()` def: `applier/assisted_apply.py:51`, `applier/auto_apply.py:50`; usages `applier/captcha_handler.py:100-102,275-276`, `form_filler.py:121-122,301-302`
- `SiteCredential.site_name`: `models/user.py:100,112`
- API: `api/settings.py:479,551-559,629-643`; ws field `site` `api/ws_models.py:103,113,165,174`
- `source_name`: `models/schemas.py:48`, `utils/source_health.py:107-147`; `JobSource`/`source_id` `models/job.py:46-71`
- `SITE_CONFIGS` constant: `scraping/site_prompts.py`

## Proposed change
Standardize **internal** identifiers on **`source`** (matches the `JobSource` ORM model + `job_sources` table). Rename internal locals/helpers/constants: `_site_key → _source_key`, `SITE_CONFIGS → SOURCE_CONFIGS`, internal `site_name`/`site` params → `source_*` where not wire-bound.

## Acceptance criteria
- [ ] Internal helpers/constants/locals use `source` consistently
- [ ] Wire/DB names unchanged (see risk); a short mapping note added to `docs/modules/`
- [ ] Full test suite passes

## Blast radius & risk
Large (~70 refs, 8+ files). **Preserve these external contracts** (rename requires migration, not code edit): DB tables/columns `job_sources`, `jobs.source_id`, `site_credentials.site_name` (`models/job.py:46,71`, `models/user.py:109,112`); HTTP paths `/api/settings/sites/{site_name}`, `/credentials/{site_name}` (`settings.py:551,629`); WebSocket JSON field `site` (`ws_models.py`, consumed by frontend). **Scope this task to internal identifiers only.** Lowest-priority rename due to contract surface.

## Dependencies
Do after NM-01/NM-02 to avoid overlapping churn. Coordinate with ST-06 (site registry).
