# RG-01 — Fix or finish the `/regenerate` documents endpoint

> Category: feature/naming · Effort: M · Risk: medium · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md) · Recurs as code-review **HR-02**

## Problem
`regenerate_documents` returns `{"status": "queued", "message": "Document regeneration has been queued"}` but **no background work is ever scheduled**. It only deletes existing docs when `force=True`, then tells the client regeneration is in flight. The injected `background_tasks: BackgroundTasks` is never used, and `documents.py` never imports/calls the CV/Letter pipeline. The function name and docstring promise behavior the body doesn't deliver — both a broken feature and a misleading name.

## Why it matters (ship)
Users click "Regenerate", get a success toast, and their documents never change. Shipping a button that silently does nothing is a release blocker.

## Locations
- Endpoint: `backend/api/documents.py:223-267`
- Unused param: `:227` `background_tasks`; import `:22` `BackgroundTasks` (becomes unused if param removed)
- Misleading docstring: `:235` "(injected, available for future use)"
- Misleading response: `:263-267`
- Stale "Called by" claim in pipeline docstring: `backend/latex/pipeline.py:26` (see DC-03)

## Proposed change
Decide with owner:
- **(A) Implement** — `background_tasks.add_task(...)` invoking the CV/Letter pipeline (`CVPipeline`/`LetterPipeline` from `request.app.state`), and return an honest status. Rename `regenerate_documents` to stay accurate.
- **(B) Defer** — if regeneration is out of scope for this release: remove the unused `background_tasks` param + import, rename the function to match reality (e.g. `clear_documents_for_regeneration` / `invalidate_documents`), and change the response message to state what actually happens (docs deleted; regenerated on next access).

## Acceptance criteria
- [ ] Response message matches actual behavior (no false "queued")
- [ ] If (A): documents are actually regenerated; if (B): no unused `BackgroundTasks` import/param and the name reflects behavior
- [ ] `tests/test_api_routes.py:135-138` updated and passing

## Blast radius & risk
**Keep the HTTP route path** `/api/documents/{match_id}/regenerate` — it's an external contract (frontend + `test_api_routes.py`). Only rename the Python function and/or fix behavior. Verify the frontend's expectation of the response shape before changing the message.

## Dependencies
Coordinate with DC-03 (stale "Called by" docstring resolves once this is settled).
