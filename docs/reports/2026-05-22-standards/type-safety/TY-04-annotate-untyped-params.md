# TY-04 — Annotate untyped params (Playwright `page`, DI collaborators)

> Category: type-safety · Effort: S · Risk: very low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Key params are untyped, so call sites get no checking on the most error-prone objects in the app: the Playwright `page` in the apply flow, and the dependency-injected collaborators in the pipeline/embedder.

## Why it matters (ship)
`page` drives every browser interaction (`page.url`, `page.query_selector`, …); injected collaborators are core dependencies. Untyped here means typos and signature drift go uncaught.

## Locations
- **Playwright `page`** (untyped): `backend/applier/captcha_handler.py:109,125,144,177,186,234`
- **DI collaborators** (untyped): `backend/latex/pipeline.py:89` (`__init__`: job_analyzer, cv_modifier, cv_applicator), `:145` (`generate_tailored_cv`: fit_assessment), `:261` (`__init__`: cv_editor), `:350` (`generate_diff`: original_sections, edits — note DC-02 may delete this); `backend/matching/embedder.py:33` (`__init__`: gemini_client)

## Proposed change
- `page`: add `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from playwright.async_api import Page`, annotate `page: "Page"` (forward-ref string keeps Playwright a soft dependency).
- DI params: annotate with concrete classes (`JobAnalyzer`, `CVModifier`, `CVApplicator`, `CVEditor`, `GeminiClient`) and data params with their models (`FitAssessment`, `list[...]`). Add `-> None` to the `__init__`s.

## Acceptance criteria
- [ ] All 6 `page` params annotated via forward ref (no new runtime import)
- [ ] DI collaborator + data params annotated with concrete types
- [ ] `pyright` clean; app still imports without Playwright installed

## Blast radius & risk
Very low — annotations only. Classes already exist and are imported nearby.

## Dependencies
Requires TY-01. Check DC-02 first (`generate_diff` at `pipeline.py:350` may be removed).
