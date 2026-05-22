# TY-02 — Remove the 47 `# type: ignore` comments on third-party imports

> Category: type-safety · Effort: M · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
47 of 87 `# type: ignore` are blanket ignores on `import`/`from` lines (fastapi, pydantic, sqlalchemy, browser_use, playwright_stealth, apscheduler, lxml, markdownify, cryptography, scrapling). With `reportMissingImports: false` they're dead noise; with checking on they over-suppress and hide real signature errors on those libs. They also normalize slapping ignores on imports.

## Why it matters (ship)
Clean, honest import block; real library type errors become visible.

## Locations (representative)
- `backend/main.py:41-46`, `:227-233`; `backend/database.py:31-32`; `backend/config.py:19-22,70`; `backend/api/ws_models.py:22`; `backend/scheduler/morning_batch.py:74,86-87`; `backend/scraping/scrapling_fetcher.py:209-215,311-313,330`; `backend/applier/{form_filler,assisted_apply,auto_apply,captcha_handler}.py` (multiple). Count: 47.

## Proposed change
After TY-01, install available stub packages and delete the import-level ignores. For genuinely stub-less optional deps, use `reportMissingTypeStubs`/`reportMissingModuleSource` config rather than per-line ignores. **Keep the `try/except ImportError` fallback-shim structure** (deliberate optional-dependency design) — only drop the redundant ignore comments.

## Acceptance criteria
- [ ] No `# type: ignore` on plain import lines (except where a documented reason is attached)
- [ ] `pyright` still clean on imports (stubs installed or configured)
- [ ] Fallback shims intact; app imports without optional deps

## Blast radius & risk
Low runtime risk; medium review surface. Must be done together with TY-01 or Pyright re-flags the imports.

## Dependencies
**Requires TY-01.**
