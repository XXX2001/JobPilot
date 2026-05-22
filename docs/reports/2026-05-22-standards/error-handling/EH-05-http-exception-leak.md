# EH-05 — Stop leaking raw exception text to HTTP clients

> Category: error-handling · Effort: S · Risk: low · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Two endpoints interpolate the caught exception directly into `HTTPException.detail` returned to the client. Internal error strings (file paths, API tokens embedded in URLs, SQL fragments, library internals) can leak to the frontend/users in production.

## Why it matters (ship)
Information disclosure — a basic pre-ship security hardening item.

## Locations
- `backend/api/jobs.py:210` — `detail=f"Adzuna search failed: {exc}"`
- `backend/api/queue.py:332` — `detail=f"Enrichment failed: {exc}"`

## Proposed change
Return a generic client message (e.g. `"Adzuna search failed"`), keep the full `exc` in the server log with `exc_info=True`. Audit for any other `detail=f"...{exc}"` patterns and apply the same rule.

## Acceptance criteria
- [ ] No raw `str(exc)` returned in any HTTP response body
- [ ] Full error retained server-side with stack
- [ ] Frontend still shows a sensible error (verify any code parsing `detail`)

## Blast radius & risk
Low — clients lose detailed error text (intended). Update any frontend that displays `detail` verbatim.

## Dependencies
None.
