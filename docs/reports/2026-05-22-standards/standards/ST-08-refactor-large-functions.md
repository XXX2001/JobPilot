# ST-08 — Refactor the largest multi-responsibility functions

> Category: structure · Effort: L · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Several functions exceed 130–210 real (non-comment) lines and mix orchestration, I/O, error handling, and persistence — hard to test and review before ship.

## Why it matters (ship)
These are core flows; their size makes regressions easy to introduce and hard to catch in review.

## Locations (code lines, not docstring-inflated)
- `scraping/orchestrator.py:122` `run_morning_batch` (~211) — also renamed by NM-01
- `applier/auto_apply.py:283` `_browser_use_apply` (~177)
- `applier/form_filler.py:83` `fill_and_submit` (~161)
- `scraping/site_prompts.py:503` `format_prompt` (~202)
- `scraping/session_manager.py:310` `_attempt_auto_login` (~171)
- `scheduler/morning_batch.py:253` `_run_batch_inner` (~144)
- `applier/assisted_apply.py:92` `apply` (~143)
- `api/applications.py:434` `apply_to_job` (~134)

## Proposed change
Extract cohesive sub-steps into named private helpers (e.g. split fetch / fill / submit / persist phases). **One function per follow-up PR.** Start with `run_morning_batch` and `_browser_use_apply`. Pure extraction with preserved behavior.

## Acceptance criteria
- [ ] Target function reduced to an orchestrator calling named helpers
- [ ] Behavior unchanged; existing tests (`tests/test_apply_engine.py`, `test_scraping.py`) still pass
- [ ] Each extracted helper independently testable

## Blast radius & risk
Medium — core flows. De-risk via existing tests + pure extraction. Tackle incrementally.

## Dependencies
Sequence after NM-01 (so `run_morning_batch` rename lands first) and ideally after TY-05 (apply-result `TypedDict` aids `fill_and_submit` extraction).
