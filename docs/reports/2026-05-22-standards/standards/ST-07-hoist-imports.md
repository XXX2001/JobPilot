# ST-07 — Hoist stdlib/intra-package imports to module top level

> Category: structure · Effort: S · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Many functions re-import stdlib modules (`json`, `hashlib`, `base64`, `webbrowser`, `platform`, `asyncio`, `tempfile`, `re`) on every call, sometimes with ad-hoc aliases (`_json`, `_json2`, `_re`, `_platform`). These have no circular-import or optional-dependency justification — they just hurt readability and consistency.

## Why it matters (ship)
Idiomatic, predictable import structure; removes confusing underscore-aliased re-imports.

## Locations
- `api/applications.py:424,480,489` (`json`/`_json`/`_json2`); `api/jobs.py:188` (`hashlib`); `api/queue.py:161` (`asyncio`)
- `scheduler/morning_batch.py:400` (`re as _re`), `:515` (`hashlib`)
- `scraping/scrapling_fetcher.py:188` (`json as _json`)
- `applier/form_filler.py:125` (`platform as _platform`), `:216` (`base64`); `applier/auto_apply.py:301` (`webbrowser`), `:386` (`base64`); `applier/assisted_apply.py:156` (`webbrowser`)
- `latex/validator.py:73,74` (`asyncio`, `tempfile`)

## Proposed change
Move these to the top-of-file import block; drop the underscore aliases. **Leave** genuinely deferred imports (`playwright`, `cryptography`, `texsoup`, and the `main.py` lifespan/circular-avoidance imports) local.

## Acceptance criteria
- [ ] Listed imports moved to module top; no underscore-aliased stdlib re-imports remain
- [ ] Optional/heavy/circular-risk imports left local
- [ ] App imports cleanly (no new circular-import errors); tests pass

## Blast radius & risk
Low — mechanical. Watch for any import that was local specifically to dodge a circular import (verify `morning_batch`, `applications`).

## Dependencies
None.
