# TY-03 — Add return-type annotations to all API route handlers

> Category: type-safety · Effort: M · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
63 functions lack return annotations; the API routers are the worst. `settings.py` is the standout: **15 of 17 functions** lack return annotations, and **5+ routes** have neither annotation NOR `response_model`, so they emit untyped raw dicts the frontend depends on.

## Why it matters (ship)
Route handlers are the public contract. Annotations make them self-documenting and let Pyright verify the body actually returns the declared model.

## Locations
- **`settings.py` (worst)** — no `response_model` AND no annotation: `get_sources` (L343), `update_sources` (L370), `toggle_site` (L552), `save_credential` (L630), `clear_session` (L678), `delete_custom_site` (L760). Has `response_model`, missing annotation: L163, L194, L245, L265, L389, L521, L588, L707, L733.
- **Others (have `response_model`, missing `->`)**: `queue.py` L88,134,150,170,181,219,245,271; `documents.py` L78,91,106,150,194,224; `jobs.py` L79,137,178,250; `applications.py` L137,159,227,268,328,434; `analytics.py` L71,119.

## Proposed change
For the 5+ untyped-dict `settings.py` routes, define small Pydantic `*Out` models (`SourcesStatusOut`, `MessageOut`, …) and add both `response_model=` and `-> Model`. For the rest, add `-> <existing response_model type>`. For file endpoints, annotate with the Starlette response type (`FileResponse`/`StreamingResponse`).

## Acceptance criteria
- [ ] Every route handler has a return annotation
- [ ] No route returns a bare untyped `dict` (new `*Out` models where needed)
- [ ] `pyright` clean; response JSON shapes verified against frontend expectations

## Blast radius & risk
Medium — new response models for the dict routes; verify JSON shape matches what the frontend reads (docstrings already document the keys, e.g. `get_sources` at `settings.py:349-366`).

## Dependencies
**Requires TY-01.**
