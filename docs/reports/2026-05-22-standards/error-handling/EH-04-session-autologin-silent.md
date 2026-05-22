# EH-04 — Surface auto-login failures in `SessionManager`

> Category: error-handling · Effort: M · Risk: low · Ship-blocker: no (high priority)
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Several `except Exception` blocks in the auto-login path return `None`/`False` or swallow at `logger.debug`, so a persistently failing auto-login (bad credentials, selector drift, Fernet/key errors) looks identical to "no credentials configured." Login is a top failure mode for a scraping/applying bot and is currently near-invisible. Notably `:323` swallows even an `ImportError` of core modules (`config`, `database`, `Fernet`) and returns `None` with **no log at all**.

## Why it matters (ship)
Operators can't tell "login broke" from "no creds set" — the hardest-to-debug class of production failure for this product.

## Locations
- `backend/scraping/session_manager.py:323-324` (`return None`, no log)
- `:370-372` (`pass; return None`), `:399-400` / `:453-454` (`success = False`)
- `:426-427` / `:435-436` (`continue`), `:446-447` (`pass`)
- `:473-478` (`except Exception: … return None`)
- `:126-127`, `:155-158` (`logger.debug` swallows)

## Proposed change
At the **outer** handlers (`:323`, `:473`), log at `warning` with `exc_info=True` (use the exception **type**, not raw text, to avoid leaking credentials). Bump the import-failure path (`:323`) to at least one `warning`. Keep inner per-selector `continue` loops but make the overall failure audible. Keep existing `None` returns so flow is unchanged.

## Acceptance criteria
- [ ] A failed auto-login emits at least one `warning` (distinguishable from "no creds")
- [ ] No credential values appear in logs (type/identifier only)
- [ ] Flow/return values unchanged

## Blast radius & risk
Low — logging-level changes only.

## Dependencies
None.
