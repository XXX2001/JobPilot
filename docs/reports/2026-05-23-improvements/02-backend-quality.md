# 02 — Backend Code Quality & Refactoring

**Scope.** Forward-looking refactor opportunities. The 12-PR sprint already closed CORS, SecretStr, DB indexes, daily-limit race, prompt caching, response_model, typed WS, JSON logs, naming sweep, and Pyright basic mode. None of those are re-listed here. The prior audit's "should-fix" findings are also excluded.

---

## 1. High-leverage refactoring opportunities

### R1. `AutoApplyStrategy` and `AssistedApplyStrategy` are 80% copy-paste — **medium**

[`backend/applier/auto_apply.py`](../../../backend/applier/auto_apply.py) and [`backend/applier/assisted_apply.py`](../../../backend/applier/assisted_apply.py) share three full structural blocks:

- **Tier 1/Tier 2 gating** (`_is_multi_step_site` + `_form_filler` fallback) — auto:96-131 vs assisted:81-112
- **Browser-use `Browser(**browser_kwargs)` boot incl. saved-session loading** — auto:286-300 vs assisted:146-160
- **LinkedIn-vs-generic prompt template** — auto:148-234 vs assisted:210-283

The only real divergence is "submit after confirm" vs "leave browser open". Today `_site_key` is **literally duplicated character-for-character** between auto_apply:33-39 and assisted_apply:37-42, and `_is_multi_step_site` is duplicated twice more.

**Direction:** extract a `BrowserApplyContext` async context-manager (handles state-path resolution, `Browser` construction, kill-on-error, screenshot capture) and a `build_fill_task(mode: ApplyMode, …)` pure function; both strategies become ~80 LOC orchestrators.

---

### R2. `BatchRunner.run_batch` is a 220-line procedural pipeline — **medium**

[`backend/scheduler/batch_runner.py:152-372`](../../../backend/scheduler/batch_runner.py#L152-L372) reads like a script: load → scrape → rank → store → fit-assess → tailor → broadcast, each phase guarded by ad-hoc `await self._broadcast_and_track(...)` calls with hand-tuned progress floats (0.05, 0.35, 0.55, 0.58, 0.65 + lerp). The interleaved `asyncio.Semaphore(CONCURRENCY_GEMINI)` blocks (lines 231-275 and 306-356) repeat the same `gather(return_exceptions=True)` → per-item-merge pattern.

**Direction:** make each phase a small `Phase` object with `name`, `weight`, and `async run(ctx) -> PhaseResult`; have `BatchRunner` walk a list and auto-emit progress as `cumulative_weight / total_weight`. **Side benefit:** phases become individually unit-testable (today `tests/test_batch_runner.py` has to mock the whole pipeline).

---

### R3. `PUT /api/settings/search` and `PUT /api/settings/profile` are textbook field-by-field upserts — **small**

[`backend/api/settings.py:185-233`](../../../backend/api/settings.py#L185-L233) (profile) and `:251-320` (search) each repeat the same pattern: `if body.X is not None: row.X = body.X` × 11 fields and × 17 fields respectively, with a parallel "if no row, construct fresh" branch that duplicates every default.

**Direction:** `model.update_from(body.model_dump(exclude_unset=True))` via a small `_upsert_singleton(model_cls, id_=1, body, defaults={…})` helper. Cuts ~120 LOC and eliminates the bug-class where a new field is added to `ProfileUpdate` but forgotten in the upsert block — **this is exactly the bug fixed in PR-10** (see verification-report F-Q4 bonus fix).

---

### R4. `applier.engine.ApplicationEngine` mixes three responsibilities — **medium**

[`backend/applier/engine.py:46-380`](../../../backend/applier/engine.py#L46-L380) is simultaneously:

- (a) WS signal router (`signal_confirm`/`signal_cancel`, lines 75-83)
- (b) Daily-limit reservation orchestrator (lines 104-167, with three separate try/except blocks just to release the placeholder)
- (c) DB persistence layer for `Application` + `ApplicationEvent` + `JobMatch.status` (`_record_application`, lines 283-380)

The `_release_reserved_slot` cleanup is called from three sites (lines 132-139, 161-167, 189-195), each wrapped in `try/except: pass` with paragraph-long comments justifying why.

**Direction:** move the reservation lifecycle into `DailyLimitGuard` as `async with guard.reserve(...) as slot:` (auto-releases on exception); move `_record_application` into an `ApplicationRecorder` collaborator. The engine becomes ~100 LOC of dispatch + signal-routing.

---

### R5. `BrowserSessionManager._attempt_auto_login` hardcodes per-site flows — **small**

[`backend/scraping/session_manager.py:259-431`](../../../backend/scraping/session_manager.py#L259-L431) has parallel hand-rolled login flows for LinkedIn (lines 330-353) and Indeed (lines 355-407), each ~30 lines of selector-list + fill + click + sleep + url-check. Adding a third site means a third `elif site == "...":` block. The selectors and post-login URL pattern are the only per-site data.

**Direction:** declare them as `SITE_LOGIN_FLOWS: dict[str, LoginFlow]` (where `LoginFlow` is a small dataclass holding `login_url`, `email_selectors`, `password_selectors`, `success_url_excludes`); the runner loop becomes 25 lines and works for any new site by adding one dict entry.

---

### R6. `form_filler.fill_and_submit` (190 LOC) and `fill_only` (90 LOC) duplicate Phases 1-3 — **small**

[`backend/applier/form_filler.py:52-229`](../../../backend/applier/form_filler.py#L52-L229) and `:231-334` repeat:

- Persistent-context launch with same args (lines 93-100 vs 259-266)
- Stealth import (102-108 vs 267-272)
- `page.goto` (110 vs 274)
- `_clean_form_html` + `_build_fill_prompt` (121-132 vs 276-287)
- Gemini call (134-135 vs 289-290)
- Field fill loop (139-148 vs 292-302)
- File-upload loop (151-167 vs 304-318)

**Direction:** extract `_prepare_filled_page() -> (page, mapping, context, pw)`; `fill_and_submit` continues from there with screenshot + WS + wait + submit; `fill_only` just returns. Drops ~110 LOC and the two will never silently diverge.

---

### R7. `applications.py:apply_to_job` does too much endpoint-side wiring — **medium**

[`backend/api/applications.py:375-523`](../../../backend/api/applications.py#L375-L523) is 150 lines for one POST: profile fallback for `full_name`/`email`/`phone`/`location` (5 chains, 408-412), `additional_info` JSON merge with two try/except blocks (414-461), `apply_url` resolution (464-473), document resolution with three-level fallback (484-507). Most of this is *applicant-context assembly* that belongs next to `ApplicantInfo`.

**Direction:** `ApplicantInfo.from_request(body, profile)` classmethod + `resolve_documents(match_id, profile, db) -> Documents` collaborator; the route shrinks to ~30 lines of "validate → assemble → dispatch → return".

---

## 2. Dead-code / can-delete candidates

### D1. `backend/utils/retry.py:async_retry` — **high confidence, delete**

The only file-map entry promising "used ad-hoc" never panned out. `grep -rn 'from backend.utils.retry\|async_retry' backend/` returns zero hits anywhere except the function's own definition site. **41 lines of dead retry decorator** at [`backend/utils/retry.py:15-41`](../../../backend/utils/retry.py#L15-L41).

### D2. `backend/utils/source_health.py` (entire module) — **high confidence, delete**

`SourceHealthMonitor`, `health_monitor` singleton, `record_success`/`record_failure` API — no external importer. `grep -rn 'source_health\|health_monitor' backend/ | grep -v utils/source_health.py` returns zero. **107 lines dead.**

### D3. `LetterPipeline` and `latex.pipeline.generate_diff` legacy helper — **medium confidence, audit-then-delete**

`LetterPipeline` is instantiated at [`backend/main.py:96`](../../../backend/main.py#L96) and stored at `app.state.letter_pipeline:132`, but `grep -rn 'letter_pipeline\.' backend/api/` returns zero — **no API route consumes it.** Same "wired-but-unused" smell flagged previously for Embedder/FitEngine. Similarly, `generate_diff` ([`backend/latex/pipeline.py:253-297`](../../../backend/latex/pipeline.py#L253-L297)) has zero callers — `CVPipeline.generate_tailored_cv` builds its diff inline (lines 146-154). Either ship the letter-tailoring API route this enables (see [PG-2 in the product-gaps report](03-product-gaps.md)), or delete ~120 LOC.

---

## 3. Type-system upgrades that would catch real bugs

### T1. `ApplicationResult.status` and `.method` should be `Literal`/enum

[`backend/applier/manual_apply.py:15-18`](../../../backend/applier/manual_apply.py#L15-L18) types both as bare `str`, with the allowed values noted only in a comment (`"applied" | "assisted" | "manual" | "cancelled"`). The vocabulary already exists as constants in [`backend/applier/__init__.py:31-49`](../../../backend/applier/__init__.py#L31-L49) (`RESULT_APPLIED`, `RESULT_MANUAL`, etc.) and the engine even has a `normalize_result_status` translation function.

Today `auto_apply.py:90` returns `status="cancelled"` as a raw string; `assisted_apply.py:78` likewise; `manual_apply.py:60` returns `status="manual"`. Pyright cannot catch a typo like `status="cancled"`.

**Fix:** `class ApplicationResult(BaseModel): status: Literal["applied", "assisted", "manual", "cancelled", "failed"]; method: Literal["auto", "assisted", "manual"]`. Catches typos *and* the existing engine-vs-strategy drift the verification-report Q2 hinted at.

### T2. `JobFilters.experience_range`/`min_score` and `RawJob`/`JobDetails` could share a `JobBase` discriminated parent

[`backend/models/schemas.py`](../../../backend/models/schemas.py) defines `RawJob` (scraper output) and `JobDetails` (matcher input) with overlapping fields, currently passed between layers via `_raw_to_details` ([`backend/scheduler/batch_runner.py:551-566`](../../../backend/scheduler/batch_runner.py#L551-L566)) that uses `getattr(raw, "X", default)` × 10 — **defeating typing entirely.**

Worse: `orchestrator._empty_filters()` (lines 356-369) constructs `JobFilters` with a different field set (`experience_range=None`) than `batch_runner.py:166-174` uses (`min_score=...`, no `experience_range`). The two construction sites have *different keyword arguments* but no static check fails because both signatures permit it via defaults.

**Fix:** a single `BaseJob(BaseModel)` parent with the 8 shared fields; `RawJob`/`JobDetails` inherit; `_raw_to_details` becomes `JobDetails.model_validate(raw.model_dump())`. And: tighten `JobFilters` to a frozen dataclass with all required fields explicit — the drift becomes a type error.

---

## 4. "If you had a free week" investment

### Extract the apply-flow into an explicit state machine

The most fragile area of the codebase is the apply lifecycle. Today it's spread across:

- [`ApplicationEngine.apply`](../../../backend/applier/engine.py) (reservation + dispatch + recording + cleanup)
- [`AutoApplyStrategy.apply`](../../../backend/applier/auto_apply.py) (Tier 1→2 fallback + browser teardown)
- [`PlaywrightFormFiller.fill_and_submit`](../../../backend/applier/form_filler.py) (CAPTCHA → fill → review → wait → submit)
- [`captcha_handler.wait_for_captcha_resolution`](../../../backend/applier/captcha_handler.py) (poll loop + WS)

Cancellation, errors, and timeouts are handled inconsistently: `_release_reserved_slot` is called from 3 sites with 3 different try/except patterns; browser cleanup is `try: await browser.stop(); except: pass` repeated 6 times in `auto_apply.py` alone; the "remote-submitted-but-DB-write-failed" edge case at engine.py:183-204 is handled with a hand-written compensation. **There's no single place to read "what are the possible states an apply attempt can be in?"**

**Model it as a real FSM:** states `Reserved → CaptchaCheck → Filling → AwaitingConfirm → Submitting → Recording → {Applied | Cancelled | Failed | RemoteSubmittedLocalFailed}`, transitions emit WS events automatically, terminal states run the right compensation (release slot, close browser, update JobMatch.status). Implementation could be a small `Statechart` class in `backend/applier/state.py` with an `async def run(self, ctx) -> Outcome` driver.

**Benefits compound:**
- (a) The duplication between `AutoApplyStrategy` and `AssistedApplyStrategy` (R1) collapses because they're just different state graphs over the same primitives.
- (b) The WS `apply_review`/`apply_result` messages get generated automatically instead of hand-broadcast from 3 places.
- (c) Cancellation becomes "transition to Cancelled" with one compensation path instead of 3.
- (d) Testing becomes "drive the FSM with mock primitives" — finally giving the hot path (audit's TS-17) the coverage it deserves.

**Estimated 3-4 days of work, deletes ~400 LOC, eliminates an entire class of "did we remember to release/close/rollback?" bugs.**
