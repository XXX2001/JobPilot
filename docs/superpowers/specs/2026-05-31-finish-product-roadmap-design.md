# Finish Product — Roadmap Design Spec

**Date:** 2026-05-31
**Status:** Design — approved by user (full autonomy granted to execute end-to-end)
**Predecessors:**
- `docs/reports/2026-05-30-dev-consolidation.md` ("Still NOT done")
- `docs/reports/2026-05-23-improvements/{INDEX,02-backend-quality,03-product-gaps}.md`
- `docs/superpowers/specs/2026-05-30-finish-inflight-sprint-design.md` (the just-completed fix sprint)

---

## 1. Goal

Take JobPilot from "works for its author" to a **shareable, open-source product**:
single-user per install, functionally complete, reliable, documented, and installable
in one command. Delivered in **four sequential milestones** (foundations-first), each
with its own implementation plan executed via subagent-driven development.

This is a **roadmap spec**: it frames the whole remaining scope and decomposes it into
milestones. M1 is specified to task level here; M2–M4 are specified to component level
and each gets a dedicated implementation plan written immediately before its execution
(so plans stay grounded in the evolving code).

## 2. Definition of "finished" (acceptance bar)

1. **No phantom surface.** Every exposed endpoint/route/wired component does something
   real (no `LetterPipeline`/caches wired but never reached).
2. **The 6 remaining product features shipped** and reachable from the UI.
3. **Pragmatic refactors done** (T1b honest endpoints, T7b Settings split + WS backoff,
   `_upsert_singleton`, `ApplicationResult` Literal, strategy de-duplication via the FSM) —
   no risky rewrite without user value.
4. **One-command install** (Docker Compose + `uv` quickstart), with `README` quickstart,
   `LICENSE` (MIT), `CONTRIBUTING.md`, and end-to-end user docs (onboarding → first batch
   → tracking → Gmail).
5. **Green CI** on GitHub Actions: `pytest` + `pyright` (at baseline or better) +
   `svelte-check` + lint, on every push/PR.
6. **No regressions:** backend suite green (≥ 484 passing), `svelte-check` 0 errors,
   `pyright backend/` at or below the 43-error / 8-warning baseline.

## 3. Non-goals (explicitly deferred)

- **Calendar integration** (Google/CalDAV) and web-push notifications.
- **Multi-user / auth / billing / multi-tenancy.**
- **Mobile app** (assumed contrarian "don't build").
- The riskiest apply-flow rewrites: full `ApplicationEngine` decomposition (R4) and
  `BatchRunner` → `Phase` objects (R2), beyond the pragmatic strategy de-duplication.
- New job-board integrations / LinkedIn profile import.

## 4. Working method

- **Branch:** all work lands on `feat/finish-product`, never directly on `main`. Merged
  locally into `main` (no fast-forward) at the very end.
- **Execution:** subagent-driven development — one implementer subagent per task, each
  followed by a spec-compliance review, then a code-quality review; fix loops until both
  pass. A final whole-implementation review per milestone.
- **TDD throughout.** Every behavioral change is pinned by a test written before the code.
- **Plans are written per milestone, just-in-time**, to `docs/superpowers/plans/2026-05-31-finish-product-m{N}-*.md`.

---

## 5. Milestone M1 — Harden the foundation

Make the base clean and honest so M2 features sit on it without rework.

### M1-T1 — Wire `record_pending_review` (close the in-flight loose end)
`apply_review` is broadcast *during* the confirm-wait (`auto_apply.py`, `form_filler.py`),
but the engine's pending-review cache is never populated → `GET /api/applications/{id}/review-state`
always returns empty. Populate the cache **at broadcast time** (not at `_dispatch` return —
too late). Mechanism: inject a `record_pending_review(job_id, payload)` callback from the
engine into the strategies (mirrors the established `_active_browser` pattern), invoked just
before the confirm-wait; cache purge on confirm/cancel/terminals already exists. Outcome: a
client that lost its WS connection can re-fetch the review payload.
**Tests:** review-state endpoint returns the payload after a simulated broadcast; cleared on confirm/cancel.

### M1-T2 — T1b honest endpoints audit
Audit every exposed route / wired collaborator for "wired but never reached":
- `LetterPipeline` → **keep** (consumed by the M2 letters editor). Add a letter-only
  `POST /api/documents/{match_id}/letter/regenerate` (today `regenerate` does CV+letter).
- Re-check `Embedder` / `FitEngine` wiring (previously flagged wired-but-unused) → wire or remove.
- Any other dead-but-exposed surface → wire or delete, with a one-line justification.
**Tests:** new letter-regenerate endpoint; assertions that retained surfaces are reachable.

### M1-T3 — `ApplicationResult` typed vocabulary
Type `ApplicationResult.status`/`.method` as `Literal[...]` (values already exist as
constants in `backend/applier/__init__.py`). Catches typos and engine↔strategy drift.
**Tests:** a bad status value fails type-check / validation; existing strategies conform.

### M1-T4 — `_upsert_singleton` helper for settings
Replace the field-by-field upserts in `PUT /api/settings/{profile,search}` with a
`_upsert_singleton(model_cls, id_=1, body, defaults={...})` helper (`model_dump(exclude_unset=True)`).
Removes ~120 LOC and the "new field forgotten in upsert" bug class. Prepares M3's Settings split.
**Tests:** partial update touches only provided fields; fresh-row creation applies defaults.

### M1-T5 — Pragmatic strategy de-duplication
Factor the **literally duplicated** blocks between `auto_apply.py` and `assisted_apply.py`
(`_site_key`, `_is_multi_step_site`, `Browser(**kwargs)` boot + saved-session load, the
LinkedIn-vs-generic prompt template) into shared helpers. **No flow rewrite** — only
de-duplication, capitalizing on the FSM landed in the previous sprint.
**Tests:** parity tests asserting both strategies use the shared helpers; existing apply tests stay green.

### M1 out of scope
Full `ApplicationEngine` decomposition (R4) and `BatchRunner` → `Phase` (R2).

---

## 6. Milestone M2 — Complete the product features

Most backends already exist; M2 is largely frontend wiring + small endpoint additions.

### M2-T1 — Cover-letter editor at `/letters`
Mirror `/cv`: list job matches, stream `GET /api/documents/{match_id}/letter/pdf`, preview,
and regenerate via the M1 letter-only endpoint. Edit through the existing JOBPILOT marker
injector where the CV editor already does.

### M2-T2 — Pre-submit field edit
Extend the `apply_review` WS payload to include `fields[]` (selector + current value); add an
inbound `patch_fields` message to the `WSMessage` union in `ws_models.py`; `form_filler`
applies patches before submit. Wire editable fields into the review modal on the queue page.

### M2-T3 — "Why this score" panel
`GET /api/jobs/{id}/score` already returns `keyword_hits`. Expand `GET /api/jobs/{id}` (or the
score endpoint) to include `keyword_hits` + a diff vs `UserProfile` + a salary comparison vs
`search_settings.salary_min`. Render a "Why this score" panel in `jobs/[id]/+page.svelte`.

### M2-T4 — Onboarding wizard at `/onboarding`
A stepper driven by the existing `getProfileStatus` helper: upload CV → set keywords →
connect a source → run first batch. Redirect new users here from `/` when setup is incomplete.

### M2-T5 — LaTeX template compile-test
Backend `POST /api/documents/validate-template` already exists. Wire a "Test template" button
in Settings → Profile that compiles the bare template and shows the rendered PDF or the errors.

### M2-T6 — Batch dry-run
`POST /api/queue/refresh?dry_run=true` runs scraping + matching but skips CV generation and DB
writes; return the would-match preview. Surface a "Preview today's matches" action in the UI.

---

## 7. Milestone M3 — Polish

### M3-T1 — T7b: split the Settings god-page
`settings/+page.svelte` (~1,091 lines, 6 tabs) → one component per tab with a thin shell.
No behavior change; pure decomposition for maintainability.

### M3-T2 — WebSocket reconnect backoff
Add exponential-backoff reconnect to the frontend WS client (currently no backoff), with a
visible connection indicator.

### M3-T3 — Accessibility + ergonomics pass
Finish a11y on remaining modals/components (focus trap, ARIA, escape handling), consistent
loading states and error toasts, and keyboard-affordance consistency.

---

## 8. Milestone M4 — Packaging, docs & CI

### M4-T1 — One-command install
Docker Compose for the full stack (backend + built frontend + Tectonic) and a documented
`uv`-based local quickstart. Verify `start.py` / `Dockerfile` / env handling.

### M4-T2 — Docs & licensing
Rewrite `README` quickstart; add an end-to-end user guide (onboarding → batch → tracker →
Gmail); add `LICENSE` (MIT) and `CONTRIBUTING.md`; cross-link the existing architecture and
credentials/privacy docs.

### M4-T3 — CI pipeline
GitHub Actions workflow: backend `pytest`, `pyright backend/` (fail above baseline),
`svelte-check`, and frontend lint — on push and PR. Cache `uv` and npm.

### M4-T4 — Pydantic V2 `Field(env=)` migration (polish)
Migrate the 12 `Field(..., env=...)` usages in `config.py` to the V2-idiomatic form to clear
deprecation warnings.

---

## 9. Cross-cutting

- **Testing strategy:** maintain a green suite at every task; add tests per feature/refactor;
  CI (M4) enforces the bar permanently. Frontend features get `vitest`/component tests where
  the existing harness supports it; otherwise a documented manual check.
- **Sequencing & dependencies:** M1 before M2 (features depend on honest endpoints + the
  letter-regenerate endpoint + typed results). M3 polish after features exist. M4 last so CI
  and docs reflect the final code. Within a milestone, tasks are mostly independent and run
  one implementer at a time (no parallel implementers — conflict risk).
- **Risk controls:** each task is TDD'd and double-reviewed; the previous sprint's behavioral
  safety nets (`test_apply_*`, `test_api_routes`, `test_migrations`, `test_db_integrity`) must
  stay green throughout.

## 10. Success criteria recap

Done when §2's six bullets all hold, the branch merges cleanly into `main` locally, and the
suite + CI are green. Calendar, multi-user, and mobile remain deferred by design.
