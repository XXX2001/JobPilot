# NM-01 — Rename `morning_batch` / `MorningBatchRunner` to a neutral product term

> Category: naming · Effort: M · Risk: medium · Ship-blocker: no (but owner's top priority)
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
The job-discovery pipeline is named `morning_batch` / `MorningBatchRunner`. This is casual, internal-shorthand naming **and factually misleading**: the pipeline is on-demand (triggered by `POST /api/queue/refresh`; `scheduler.start()` is never called), so nothing about it is tied to "morning."

## Why it matters (ship)
This is the single most unprofessional, domain-leaky name in the codebase and the owner explicitly called it out. It appears in class names, a module name, an orchestrator method, and many log strings users/operators may see.

## Locations
- Module: `backend/scheduler/morning_batch.py` (rename file → e.g. `batch_runner.py`)
- Class def: `backend/scheduler/morning_batch.py:150` `MorningBatchRunner`; `__all__` at `:646`
- Orchestrator method: `backend/scraping/orchestrator.py:122` `run_morning_batch()` (call at `backend/scheduler/morning_batch.py:282`); test refs `tests/test_scraping.py:102,155,173,192`, `tests/test_morning_batch.py:26`
- Importers of `MorningBatchRunner`: `backend/main.py:105,144` (+docstring `:15`); `backend/api/deps.py:35,76,78,80`
- **Mock-patch string targets** (must change in lockstep): `tests/test_morning_batch.py:84,85,93,94,139,140,185,186` (`patch("backend.scheduler.morning_batch.*")`); imports at `tests/test_morning_batch.py:10,55,60`, `tests/test_morning_batch_cv_fallback.py:8`
- Log/comment strings: "Morning batch started/failed/complete" across the module
- Test files to rename: `tests/test_morning_batch.py`, `tests/test_morning_batch_cv_fallback.py`
- Cosmetic "Called by: …morning_batch" doc-comments in ~12 files (matching/*, latex/*, orchestrator, database.py, api/ws.py, api/deps.py, scheduler/__init__.py)

## Proposed change
Pick one neutral name (suggest **`BatchRunner`** + module `batch_runner.py`, or `JobDiscoveryRunner`/`discovery_run.py`). Rename: module file, class, `run_morning_batch` → `run_scrape_batch`/`run_batch`, and all log/comment strings. Update every importer and the `__all__`.

## Acceptance criteria
- [ ] No identifier or string containing "morning" remains in `backend/` or `tests/` (`grep -ri morning backend tests` is clean)
- [ ] All `unittest.mock.patch("backend.scheduler.morning_batch...")` targets updated to the new module path
- [ ] `pytest tests/test_morning_batch*.py` (renamed) passes
- [ ] App boots; `POST /api/queue/refresh` still runs the batch

## Blast radius & risk
~10 source files + 3 test files, ~40 references. **No external contract touched** — HTTP route stays `/api/queue/refresh`; no DB column or env var named "morning"; `app.state.batch_runner` is already neutral (leave it). The 8 mock-patch string targets break silently if not updated with the module rename — do them together.

## Dependencies
None. (Touches the same module as DC-01 and NM-04 — consider doing those in the same PR.)
