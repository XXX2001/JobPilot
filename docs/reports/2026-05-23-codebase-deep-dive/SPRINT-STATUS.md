# Fix Sprint — Status Snapshot (2026-05-24)

Mid-flight snapshot of the remediation sprint defined in `.claude/plans/jolly-squishing-babbage.md`. Agents stopped mid-wave at user request. No new work in flight.

## Where main is

```
27eae3d (HEAD -> main) fix(T4a-3): captcha wait honours cancel_event for early exit
2dfaed4                fix(T4a-2): unify _site_key / _domain_key to canonical site_profile_key
a66ca98                fix(T4a-1): GeminiClient — drop instance-state mutation in candidate loop
a09a6fa                docs(T1a): correct claimed-shipped status + add fix-sprint CHANGELOG
d9f7b23                fix(T1a-4): PATCH /applications/{id} status uses Literal alias
90b45a2                fix(T1a-3): lifespan re-raises singleton-init failures (fail-fast on startup)
6775723                fix(T1a-2): scan_overdue respects last_correspondence_at freshness anchor
2e39bfd                fix(T1a-1): omit Content-Type in apiFetch when body is FormData
f3ba534                docs: codebase deep-dive (2026-05-23)
```

**On main: T1a (5 commits) + T4a items 1–3 (3 commits) + deep-dive docs (1 commit) = 9 commits past `625a7f1` (the previous gm-sprint docs tip).**

Working tree has 11 modified + 5 untracked files left over from a previous session (mostly within T8's scope: `tests/conftest.py`, `pyproject.toml`, `tests/factories.py`, `frontend/vitest.config.ts`, `frontend/src/lib/api.test.ts`, `tests/test_gmail_classifier_property.py`, `scripts/diagnostics/`, plus a delete of `tests/test_windows_playwright.py`). These predate this session and were not produced by any T-track agent.

## Per-track status

| Track | Status | Branch | Tip | Notes |
|---|---|---|---|---|
| **T1a** — Re-open claimed-shipped | ✅ MERGED to main | `fix/T1a-reopen-shipped` | `a09a6fa` | 5 commits. 415/0 tests. svelte-check clean. Already fast-forwarded into main. |
| **T2a** — Schema enforcement | ⏸ Killed in flight | `fix/T2a-schema-enforcement` | `a09a6fa` (no commits) | Worktree at `.claude/worktrees/agent-a5129daaf3667d24f`. Agent created stub files (`tests/test_migrations.py`, `tests/test_db_integrity.py`) but no commits landed. Inspect worktree for partial state if salvaging. |
| **T3** — Silent failures | ⏸ Killed in flight | `fix/T3-silent-failures` | `a09a6fa` (no commits) | Worktree at `.claude/worktrees/agent-a64f24b01801aedbc`. No commits. Check worktree for any partial edits. |
| **T4a** — Applier FSM correctness | ⚠️ Partial — items 1-3 on **main**, items 4-6 lost | `fix/T4a-fsm-correctness` (empty, at `a09a6fa`) | — | Killed mid-flight. Items 1-3 (GeminiClient mutation, `_site_key`/`_domain_key` unification → `site_profile_key`, captcha cancel_event) committed directly to **main** (not to its branch) as `a66ca98 / 2dfaed4 / 27eae3d`. **Items 4 (`ApplyContext.browser` assignment), 5 (browser cleanup centralization in FSM), 6 (re-entrancy guard race) were not finished.** |
| **T6** — Scraping resilience | ✅ Branch ready (not merged) | `fix/T6-scraping-resilience` | `57bf6ea` | 2 commits. 37 new tests. Adds `backend/scraping/source_health.py`, country-domain unification, pagination on 5 of 6 adapters, `GET /api/queue/source-health`, `SourceHealthPills.svelte`. Different region of `scrapling_fetcher.py` than T3 — should merge cleanly. |
| **T7a** — Frontend types/a11y/dead code | ✅ Branch ready (not merged) | `fix/T7a-frontend-types-a11y` | `7c6c2da` | 1 commit. Net −94 LoC. svelte-check 0 errors. New `lib/types/api.ts`, `lib/utils/focusTrap.ts`. 14 duplicate type defs removed. 3 modals get full a11y. Dead `JobCard.svelte` + `TypewriterText.svelte` deleted. Will need small rebase against T1b later in `routes/queue/+page.svelte` (lines 342-397). |
| **T8** — Test infrastructure | ⏸ Killed in flight | `fix/T8-test-infrastructure` | `a09a6fa` (no commits) | Worktree at `.claude/worktrees/agent-a591649ce3747a1c4`. No commits on branch. Note: many T8-scope files appear as uncommitted in main's working tree (pre-session leftovers from a prior attempt). Consider salvaging from worktree or from main's working tree. |
| **T9** — Ops + dead-code purge | ✅ Branch ready (not merged) | `fix/T9-ops-and-cleanup` | `f2264e3` | 5 commits. +746 / −211 (~175 LoC of dead code deleted). New `scripts/backup_db.py`, `scripts/migrate_legacy_applied.py`. CREDENTIAL_KEY rotation docs. `start.py` honors `JOBPILOT_HOST`/`PORT`. Dockerfile Tectonic version aligned. 9 new tests. |

## Worktrees still on disk

```
~/Web-automation/.claude/worktrees/agent-a0f57b6f4947cea90  fix/T9-ops-and-cleanup
~/Web-automation/.claude/worktrees/agent-a5129daaf3667d24f  fix/T2a-schema-enforcement
~/Web-automation/.claude/worktrees/agent-a591649ce3747a1c4  fix/T8-test-infrastructure
~/Web-automation/.claude/worktrees/agent-a64f24b01801aedbc  fix/T3-silent-failures
~/Web-automation/.claude/worktrees/agent-a9ab38006b8684e92  fix/T7a-frontend-types-a11y
~/Web-automation/.claude/worktrees/agent-ace57d6f3e24ea25a  fix/T4a-fsm-correctness
~/Web-automation/.claude/worktrees/agent-ad40efe1e0a018842  fix/T1a-reopen-shipped
~/Web-automation/.claude/worktrees/agent-af760cf373decaba6  fix/T6-scraping-resilience
```

All locked. Some hold uncommitted partial edits (T2a, T3, T8). Inspect before pruning.

## Suggested next moves (when ready to continue)

1. **Sanity-check main.** `uv run pytest -q` against main — main has T1a + T4a-1/2/3 but no integration with the other branches yet. Expect green.
2. **Decide on stale working-tree changes.** The 11 modified + 5 untracked files in main's working tree predate this session. Either:
   - Stash them (`git stash push -u -m "pre-session leftovers"`) and resume later.
   - Discard them (`git checkout -- . && git clean -fd`) and rely on T8's worktree to redo the work cleanly.
   - Inspect each diff and keep what's wanted.
3. **Merge order** (low-risk → high-risk):
   - `fix/T7a-frontend-types-a11y` (frontend-only, no overlap)
   - `fix/T6-scraping-resilience` (scraping isolated; might touch scrapling_fetcher.py region that T3 also wants — defer T3 until after T6)
   - `fix/T9-ops-and-cleanup` (scripts + docs + dead-code; minimal overlap)
   - Then revisit T2a, T3, T8 (require salvage or re-dispatch).
4. **T4a leftover items.** Items 4, 5, 6 from the plan still need landing: `ApplyContext.browser` assignment, browser cleanup centralization in FSM, re-entrancy guard race. Dispatch a focused agent or do inline.
5. **T2a, T3, T8 status.** Inspect each killed worktree before re-dispatching — they may have salvageable work-in-progress.
6. **Follow-on tracks not yet started.** T1b (honest endpoints), T4b (strategy collapse), T7b (Settings split + WS backoff), T2b (schema tightening post-T4).

## Reference docs

- Spec: `.claude/plans/jolly-squishing-babbage.md`
- Deep-dive: `docs/reports/2026-05-23-codebase-deep-dive/INDEX.md` + 9 sub-reports
- Improvements log: `docs/reports/2026-05-23-improvements/INDEX.md` — items 1, 4, 10 now correctly marked "Foundation only — re-opened 2026-05-24"
- CHANGELOG: `## fix-sprint 2026-05-24` section open

---
*Snapshot taken 2026-05-24, mid-wave, at user request to halt and mark state.*
