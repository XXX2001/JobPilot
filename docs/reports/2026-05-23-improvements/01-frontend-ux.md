# 01 — Frontend UX & User-Delight

**Scope.** What would make daily use *better* for a real job seeker — not bugs, not refactors. Pre-existing FE bugs and audit findings are excluded (see [`../2026-05-22-audit/04-frontend-audit.md`](../2026-05-22-audit/04-frontend-audit.md)).

---

## 1. Top 5 user-friction points

**1a. The Job Queue hides the most valuable info behind a click.** On [`frontend/src/routes/+page.svelte`](../../../frontend/src/routes/+page.svelte), the queue row only shows score, title, and `company · location`. To learn *why* a job scored 80% or *what was tailored in the CV*, the user must enter the per-card review flow (`CVReviewPanel`) one match at a time, or navigate to `/jobs/[id]`. Salary, posting age, easy-apply badge, and a CV-diff preview all exist on the detail page but are absent from the queue row — for a list of 10–15 daily matches, this means 10–15 round-trips just to triage. Worse, the row's three buttons (Auto / Manual / Skip) are *modes for later batch apply*, not *immediate actions* — but nothing in the UI communicates this gap, so users likely expect "click Auto = apply now."

**1b. "Scan for Jobs" is a fire-and-forget that locks the UI for ~5 minutes with no cancel.** [`routes/+page.svelte:109`](../../../frontend/src/routes/+page.svelte#L109) sets `refreshing = true` and uses a 5-minute fallback `setTimeout` (line 113). The `BatchPipelineTracker` (`lib/components/BatchPipelineTracker.svelte`) takes over the full page — the user can't open `/tracker` or `/cv` to do *anything* else, can't see partial results as they land, and there is no abort button. The backend `POST /api/queue/refresh` even has a 409 code for "already running" (line 120) but the FE silently swallows it. For a flow that legitimately costs 30–120 s of Gemini calls, no cancel + no parallel work = a feels-broken experience.

**1c. CV upload is a placebo.** [`routes/cv/+page.svelte:56-81`](../../../frontend/src/routes/cv/+page.svelte#L56-L81) (with a self-aware `TODO FE-12` comment) tells the user "CV template '\<file>' registered" — but the bytes are never POSTed. Same dead path in `SetupWizard.svelte:30-50`. A new user completes the wizard and the dashboard reports `setup_complete=true`, yet the first batch run will fail (or use a stale CV) with no breadcrumb back to the cause. **This is the single biggest first-run trust-break.**

**1d. The CVReviewPanel review flow doesn't remember "I just looked at this."** [`lib/components/CVReviewPanel.svelte`](../../../frontend/src/lib/components/CVReviewPanel.svelte) walks through approved matches one at a time (lines 100–109). There is no way to: jump to a specific match (`cursor` is incremented sequentially only), bulk-approve all with score ≥ 80, or see the *next* job's preview while looking at the current one. For a batch of 10 jobs the user has to click "Approve →" 10 times after individually reading 10 split-pane diffs — no undo on a misclicked Skip, no progress save if the tab closes.

**1e. The Settings page has 6 tabs, each costs a network round-trip, and saves give zero confirmation in-context.** [`routes/settings/+page.svelte`](../../../frontend/src/routes/settings/+page.svelte) has 7 loaders triggered per tab activation, but no per-tab "dirty" indicator — a half-filled Search tab loses everything if the user clicks Profile mid-edit. Saving currently flashes a top-level banner (`successMsg`) that doesn't pin to the field that changed; with `daily_limit`, `min_match_score`, `cv_modification_sensitivity` all on one tab, the user has no idea which one took effect.

---

## 2. Top 5 user-delight opportunities

**2a. Global keyboard shortcuts.** None exist today (grep confirmed: only `Enter` handlers in input fields). Add `j`/`k` to move between queue cards, `a`/`m`/`s` to toggle Auto/Manual/Skip on the focused card, `Enter` to enter detail, `/` to focus search, `Esc` to close modals (queue confirm modal, login modal, easter-egg toasts). Same hotkeys in `CVReviewPanel`: `1`=skip, `2`=base CV, `3`=approve, `←`/`→` navigate. Concrete file: add a `lib/utils/hotkeys.ts` and a single global `<svelte:window onkeydown={...}>` in `routes/+layout.svelte`.

**2b. "Apply Now" inline on the queue row.** Collapse the current 2-phase (Queue → Review → Confirm → Run) into a 1-click path for high-confidence matches (score ≥ 85 with `apply_method=easy_apply`). Today the user has to: enter review panel → load diff → approve → confirm → run. For ⚡ Auto matches the panel adds friction without value (no decisions to make). Sketch: in [`routes/+page.svelte:295-308`](../../../frontend/src/routes/+page.svelte#L295-L308) add a green "Apply now" button next to Auto/Manual/Skip when conditions are met, POST directly to `/api/applications/{id}/apply`.

**2c. Surface batch progress in the sidebar, not as a full-page takeover.** Move the `BatchPipelineTracker` from [`routes/+page.svelte:236`](../../../frontend/src/routes/+page.svelte#L236) into a compact widget in `routes/+layout.svelte` (under the WS status block, lines 73-84). Free the main pane so the user can browse Tracker / CV history / Settings while a scan runs. The full tracker is still reachable by clicking the widget.

**2d. Daily-limit budget meter.** Backend already tracks `DailyLimitGuard` (cap 10); FE never surfaces it. Add a small "3 / 10 applications today" pill to the layout sidebar, color-coded amber at 8+ and red at 10. File: `routes/+layout.svelte` near the WS status, fed by a new `GET /api/applications/limit-status` (or reuse `/api/analytics/summary`).

**2e. Optimistic + persistent skip in the queue.** Today `setMode(matchId, 'skip')` ([`routes/+page.svelte:130-158`](../../../frontend/src/routes/+page.svelte#L130-L158)) drops the card from the array with no undo, and Auto/Manual is *not persisted* (lives only in the in-memory `modes` Map). On reload the user loses their per-row choices. Persist mode per match (small `PATCH /api/queue/{id}/mode`) and add a Snackbar "Skipped {title} — Undo" with a 5 s undo window — same pattern as Gmail's archive.

---

## 3. One bigger bet — a "Today" dashboard as the new home

Replace the current `/` (raw queue list) with a single-screen **Today** view that answers the three questions a job seeker actually has when they open the app:

1. **What's new since yesterday?** Top-of-page: "12 new matches scanned at 08:14 — 3 high-confidence (≥80%)." A grouped/sectioned feed (High-confidence → Worth reviewing → Skipped automatically) instead of the current flat list.
2. **What needs my attention right now?** A blocked-actions strip: "1 login required on LinkedIn · 1 application awaiting your Confirm Submit click · 2 manual-apply jobs you opened but never marked done." Today these signals are scattered across the `LoginRequiredModal`, the queue-page `confirmModal`, and the Tracker — there's no single inbox for "things I need to unblock."
3. **How am I doing this week?** A compressed version of the `/analytics` cards inline (`Applications this week`, `Response rate`, `Daily limit usage`) — turning analytics from a destination into ambient context.

The current Job Queue becomes a sub-view (`/queue`) for power users; the Today view becomes the default landing. This re-frames JobPilot from "a tool I configure and trigger" into "a daily ritual that tells me where I stand," which is what a job-hunting product needs to be to earn an open tab every morning.

**Files affected:** new `routes/+page.svelte` (rewrite), move existing into `routes/queue/+page.svelte`, surface aggregate endpoints already returned by `/api/analytics/summary` + a new `/api/today` that bundles new-matches + blocked-actions + week-totals in one call.
