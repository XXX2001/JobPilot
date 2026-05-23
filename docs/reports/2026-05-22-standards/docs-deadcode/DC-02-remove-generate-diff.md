# DC-02 — Remove the unused legacy `generate_diff` helper

> Category: dead-code · Effort: S · Risk: low (verify before delete) · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`generate_diff` is a public, ~48-line function documented as "legacy … retained for backward compatibility with older callers," but it has **zero callers** (confirmed via codegraph "No callers found" + repo-wide grep across Python and TS/JS). The active pipeline builds `DiffEntry` objects directly (`pipeline.py:177,217`).

## Why it matters (ship)
Dead public surface that misleads readers into thinking a legacy path is live, and adds maintenance weight.

## Locations
- `backend/latex/pipeline.py:350-397` (function body)
- `:20` module docstring mention "plus the legacy generate_diff helper"

## Proposed change
Delete `generate_diff` and remove the docstring sentence referencing it. **Keep `DiffEntry`** (it is used and must stay).

## Acceptance criteria
- [ ] `generate_diff` removed; `DiffEntry` retained
- [ ] Docstring updated
- [ ] Full-repo grep for `generate_diff` (incl. non-`.py` and dynamic `getattr`) is clean; tests pass

## Blast radius & risk
**Candidate — verify before delete.** No callers per codegraph + grep, but run a final whole-repo `generate_diff` grep before deletion.

## Dependencies
Do before TY-04 (which references param annotations on `generate_diff:350`) — if removed, that sub-item drops.
