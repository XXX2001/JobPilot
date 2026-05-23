# DC-01 — Remove the never-started APScheduler scaffolding

> Category: dead-code · Effort: S · Risk: low (verify before delete) · Ship-blocker: no (high priority)
> Part of: [Naming & Standards backlog](../INDEX.md) · Relates to code-review **HR-01**

## Problem
`MorningBatchRunner` instantiates `self._scheduler` but never uses it (the only reference is its own assignment). `CronTrigger` is imported but never referenced. The module docstring states "scheduler.start() is intentionally never called." This is dead infrastructure — an entire optional `apscheduler` dependency — for a feature that was never built, and it implies scheduling exists when all runs are on-demand via `POST /api/queue/refresh`.

## Why it matters (ship)
Dead scaffolding misleads readers/operators into thinking scheduled runs happen. Either build scheduling or remove the pretense before shipping.

## Locations
- `backend/scheduler/morning_batch.py:203` (`self._scheduler = AsyncIOScheduler() …` "never started; kept for future use")
- `:85-93` defensive `apscheduler` import block (`AsyncIOScheduler`, `CronTrigger`, `_APSCHEDULER_AVAILABLE`)
- `:22-24` docstring note

## Proposed change
Remove `self._scheduler`, the `CronTrigger` import, and the now-unneeded `_APSCHEDULER_AVAILABLE`/`AsyncIOScheduler` scaffolding; trim the docstring note. If `apscheduler` is otherwise unused, drop it from dependencies. (If the owner *wants* scheduling, that's a separate feature task — not this cleanup.)

## Acceptance criteria
- [ ] No `_scheduler` / `AsyncIOScheduler` / `CronTrigger` / `_APSCHEDULER_AVAILABLE` references remain
- [ ] `apscheduler` removed from deps if unused (check `pyproject.toml`/`uv.lock`)
- [ ] App boots; `POST /api/queue/refresh` still runs the batch; tests pass

## Blast radius & risk
**Candidate — verify before delete.** Re-grep `_scheduler`, `AsyncIOScheduler`, `CronTrigger`, `_APSCHEDULER_AVAILABLE` across repo and check deps before removing the dependency. No runtime path uses it today.

## Dependencies
Touches the same module as NM-01/NM-04 — bundle if convenient.
