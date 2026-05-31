# M1 — Harden the Foundation — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-31-finish-product-roadmap-design.md` §5
**Branch:** `feat/finish-product`
**Method:** subagent-driven development, TDD per task, commit per task.

Baseline before M1: backend suite **484 passed, 5 skipped**; `pyright backend/` **43 errors / 8 warnings**; `svelte-check` 0 errors. Keep at/above baseline throughout.

---

## M1-T1 — Wire `record_pending_review` at broadcast time

**Problem.** `ApplicationEngine.record_pending_review(job_id, filled_fields, screenshot_b64)`
and `GET /api/applications/{job_id}/review-state` exist, but nothing ever calls
`record_pending_review`, so the endpoint always returns 404/empty. The `apply_review` WS
message is broadcast inside the strategies (`auto_apply.py` ~L368, `form_filler.py` ~L199)
during the confirm-wait — that is exactly where the cache must be populated.

**Design.** Inject the engine's `record_pending_review` as a callback into the strategies at
`ApplicationEngine.__init__` (mirrors the `_active_browser` exposure pattern):
- `AutoApplyStrategy(..., on_review=self.record_pending_review)`
- `AssistedApplyStrategy(..., on_review=self.record_pending_review)`
- `PlaywrightFormFiller` likewise receives the callback (it owns the Tier-1 broadcast).
Each strategy stores `self._on_review` (default `None`, callable optional) and, immediately
before/after broadcasting `apply_review`, calls
`self._on_review(job_id, filled_fields=<mapping>, screenshot_b64=<b64 or None>)` when set.
Remove the stale `NOTE` comment above `record_pending_review` in `engine.py`.

**TDD.**
1. Test (`tests/test_apply_engine.py` or a new `tests/test_review_state.py`):
   construct an engine, grab the injected callback, simulate a strategy broadcast by invoking
   the callback, then assert `engine.get_pending_review(job_id)` returns the payload and that
   `GET /api/applications/{job_id}/review-state` (via the app client, see `tests/test_apply_http.py`
   patterns) returns it. Assert it is cleared after `signal_confirm` / `signal_cancel`.
2. Watch fail → wire callback → watch pass.

**Files.** `backend/applier/engine.py`, `backend/applier/auto_apply.py`,
`backend/applier/assisted_apply.py`, `backend/applier/form_filler.py`, tests.
**Commit.** `feat(M1): populate pending-review cache at apply_review broadcast`.

---

## M1-T2 — `ApplicationResult` typed vocabulary

**Goal.** Replace the bare `str` types in `backend/applier/manual_apply.py`:
```python
class ApplicationResult(BaseModel):
    status: Literal["applied", "assisted", "manual", "cancelled", "failed"]
    method: Literal["auto", "assisted", "manual"]
    message: str = ""
```
Use the `RESULT_*` constants where results are constructed if it reads cleanly; the Literal is
the contract. Fix any call site that sets a value outside the Literal (there should be none;
this surfaces drift). Keep `manual_apply.ApplicationResult` the single definition (it is
imported widely).

**TDD.**
1. Test: `ApplicationResult(status="cancled", method="auto")` raises `ValidationError`; valid
   combos construct fine; `method="auto"` allowed, `method="manual"` allowed.
2. Watch fail (today it accepts anything) → tighten types → watch pass + full suite green.

**Files.** `backend/applier/manual_apply.py`, tests.
**Commit.** `refactor(M1): type ApplicationResult.status/method as Literal`.

---

## M1-T3 — `_upsert_singleton` helper for settings endpoints

**Goal.** In `backend/api/settings.py`, the `PUT /api/settings/profile` and
`PUT /api/settings/search` handlers repeat field-by-field `if body.X is not None: row.X = body.X`
(~11 and ~17 fields) plus a parallel fresh-row branch. Extract:
```python
async def _upsert_singleton(db, model_cls, *, id_=1, body, defaults):
    row = await db.get(model_cls, id_)
    data = body.model_dump(exclude_unset=True)
    if row is None:
        row = model_cls(id=id_, **{**defaults, **data})
        db.add(row)
    else:
        for k, v in data.items():
            setattr(row, k, v)
    await db.commit(); await db.refresh(row)
    return row
```
Both endpoints become a few lines. Preserve exact current behavior (only `exclude_unset`
fields update; fresh-row defaults match today's).

**TDD.**
1. Tests in `tests/test_api_routes.py` / a settings test: partial PUT updates only provided
   fields and leaves others intact; first-ever PUT creates the row with defaults; full suite
   for settings stays green.
2. Watch fail/refactor under green.

**Files.** `backend/api/settings.py`, tests.
**Commit.** `refactor(M1): _upsert_singleton helper for settings endpoints`.

---

## M1-T4 — Letter-only regenerate endpoint (T1b prep for M2)

**Goal.** `POST /api/documents/{match_id}/regenerate` currently regenerates CV + letter.
Add `POST /api/documents/{match_id}/letter/regenerate` that regenerates only the cover letter
(reuses `LetterPipeline`, persists the `TailoredDocument(doc_type="letter")`). This both makes
`LetterPipeline` honestly consumed (T1b) and unblocks M2's letters editor.

**TDD.**
1. Test (`tests/test_documents_api.py` or nearest): POST returns 200 + a letter doc id; the
   `letter/pdf` endpoint then streams the regenerated file; 404 for unknown match.
2. Watch fail → implement → watch pass.

**Files.** `backend/api/documents.py` (+ `LetterPipeline` usage), tests.
**Commit.** `feat(M1): letter-only regenerate endpoint (T1b)`.

---

## M1-T5 — T1b honest-endpoint audit (Embedder / FitEngine / dead surface)

**Goal.** Grep for collaborators instantiated in `backend/main.py` / stored on `app.state` but
never consumed by any API route or scheduler path (previously flagged: `Embedder`, `FitEngine`,
`LetterPipeline`). For each: confirm it IS now reached (LetterPipeline → yes via M1-T4 + M2), or
wire it to its intended consumer, or delete it with a one-line justification in the commit body.
Do NOT delete anything still reached on a live path. Produce a short findings note appended to
the M1 plan file (or commit body) listing each surface and its disposition.

**TDD.**
1. If anything is deleted: a test asserting the removed import/route is gone and the suite is
   green. If wired: a test exercising the now-reachable path.
2. No behavioral regressions.

**Files.** `backend/main.py`, possibly `backend/api/*`, tests.
**Commit.** `refactor(M1): T1b honest-endpoint audit (wire/remove dead surface)`.

---

## M1-T6 — Pragmatic strategy de-duplication

**Goal.** Remove the literally-duplicated blocks between `backend/applier/auto_apply.py` and
`assisted_apply.py` by extracting shared helpers (new module
`backend/applier/_strategy_common.py` or additions to an existing shared spot):
- `site_profile_key(url) -> str` (the duplicated `_site_key`)
- `is_multi_step_site(url) -> bool`
- `build_browser(browser_kwargs, saved_session_path) -> Browser` (the duplicated boot + session load)
- `build_fill_prompt(mode, …)` or a shared template constant for the LinkedIn-vs-generic prompt.
**No control-flow rewrite.** Strategies keep their distinct submit-vs-leave-open behavior; they
just call the shared helpers. Preserve the `_active_browser` / `on_review` wiring from M1-T1.

**TDD.**
1. Tests: the shared helpers behave identically to the old per-strategy versions (port existing
   assertions for `_site_key`/`_is_multi_step_site`); both strategies import from the shared
   module (assert no duplicate private defs remain via a grep-style test or import check).
2. `tests/test_apply_*` stay green throughout.

**Files.** `backend/applier/auto_apply.py`, `assisted_apply.py`, new shared module, tests.
**Commit.** `refactor(M1): de-duplicate auto/assisted apply strategies`.

---

## M1 verification (end of milestone)

`uv run pytest -q` (≥ baseline, all green) and `uv run pyright backend/` (≤ 43/8). Confirm
`GET /api/applications/{id}/review-state` returns a real payload in an end-to-end-ish test, and
`rg -n "_site_key|_is_multi_step_site" backend/applier/{auto_apply,assisted_apply}.py` shows the
private duplicates are gone (defined once in the shared module).
