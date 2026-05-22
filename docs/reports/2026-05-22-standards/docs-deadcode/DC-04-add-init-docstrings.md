# DC-04 — Add missing `__init__` / validator docstrings

> Category: docs · Effort: M · Risk: none · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Docstring coverage is otherwise excellent (100% modules, near-100% classes/functions). The one consistent gap is `__init__` constructors across LLM/scraping/matching, plus two Pydantic `@field_validator` methods.

## Why it matters (ship)
Closes the last documentation gaps for a clean, professional codebase; constructors are where collaborators/config are wired, so they benefit most from a one-line contract.

## Locations
- Constructors: `backend/llm/cv_editor.py:60`, `llm/job_analyzer.py:38`, `llm/gemini_client.py:111`, `llm/cv_modifier.py:72`, `matching/embedder.py:33`, `scraping/orchestrator.py:108`, `scraping/scrapling_fetcher.py:64`, `scraping/adzuna_client.py:41`, `scraping/session_manager.py:75`, `scraping/adaptive_scraper.py:56`, `api/ws.py:68`
- Field validators: `backend/api/applications.py:413` (`validate_url`), `:422` (`validate_json`)

## Proposed change
Add concise docstrings consistent with the project's existing convention. **Skip** the intentional import-guard fallback shims (`ws.py:31-52`, `ws_models.py:25-39`, `morning_batch.py:77,80`) — those are not defects.

## Acceptance criteria
- [ ] Each listed constructor/validator has a one-line-or-better docstring
- [ ] Fallback shims left untouched
- [ ] Style matches surrounding docstrings

## Blast radius & risk
Docs only; zero runtime risk.

## Dependencies
None.
