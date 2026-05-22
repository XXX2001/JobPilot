# EH-07 — Replace generic `RuntimeError` with typed domain exceptions in the apply flow

> Category: error-handling · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
The applier raises bare `RuntimeError` for distinct, catchable conditions (unresolved CAPTCHA, confirmation timeout, user-cancelled login). Callers must catch broad `Exception` or string-match messages to tell them apart — brittle, and it pushes everything into the over-broad handlers elsewhere. The codebase already has domain exceptions (`GeminiRateLimitError`, `GeminiJSONError`, `LaTeXCompilationError`, `AdzunaAPIError`, `DailyLimitExceeded`); these spots are inconsistent.

## Why it matters (ship)
User-cancel and timeout are normal outcomes, not bugs — they should be distinguishable from genuine errors so the UI and logs treat them correctly.

## Locations
- `backend/applier/form_filler.py:161` (CAPTCHA unresolved), `:251` (confirmation timeout)
- `backend/scraping/session_manager.py:308` (login cancelled by user)

## Proposed change
Introduce typed exceptions (e.g. `CaptchaUnresolvedError`, `ApplyConfirmationTimeout`, `LoginCancelledError`) in the appropriate module, raise them at these sites, and catch them specifically in `auto_apply.py` / `engine.py` where these flows are handled.

## Acceptance criteria
- [ ] No bare `RuntimeError` for these three conditions
- [ ] The corresponding `except` sites catch the typed exceptions specifically
- [ ] User-cancel/timeout produce a distinct, non-error result; tests cover each

## Blast radius & risk
Medium — must update the broad `except` sites in `auto_apply.py`/`engine.py` that currently rely on catching `Exception`/string-matching.

## Dependencies
Pairs with EH-01 (typed `ApplicationRecordError`) and EH-06 (so the broad catches can narrow).
