# Dev Consolidation — Status & Action Record (2026-05-30)

Records the state of the fix-sprint and the consolidation action taken on
2026-05-30: committing leftover work, merging the ready branches into `main`,
and force-pushing `main` to remote `dev`.

Supersedes the mid-flight snapshot at
`docs/reports/2026-05-23-codebase-deep-dive/SPRINT-STATUS.md` (2026-05-24).

## Starting point (before this action)

- `main` HEAD: `27eae3d` — unchanged since the 2026-05-24 halt.
- Backend tests: **407 passed, 5 skipped** (with working-tree leftovers applied).
- Frontend `svelte-check`: **0 errors, 1 a11y warning**.
- Working tree was **dirty**: 11 modified + 5 untracked files left over from a
  prior session.

### Divergence between local `main` and remote `dev`

```
git rev-list --left-right --count origin/dev...main  →  9   106
```

- `origin/dev` had **9 commits** not on `main` (e.g. `e398ff7 setup: improved
  fallback`, `aa4a9fe minor corrections in settings page and .gitignore`,
  `a92b951` merge).
- `main` had **106 commits** not on `dev`.

A push to `dev` therefore could **not** fast-forward.

## Decisions taken (user-authorized)

1. **Scope** — commit the leftovers **and** merge the 3 finished branches
   (`fix/T7a-frontend-types-a11y` → `fix/T6-scraping-resilience` →
   `fix/T9-ops-and-cleanup`) into `main`.
2. **Push method** — **force-push** `main` onto remote `dev`, discarding dev's
   9 unique commits.

### Safety net

Before the force-push, `origin/dev`'s tip was preserved locally so the 9
discarded commits remain recoverable:

- Branch: `backup/dev-pre-forcepush-20260530` → `e398ff7`
- Tag:    `backup-dev-pre-forcepush-20260530` → `e398ff7`

To recover dev's old state: `git push --force origin backup/dev-pre-forcepush-20260530:dev`.

## Leftovers committed

The dirty working tree resolved into two logical commits:

- **T4a-6** — `backend/applier/engine.py`: the re-entrancy guard race fix
  (serialise the in-flight membership-check + insert under a new
  `_registry_lock` so two concurrent `apply()` calls for the same
  `job_match_id` cannot both pass the check). This is one of the previously
  "unlanded" T4a items, found sitting uncommitted.
- **T8** — test infrastructure: per-worker `JOBPILOT_DATA_DIR` isolation in
  `conftest.py`, `pytest-xdist` + `hypothesis` dev deps, `tests/factories.py`,
  `tests/test_gmail_classifier_property.py`, frontend `vitest` config +
  `api.test.ts`, Gmail test refactors to drop the email-prefix workaround,
  and relocation of `tests/test_windows_playwright.py` →
  `scripts/diagnostics/windows_playwright.py`.

## Branches merged into `main`

| Order | Branch | Tip | Summary |
|---|---|---|---|
| 1 | `fix/T7a-frontend-types-a11y` | `7c6c2da` | FE types/a11y, dead-code removal (net −94 LoC) |
| 2 | `fix/T6-scraping-resilience` | `57bf6ea` | source-health, pagination, 37 tests |
| 3 | `fix/T9-ops-and-cleanup` | `f2264e3` | backup/migrate scripts, ops docs, dead-code purge |

## Still NOT done (remaining sprint work)

This consolidation does **not** make the codebase production-ready. Outstanding:

- **T4a items 4 & 5** — `ApplyContext.browser` assignment + browser-cleanup
  centralization in the FSM. (Item 6 is now landed; see above.)
- **T3 — silent failures** — killed in flight, 0 commits. Partial work may
  exist in worktree `agent-a64f24b01801aedbc`.
- **T2a — schema enforcement** — killed in flight, 0 commits. Stub files in
  worktree `agent-a5129daaf3667d24f`.
- **Follow-on tracks never started** — T1b (honest endpoints), T4b (strategy
  collapse), T7b (Settings split + WS backoff), T2b (schema tightening).

## Reference

- Deep-dive: `docs/reports/2026-05-23-codebase-deep-dive/INDEX.md` + 9 sub-reports
- Prior snapshot: `docs/reports/2026-05-23-codebase-deep-dive/SPRINT-STATUS.md`
