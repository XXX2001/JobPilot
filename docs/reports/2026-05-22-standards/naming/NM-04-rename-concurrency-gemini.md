# NM-04 — Rename `CONCURRENCY_GEMINI` → `GEMINI_MAX_CONCURRENCY`

> Category: naming · Effort: S · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
The constant `CONCURRENCY_GEMINI` reads like reversed-order developer shorthand, and its docstring still references "the morning batch runner."

## Why it matters (ship)
It lives in `defaults.py`, a config surface that ships; a clear, conventional name (noun-adjective order, `MAX_` prefix) reads better.

## Locations
- Definition: `backend/defaults.py:37`
- Import + use: `backend/scheduler/morning_batch.py:60,395`

## Proposed change
Rename to `GEMINI_MAX_CONCURRENCY` (or `MAX_CONCURRENT_GEMINI_CALLS`). Update the docstring to drop the "morning batch" reference.

## Acceptance criteria
- [ ] No `CONCURRENCY_GEMINI` remains
- [ ] Docstring no longer says "morning batch"
- [ ] Import resolves; tests pass

## Blast radius & risk
2 files, 3 refs. No external contract. Trivial.

## Dependencies
Pairs naturally with NM-01 (same module touched).
