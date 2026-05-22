# EH-09 — Make silent except-blocks observable (logging only)

> Category: error-handling · Effort: S · Risk: very low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Several `except Exception: pass` / `logger.debug`-only blocks hide real failures behind graceful defaults. Each is individually small; bundled here as one "add visibility" pass. **Keep the graceful fallback behavior — only add logging** so failures stop being invisible.

## Why it matters (ship)
A broken column / parser / detector currently looks identical to "no data" or "not blocked," which masks regressions in production.

## Sites (one checklist, fix together)
1. **Analytics avg score** — `backend/api/analytics.py:107-108` (`except Exception: pass` → `logger.warning(..., exc_info=True)`; keep `None` fallback). Broken column currently looks like an empty dataset.
2. **Scraper JSON extractor** — `backend/scraping/json_utils.py:88-119` (4× `except json.JSONDecodeError: pass`) → on the final fallthrough (`:119`) log at `debug`/`warning` that text was present but no strategy parsed (bounded snippet). Consider bumping per-item skip `:205-207` from `debug` to `info`.
3. **CAPTCHA/block detection** — `backend/applier/captcha_handler.py:159-160`, `:171-172` (`except Exception: pass`) → log the detection failure before returning `False`; `:121-122` bump from `debug`.
4. **Startup / migration / static-mount** — `backend/database.py:148-149` (column-migration `debug` → `warning`+`exc_info`, log per-statement); `backend/main.py:243-245` (router import `debug` → `warning`/`error`+`exc_info`), `:388-389` (static mount).
5. **LaTeX parser fallback** — `backend/latex/parser.py:92-94` (`except Exception: pass` "if TexSoup unavailable or parsing fails") → catch `ImportError` separately (`debug`: optional dep missing) and log other exceptions at `warning`+`exc_info`.

## Acceptance criteria
- [ ] Each site emits a log on failure (level per above); fallback behavior unchanged
- [ ] No bare `except Exception: pass` remains at these sites
- [ ] Log volume stays bounded (snippet length capped for the JSON extractor)

## Blast radius & risk
Very low — logging-only. Watch JSON-extractor log volume.

## Dependencies
None. (Distinct from EH-01/EH-02, which change *behavior*, not just logging.)
