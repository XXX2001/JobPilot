# EH-08 — Retain reference + done-callback for the fire-and-forget batch task

> Category: error-handling · Effort: S · Risk: very low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`asyncio.create_task(_run())` is not stored, so the task can be garbage-collected before completion ("Task was destroyed but it is pending"), and there's no done-callback to surface exceptions raised outside the inner `try`. The inner `_run` logs `Batch run error` at `error` without `exc_info`.

## Why it matters (ship)
A dropped/crashed background refresh can vanish without a trace, leaving the queue silently un-refreshed.

## Locations
- `backend/api/queue.py:170-176` (`asyncio.create_task(_run())`)

## Proposed change
Store the task on `app.state` (or a module-level set) and attach a done-callback that logs exceptions with `exc_info=True`. Add `exc_info=True` to the existing `logger.error`.

## Acceptance criteria
- [ ] Task reference is retained until completion
- [ ] Unhandled task exceptions are logged with stack via a done-callback
- [ ] No "Task was destroyed but it is pending" warnings under load

## Blast radius & risk
Very low — additive.

## Dependencies
None.
