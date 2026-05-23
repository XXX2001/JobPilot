# JobPilot — Forward-Looking Improvements (2026-05-23)

Three parallel analyses run the day after the pre-ship hardening sprint landed on `main`. The deep audit at [`../2026-05-22-audit/`](../2026-05-22-audit/) and the standards backlog at [`../2026-05-22-standards/`](../2026-05-22-standards/) covered **what was broken**; this report covers **what could be better** — frontend UX, backend code quality, and product gaps a real job seeker would want closed.

> **Scope.** Forward-looking only. Bugs are tracked in the audit reports. Style/lint issues are tracked by Pyright + svelte-check. This report exists to answer "what's the next sprint?"
>
> **Source.** Three specialised agents (UX, backend code-quality, product gaps), each with read-only access, prior-audit context (to avoid duplication), and a `file:line` evidence mandate.

---

## Reports

| # | Report | Focus | Format | Key takeaway |
|---|---|---|---|---|
| 01 | [Frontend UX & user-delight](01-frontend-ux.md) | Daily-use friction, keyboard affordances, layout takeover, persistent state | 5 friction + 5 delight + 1 bet | The Job Queue hides the most valuable info one click deep; "Scan for Jobs" is a 5-min fire-and-forget that locks the whole UI; CV upload is a placebo (FE-12 never POSTs bytes). |
| 02 | [Backend code quality & refactoring](02-backend-quality.md) | God classes, copy-paste between strategies, dead modules, type tightening | 7 refactors + 3 dead-code + 2 type wins + 1 big-bet | `AutoApplyStrategy` and `AssistedApplyStrategy` are 80% copy-paste. `backend/utils/retry.py` and `backend/utils/source_health.py` are 100% dead. The apply flow wants to be an FSM. |
| 03 | [Product gaps & roadmap](03-product-gaps.md) | Missing user-facing features, integration leverage, workflow holes, contrarian "don't build" | 5 features + 3 integrations + 3 workflow + 1 contrarian | **Gmail/IMAP response detection is the single highest-leverage integration** — turns the Tracker from a manual-update spreadsheet into a live CRM. Don't build a mobile app. |

---

## Top 10 things to do next

Ordered by combination of **user value** × **effort to ship**.

| # | ID | Title | Source | Effort | Why now |
|---|---|---|---|---|---|
| 1 | **PG-PRE** | Wire CV upload to actually POST bytes (FE-12 stub) *(Shipped 2026-05-23)* | UX-3c | XS | First-run trust-break. Users complete onboarding, the dashboard reports `setup_complete=true`, and the first batch fails. |
| 2 | **BE-DEAD** | Delete `backend/utils/retry.py` + `backend/utils/source_health.py` *(Shipped 2026-05-23)* | BE-D1+D2 | XS | 150 LOC dead. Zero importers. Standalone PR. |
| 3 | **UX-HOTKEY** | Global `j/k/a/m/s/Enter/Esc` hotkeys + queue navigation *(Shipped 2026-05-23)* | UX-2a | S | No hotkeys exist today. Single `lib/utils/hotkeys.ts` + `svelte:window` in `+layout.svelte`. |
| 4 | **PG-1** | Follow-up reminders (7-day post-apply nudge) *(Shipped 2026-05-23)* | PG-1 | S–M | `applied_at` already stored but never read. Highest-impact small feature; jumps response rate. |
| 5 | **UX-LIMIT** | Daily-limit budget meter in sidebar *(Shipped 2026-05-23)* | UX-2d | S | Backend tracks it (`DailyLimitGuard`); FE never surfaces it. New `GET /api/applications/limit-status`. |
| 6 | **PG-3** | Application portfolio CSV/PDF export *(Shipped 2026-05-23)* | PG-3 | S | Required for unemployment-benefit reporting in EU. Pure read-side join. |
| 7 | **BE-R3** | `_upsert_singleton` helper for settings endpoints *(Shipped 2026-05-23)* | BE-R3 | S | Same field-by-field upsert × 2 today. Prevents the exact bug fixed in PR-10 (F-Q4 bonus). |
| 8 | **PG-INT-1** | Gmail integration Phase 1 (read-only sync) | PG-Int-1 | M | Already designed in [`../2026-05-22-audit/03-gmail-integration-design.md`](../2026-05-22-audit/03-gmail-integration-design.md). 3× user-perceived value of any other single feature. |
| 9 | **UX-BET** | "Today" dashboard replaces raw queue as `/` *(Shipped 2026-05-23)* | UX-3 | M | Re-frames product from "tool I trigger" to "ritual I check daily". Earns the open-tab. |
| 10 | **BE-R4** | Extract apply-flow state machine (`backend/applier/state.py`) *(Shipped 2026-05-23 — foundation; strategy collapse deferred to follow-up sprint)* | BE-1bet | L | Deletes ~400 LOC, eliminates the "did we release/close/rollback?" bug class. Foundation for everything else apply-side. |

---

## Cross-cutting themes

**1. "Wired but never reached" is still the dominant smell.** Both PG-1 (follow-up reminders — `applied_at` stored but never read) and PG-2 (cover-letter editor — `LetterPipeline` instantiated but no UI route consumes it) match the same pattern the prior audit flagged 6 times: features that were *scaffolded* but never *closed the loop*. Either finish them or delete them; both are honest. The current state ("the code suggests it works") is not.

**2. The apply-flow is the natural bottleneck of every future feature.** Cover-letter editing, pre-submit field editing, follow-up scheduling, application-export accuracy, FSM refactor — all of them touch `backend/applier/`. Investing in BE-R4 (FSM extract) compounds returns for every subsequent product feature. The 4-day cost pays back the first time a new state needs to be added.

**3. Daily-use ergonomics are the gap between "works" and "wins the morning tab."** The product technically functions but every interaction has 1-2 extra clicks, takeover modals, no keyboard, no undo, no in-context confirmation. UX-2 (delight) items are tiny individually but together they turn the app into something a job seeker *wants* to open every morning — which is the actual product question.

---

## What's NOT in scope here

- **Bugs** — see [`../2026-05-22-audit/INDEX.md`](../2026-05-22-audit/INDEX.md) and [`POST-SPRINT-VERIFICATION.md`](../2026-05-22-audit/POST-SPRINT-VERIFICATION.md).
- **Style / lint / type warnings** — Pyright (40/7) and svelte-check (0/1) baselines are tracked in the verification report.
- **Pydantic V2 `Field(env=…)` migration** — deferred Nice-to-have, doesn't block any of the above.
- **Stacked-branch push to origin / PR opening** — repo policy decision, not scope of this report.
