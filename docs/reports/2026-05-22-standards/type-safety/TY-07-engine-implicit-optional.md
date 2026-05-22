# TY-07 — Fix the `model: str = None` implicit-Optional in `engine.py`

> Category: type-safety · Effort: S · Risk: very low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`engine.py:80` declares `model: str = None` — the annotation says `str` but the default is `None`. This is an outright incorrect type (a genuine correctness bug, not just style) that strict checking rejects.

## Why it matters (ship)
A real type lie on a constructor parameter; downstream code may assume `self.model` is always a `str`.

## Locations
- `backend/applier/engine.py:80` (`__init__`, param `model: str = None`)

## Proposed change
Change to `model: str | None = None` and handle the `None` case at point of use — or give it a real default string if `None` is never intended. Trace the one path that reads `self.model` to confirm.

## Acceptance criteria
- [ ] Annotation matches the default
- [ ] `self.model` usage handles `None` (or a non-None default is set)
- [ ] `pyright` clean; tests pass

## Blast radius & risk
Very low — check the single call path reading `self.model`.

## Dependencies
None (more visible once TY-01 is on).
