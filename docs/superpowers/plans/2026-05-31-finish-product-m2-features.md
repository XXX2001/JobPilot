# M2 — Complete Product Features — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-31-finish-product-roadmap-design.md` §6
**Branch:** `feat/finish-product` · **Method:** subagent-driven dev, TDD, commit per task.

Baseline at M2 start: backend **535 passed, 5 skipped**; `pyright backend/` **41 errors / 8 warnings**; `svelte-check` **0 errors / 1 warning**. Keep at/above. Backend additions are TDD'd with `pytest`. Frontend wiring is verified with `npm run check` (svelte-check must stay 0 errors); any non-trivial pure logic is extracted into a function with a `vitest` test (node env, mirror `frontend/src/lib/api.test.ts`). Svelte component-render tests need jsdom + @testing-library (not installed) — that harness is set up in M4-T3; until then UI is verified by svelte-check + a documented manual check.

Universal frontend patterns (from the codebase map): API via `apiFetch<T>` in `frontend/src/lib/api.ts` (never set Content-Type for FormData; PDFs loaded by URL in `<a>`/`<iframe>`, not fetched); Svelte 5 runes only (`$state/$derived/$effect/$props`); WS via `{ messages, send, onWsConnect, wsStatus }` from `$lib/stores/websocket`; nav links in `frontend/src/routes/+layout.svelte` (`navLinks`).

---

## M2-T1 — Cover-letter view at `/letters`

Mirror `frontend/src/routes/cv/+page.svelte`.
- New `frontend/src/routes/letters/+page.svelte`: `apiFetch<Document[]>('/api/documents')` filtered to `doc_type === 'letter'`; list matches; inline preview via `<iframe src="/api/documents/{matchId}/letter/pdf?t={ts}">` (cache-bust on regenerate); a **Regenerate** button → `apiFetch<LetterRegenerateResponse>('/api/documents/{matchId}/letter/regenerate', {method:'POST'})` then refresh the iframe `src` with a new `?t=`.
- Add `base_letter_path` to the `Profile` TS interface where the settings page reads profile, and show the configured base letter template (read-only), mirroring how `/cv` shows `base_cv_path`.
- Add a `/letters` entry to `navLinks` in `+layout.svelte`.
- No letter-template upload endpoint (out of scope; users drop a `*letter*.tex` in `templates/`, which `_resolve_letter_path` already auto-detects).

**Tests:** `svelte-check` 0 errors. (Backend letter endpoints already covered by `tests/test_documents_letter_regenerate.py`.)
**Commit:** `feat(M2): cover-letter view + regenerate at /letters`.

---

## M2-T2 — Pre-submit field editing

Backend (TDD with pytest):
- `backend/api/ws_models.py`: add inbound `PatchFields` to the `ClientMessage` union: `{type:"patch_fields", job_id:int, fields: dict[str,str]}` (fields keyed by CSS selector, same keys as `ApplyReview.filled_fields`).
- `backend/applier/engine.py`: add `self._pending_patches: dict[int, dict[str,str]] = {}`; `signal_patch_fields(job_id, fields)` stores it; expose `get_pending_patches(job_id)`; purge alongside the existing confirm/cancel/finally cleanup.
- `backend/main.py`: register a `patch_fields` WS handler next to `confirm_submit`/`cancel_apply` → `engine.signal_patch_fields(job_id, fields)`.
- `backend/applier/form_filler.py`: after the confirm event fires and BEFORE `page.click(submit)`, apply any patches for this job (re-`page.fill(sel, val)` for each). The filler needs the patches — thread them via a callback injected from the engine (mirror the `_on_review` pattern: `on_get_patches(job_id) -> dict`), or read them through a passed-in accessor. Keep the existing fill→broadcast→wait→submit order otherwise unchanged.

Frontend:
- `frontend/src/lib/types/ws.ts`: add `PatchFieldsMsg` to `ClientMessage`.
- `frontend/src/routes/queue/+page.svelte`: make the read-only review `<dd>{v}</dd>` (~L377) editable `<input bind:value={confirmModal.fields[k]}>`; in `confirmApply()` (~L162) `send({type:'patch_fields', job_id, fields: confirmModal.fields})` BEFORE `send({type:'confirm_submit', job_id})`.

**Tests:** pytest for the WS model accepts `patch_fields`; `engine.signal_patch_fields` stores + `get_pending_patches` returns + purge on terminal; a form_filler-level test (mock page) asserting patched selectors are re-filled before submit. `svelte-check` 0 errors.
**Commit:** `feat(M2): pre-submit field editing via patch_fields`.

---

## M2-T3 — "Why this score" panel

Mostly frontend; bridge `match_id → job_id`.
- In `frontend/src/routes/jobs/[id]/+page.svelte`: after loading the queue match (`matchData`), use `matchData.job_id` to call `apiFetch<JobScoreOut>('/api/jobs/{job_id}/score')` for `keyword_hits`; fetch `apiFetch<SearchSettings>('/api/settings/search')` for `salary_min` + keywords. Compute, in a small pure function (vitest-tested), a breakdown: matched vs missing keywords (from `keyword_hits` — INSPECT `backend/matching/matcher.py` to confirm the dict shape first) and a salary comparison (`matchData.job.salary_min/max` vs `salary_min`).
- Render a "Why this score" tab (add to the existing `description|diff|emails` tab set) or a card under the header, reusing `ScoreIndicator` for the scalar.
- Only add backend fields if the client genuinely can't compute the breakdown from existing endpoints; prefer client-side composition. If `keyword_hits` is absent from the score endpoint output, that's the one acceptable small backend touch.

**Tests:** vitest for the score-breakdown pure function (matched/missing/salary cases). `svelte-check` 0 errors.
**Commit:** `feat(M2): why-this-score breakdown on job detail`.

---

## M2-T4 — Onboarding wizard at `/onboarding`

Promote the existing `frontend/src/lib/components/SetupWizard.svelte` steps into a full-page stepper.
- New `frontend/src/routes/onboarding/+page.svelte` with steps: (1) API keys (instructional, shows `.env` snippet — keys are env-only), (2) CV upload → `POST /api/settings/profile/cv-upload` (FormData), (3) keywords → `PUT /api/settings/search` `{keywords:{include:[...]}}`, (4) enable a source → `PUT /api/settings/sites/{name}` `{enabled:true}` (and/or custom site), (5) run first batch → `POST /api/queue/refresh` with WS progress.
- Gate: in `frontend/src/routes/+page.svelte` `load()`, fetch `GET /api/settings/status`; if `!setup_complete`, `goto('/onboarding')`. Provide a visible "skip"/"do later" affordance so it's not a hard lock.
- Reuse `SetupWizard`'s existing handlers; convert its legacy `createEventDispatcher` usage to runes/callbacks if it's moved (avoid leaving dead dispatch).

**Tests:** `svelte-check` 0 errors; any extracted step-completion logic gets a vitest test. (`GET /api/settings/status` already covered.)
**Commit:** `feat(M2): /onboarding stepper + first-run redirect`.

---

## M2-T5 — LaTeX template compile-test

The existing `POST /api/documents/validate-template` only checks markers (no compile). Add a real compile-test:
- Backend (TDD): `POST /api/settings/profile/compile-test` (or `/api/documents/compile-test`) that resolves the stored base CV template (`profile.base_cv_path` via the same resolution as elsewhere), runs `LaTeXCompiler.compile()` on it with empty/placeholder substitutions, and returns `{ok: bool, error_log: str|null}` (catch `LaTeXCompileTimeout` and compile errors → `ok:false` + log; success → `ok:true`). Do NOT stream a PDF (a marker-only template won't render meaningfully); reporting compile success/errors is the user value (catch Tectonic failures before a batch). Mock Tectonic in the test the way existing compiler tests do.
- Frontend: a "Test template" button in the Settings → Profile tab (`settings/+page.svelte`, near `saveProfile`) that calls it and shows a success badge or the error log. Also surface the existing marker-validation warnings.

**Tests:** pytest for the compile-test endpoint (success + failure/timeout paths, Tectonic mocked). `svelte-check` 0 errors.
**Commit:** `feat(M2): template compile-test endpoint + Settings button`.

---

## M2-T6 — Batch dry-run

Backend (TDD):
- `backend/scheduler/batch_runner.py`: add `dry_run: bool = False` to `run_batch`/`_run_batch_inner`. When true, run scrape + match/rank (Steps 1–2) only, then RETURN a preview list built from `ranked` (e.g. `[{title, company, score, location}]`) and SKIP `_store_matches`, fit assessment, and CV generation (Steps 3–4) and the DB writes. Default path returns `None` as today (keep signature back-compatible).
- `backend/api/queue.py`: add `dry_run: bool = Query(False)` to `POST /api/queue/refresh`. When `dry_run`, run INLINE (`preview = await runner.run_batch(dry_run=True)`) and return `{status:"preview", matches:[...], total:int}`; keep the `running` guard; the normal path stays fire-and-forget.
- Frontend: a "Preview today's matches" button near `refreshQueue` in `queue/+page.svelte` that calls `POST /api/queue/refresh?dry_run=true` and shows the preview list (no rows committed).

**Tests:** pytest: dry-run returns a preview and performs NO DB writes (assert match/doc tables unchanged), normal run unchanged. `svelte-check` 0 errors.
**Commit:** `feat(M2): batch dry-run preview (no DB writes)`.

---

## M2 verification (end of milestone)
`uv run pytest -q` (green, ≥ baseline), `uv run pyright backend/` (≤ 41/8), and `cd frontend && npm run check` (0 errors). Manual smoke (documented): `/letters` preview+regenerate, editable review modal sends `patch_fields`, score panel renders, `/onboarding` redirect for a fresh profile, template compile-test button, dry-run preview.
