# TY-05 — Replace the bare `dict` apply-boundary return with a `TypedDict`

> Category: type-safety · Effort: M · Risk: low-medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`fill_and_submit` and `fill_only` return bare `-> dict` with a documented stable shape (`status`, `filled_fields`, `screenshot_b64` — see `form_filler.py:110`). This is the apply-flow result consumed by the engine — a loosely-typed internal boundary where a key typo is invisible. `_parse_gemini_response` and `fit_engine.to_dict` also return bare `dict`.

## Why it matters (ship)
The apply-result contract crosses module boundaries; a `TypedDict` catches mismatches between producer and consumer at check time.

## Locations
- `backend/applier/form_filler.py:96` (`fill_and_submit`), `:274` (`fill_only`), `:503` (`_parse_gemini_response`)
- `backend/matching/fit_engine.py:72` (`to_dict`)

## Proposed change
Define `class FillResult(TypedDict)` with `status: Literal["applied","cancelled"]`, `filled_fields: ...`, `screenshot_b64: str | None`; annotate both fill methods `-> FillResult`. For `fit_engine.to_dict`, prefer returning the existing Pydantic/dataclass via a typed `.model_dump()` shape or a `TypedDict`.

## Acceptance criteria
- [ ] `fill_and_submit`/`fill_only` return an annotated `FillResult`
- [ ] Engine consumers type-check against the `TypedDict` keys
- [ ] `pyright` clean; apply-flow tests pass (`tests/test_form_filler.py`, `test_apply_engine.py`)

## Blast radius & risk
Low-medium — touches the engine consumers; keys are already documented so the mapping is mechanical.

## Dependencies
Requires TY-01.
