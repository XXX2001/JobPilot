# DC-03 — Fix the stale "Called by" reference in `pipeline.py` docstring

> Category: docs · Effort: S · Risk: none · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
The `pipeline.py` module docstring claims it is "Called by: backend.api.documents (regenerate endpoint)," but `documents.py` does not import `CVPipeline`/`LetterPipeline` or call anything in `pipeline.py` (it only imports `LaTeXParser` at `:97` for a different purpose). Misleading comment for anyone tracing the regeneration flow.

## Why it matters (ship)
Stale call-graph comments actively mislead maintainers during debugging.

## Locations
- `backend/latex/pipeline.py:26` ("backend.api.documents (regenerate endpoint),")

## Proposed change
Remove the `backend.api.documents` line from the "Called by" list — **or** correct it once RG-01 actually wires the regenerate endpoint to the pipeline.

## Acceptance criteria
- [ ] Docstring "Called by" list reflects real callers
- [ ] Wording coordinated with RG-01's outcome

## Blast radius & risk
Comment-only; no runtime impact.

## Dependencies
Coordinate with RG-01.
