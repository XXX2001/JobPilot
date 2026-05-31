# M3 — Polish — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-31-finish-product-roadmap-design.md` §7
**Branch:** `feat/finish-product` · **Method:** subagent-driven dev, commit per task.

Baseline at M3 start: backend **557 passed, 5 skipped**; `pyright backend/` **41/8**; `svelte-check` **0 errors / 1 warning** (the lone warning is `settings/+page.svelte` cv-tailoring toggle missing aria-label — M3-T3 clears it). All M3 work is frontend; verify each task with `svelte-check` (target **0 errors**, and by end of M3 **0 warnings**) and `vitest` for any extracted logic. Component-render tests still need the jsdom harness (set up in M4-T3); until then, UI changes are verified by svelte-check + a documented manual check.

Svelte 5 runes only. WS via `$lib/stores/websocket`. Patterns per the M2 codebase map.

---

## M3-T1 — Split the Settings god-page (T7b)

`frontend/src/routes/settings/+page.svelte` is ~1,091 lines with 6 tabs. Decompose into one component per tab under `frontend/src/lib/components/settings/` (e.g. `ProfileTab.svelte`, `SearchTab.svelte`, `SourcesTab.svelte`, `GmailTab.svelte`, `AdvancedTab.svelte`, plus whatever the 6th tab is — read the file to enumerate the real tabs). The page becomes a thin shell: tab state + nav + `<svelte:component>`/conditional render of the active tab.

Rules:
- **Behavior-preserving.** No endpoint, payload, or visible-behavior change. Move markup + the handlers each tab uses into its component; pass shared state via props/callbacks or a small shared store if cleaner.
- Keep the new `base_letter_path` field (M2-T1) and the "Test template" button (M2-T5) in the Profile tab.
- Each extracted component must use runes and be self-contained (its own `$state`, its own `apiFetch` calls for its tab's data, or receive data via props).
- After the split, the shell file should be well under ~200 lines.

**Verify:** `svelte-check` 0 errors; manually confirm each tab still loads/saves (documented). No new warnings; ideally fix the cv-tailoring toggle aria-label here or in M3-T3.
**Commit:** `refactor(M3): split Settings into per-tab components (T7b)`.

---

## M3-T2 — WebSocket reconnect backoff (T7b)

`frontend/src/lib/stores/websocket.ts` `scheduleReconnect()` uses a FIXED 3000 ms delay, no backoff, no cap, no jitter. Replace with exponential backoff + jitter + a max cap, resetting on successful `onopen`.

- Extract the pure backoff math into a testable function, e.g. `frontend/src/lib/utils/backoff.ts`: `nextBackoffDelay(attempt, {baseMs=1000, maxMs=30000, jitter=true}) -> number`. Vitest-test it (monotonic growth up to cap; cap respected; attempt 0 ≈ base; jitter within bounds when seeded/stubbed).
- Wire it into `websocket.ts`: track an attempt counter, increment per reconnect, reset to 0 in `onopen`. Keep the existing `wsStatus` store semantics (the sidebar indicator). Optionally surface "reconnecting (attempt N)" in the indicator.
- Do NOT change the message dispatch or `send` behavior (note: `send` silently drops when disconnected — leave as-is for M3; out of scope).

**Verify:** vitest on `backoff.ts` passes; `svelte-check` 0 errors. Manually confirm reconnect still works (documented).
**Commit:** `feat(M3): exponential WebSocket reconnect backoff (T7b)`.

---

## M3-T3 — Accessibility + ergonomics pass

- Clear the one remaining `svelte-check` warning: add an explicit `aria-label` (or visible label) to the cv-tailoring toggle in the Profile tab (wherever it now lives post-M3-T1).
- Modals/dialogs (the queue review modal, any confirm modals, the login-required prompt): ensure focus trap on open, `Escape` closes, `role="dialog"` + `aria-modal="true"` + an accessible label, and focus returns to the trigger on close. There is an existing `frontend/src/lib/utils/focusTrap.ts` (from T7a) — reuse it; apply to modals that lack it.
- Ergonomics: ensure consistent loading states (skeleton/spinner) and that user-facing errors surface as a toast/inline message rather than being swallowed, on the pages touched in M2 (`/letters`, `/onboarding`, queue review, job detail) — light pass, don't over-engineer.

**Verify:** `svelte-check` **0 errors and 0 warnings**. Documented manual check of modal keyboard behavior.
**Commit:** `feat(M3): a11y + ergonomics pass (focus trap, labels, toasts)`.

---

## M3 verification (end of milestone)
`cd frontend && svelte-check` → **0 errors, 0 warnings**. Backend untouched (suite still 557/5). Vitest suite green. Documented manual smoke of Settings tabs, WS reconnect, and modal a11y.
