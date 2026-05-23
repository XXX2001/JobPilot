# TY-01 — Enable Pyright type checking + install stubs

> Category: type-safety · Effort: S (config) + surfaced backlog · Risk: low (config only) · Ship-blocker: no (foundational)
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`pyrightconfig.json` sets `typeCheckingMode: "off"` and `reportMissingImports: false` — **nothing is type-checked today**. The owner wants strong, consistent typing, but every other type-safety fix is unverifiable and can silently regress until checking is on. This is the root cause of most `# type: ignore` noise (87 total: 79 backend, 8 tests).

## Why it matters (ship)
Turns typing from aspiration into something CI can enforce. Unlocks TY-02/03/04/05/06/08.

## Locations
- `pyrightconfig.json:2-3` (entire 4-line file). Affects all 71 backend modules + 37 test modules.

## Proposed change
Set `typeCheckingMode: "basic"` (then consider `"standard"`), re-enable `reportMissingImports`. For genuinely stub-less third-party libs, prefer `reportMissingTypeStubs: false` (or per-module config) over blanket import suppression. Run `pyright` once to capture a baseline error count — that backlog is the point. Optionally stage strictness per-module via `include`/`strict` globs.

## Acceptance criteria
- [ ] `typeCheckingMode` is `basic` or stricter
- [ ] `pyright` runs and a baseline error count is recorded (commit it in the PR description)
- [ ] No blanket `reportMissingImports: false`
- [ ] (Stretch) a `pyright` step added to CI / pre-commit

## Blast radius & risk
Config only — zero runtime risk. Will surface a large initial error list (expected); subsequent TY tasks burn it down.

## Dependencies
**Do this first.** Blocks TY-02, TY-03, TY-04, TY-05, TY-06, TY-08.
