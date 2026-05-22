# TY-06 — Standardize on `X | None` + lowercase generics (+ ruff `UP`)

> Category: type-safety · Effort: M · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Typing style is inconsistent: 177 `Optional[X]` vs 89 `X | None`; two files mix both in one module (`api/applications.py`, `llm/cv_editor.py`); `Dict[` survives in `ws.py` while the rest uses lowercase `dict`. Python is pinned `>=3.12`, so PEP 604 / PEP 585 syntax is fully available.

## Why it matters (ship)
The owner asked for *consistent* typing. One style reads more professionally and reduces diff noise.

## Locations
- `Optional[` in 18 files (177 uses); mixed-style: `backend/api/applications.py`, `backend/llm/cv_editor.py`
- `Dict[` at `backend/api/ws.py:72` (+1 more in the same file)

## Proposed change
Adopt `X | None` and lowercase `dict`/`list`/`tuple` (matches the majority + modern style). Add ruff rules `UP006`/`UP007`/`UP045` (current `[tool.ruff] select = ["E","F","I"]` → add `"UP"`) and run `ruff --fix`. Drop now-unused `Optional`/`Dict` imports.

## Acceptance criteria
- [ ] No `Optional[` or `Dict[`/`List[`/`Tuple[` remain in `backend/`
- [ ] ruff `UP` rules enabled and passing
- [ ] Unused `typing` imports removed; tests pass

## Blast radius & risk
Low runtime risk, large diff (~180 sites). Do as one mechanical autofix commit so review is easy. Run ruff `--fix` then verify.

## Dependencies
Requires TY-01 (so the result is verified). Best done after TY-02/03/04 to avoid churn collisions.
