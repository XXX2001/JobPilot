# TY-08 — Review the 10 coded `# type: ignore[...]` for masked bugs

> Category: type-safety · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Beyond the import-level ignores (TY-02), 10 ignores carry a specific error code — these are the ones most likely to hide real bugs once checking is on.

## Why it matters (ship)
Each coded ignore is a suppressed real error; some point at genuine smells (a name that isn't in scope, a SQLAlchemy comparison that should be properly typed).

## Locations (notable ones first)
- `backend/scraping/session_manager.py:328` `# type: ignore[name-defined]` — suggests `AsyncSessionLocal` isn't imported/defined in scope (**real smell**)
- `backend/applier/daily_limit.py:63` `# type: ignore[operator]` — SQLAlchemy datetime comparison; likely fixed via `Mapped[datetime]` typing, not suppression
- `backend/scraping/adaptive_scraper.py:59` `def _make_llm(self): # type: ignore[return]` — untyped function suppressing a return-path error
- `backend/database.py:92` `# type: ignore[override]` on `db_session`
- Others: `backend/main.py:214`; `backend/utils/retry.py:76,78`; `backend/scheduler/morning_batch.py:77,81`; `backend/scraping/session_manager.py:116`

## Proposed change
After TY-01, address each at the source: import/define `AsyncSessionLocal` properly, add `Mapped[datetime]` for the comparison, annotate `_make_llm`'s return type, etc. Keep an ignore only where a library genuinely lacks correct stubs — with a comment explaining why.

## Acceptance criteria
- [ ] Each coded ignore is either removed (root cause fixed) or annotated with a justifying comment
- [ ] `session_manager.py:328` name-defined issue resolved (not suppressed)
- [ ] `pyright` clean

## Blast radius & risk
Medium — touches DB/session/scraper internals; verify each individually. Do **last**, after TY-01 surfaces the underlying errors.

## Dependencies
Requires TY-01; do after TY-02.
