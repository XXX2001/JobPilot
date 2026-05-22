# ST-05 — Migrate `datetime.utcnow()` → tz-aware + consolidate `_now()`

> Category: structure · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md) · Recurs as code-review **LR-01**

## Problem
`datetime.utcnow()` is deprecated as of Python 3.12 and returns **naive** datetimes, which causes silent timezone bugs in analytics windows and `applied_at`/`updated_at` columns. 14 occurrences across 7 files. Four identical `_now()` default factories are duplicated across model files. The codebase is only partially migrated — newer scraping/matching paths already use `datetime.now(timezone.utc)`.

## Why it matters (ship)
Deprecation + correctness: mixing naive and aware datetimes produces wrong ordering/window math that's hard to spot.

## Locations
- `applier/engine.py:268`
- Model `_now()` factories: `models/job.py:33`, `models/application.py:29`, `models/user.py:31`, `models/document.py:27`
- `utils/source_health.py:128,145`; `api/analytics.py:83,130,145`; `api/settings.py:187,188,236,671`

## Proposed change
Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`. Consolidate the four `_now()` factories into one shared util (e.g. `backend/utils/time.py`) imported by each model.

## Acceptance criteria
- [ ] No `datetime.utcnow()` remains in `backend/`
- [ ] Single shared `_now()`/`utcnow` helper used by all models
- [ ] Analytics window math + `applied_at`/`updated_at` verified against existing rows
- [ ] Tests pass

## Blast radius & risk
Medium — switching to aware datetimes can change comparisons against existing **naive** values stored in SQLite. Verify column round-trips and the analytics `- timedelta(...)` math; consider a one-time read-compatibility check.

## Dependencies
None. Coordinate with code-review MR-04 (`onupdate`) if tackled together.
