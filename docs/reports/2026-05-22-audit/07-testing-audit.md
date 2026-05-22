# Testing Audit — JobPilot Test Suite

Scope: `tests/` (37 .py files, 4,662 LOC, 281 test functions) against `backend/` (71 .py files, 13,217 LOC).
Date: 2026-05-22. This audit does NOT re-litigate the naming / EH / TY / DC items already covered in
`docs/reports/2026-05-22-standards/`.

Tools surveyed: `pyproject.toml` (pytest config), `tests/conftest.py`, every `tests/test_*.py` and
`tests/integration/test_full_pipeline.py`. Coverage estimates are file-presence + behaviour-walkthrough,
**not** measured — there is no `pytest --cov`, no `coverage.xml`, no `[tool.coverage]` block, no CI.

---

## TL;DR

The suite is **breadth-first and over-mocked**: 281 tests touch most modules but exercise mostly
narrow helpers and golden-path API shapes, with the **production SQLite database used as the test
DB** (no isolation between tests), the conftest's headline mocks (`mock_gemini`, `test_settings`)
**never imported by any test**, and zero coverage of the captcha handler, ws message dispatch, latex
compiler, scrapling fetcher's fetch path, source-health, retry util, or the FormFiller's actual
Playwright drive — the riskiest production code paths. **Top 3 risks**: (1) tests mutate a shared
on-disk DB and pass only because creation order accidentally aligns; (2) `captcha_handler.py` (364
LOC, runs on every auto-apply) has zero tests; (3) integration test docstring claims "in-memory
SQLite" but the suite writes to `data/jobpilot.db` — a maintainer reading the comment will trust an
isolation guarantee that doesn't exist.

---

## Module-by-module coverage

Estimated by file presence + behaviours touched. "Tested %" is a rough behavioural-line estimate, not
measured coverage. Modules are grouped by `backend/` subpackage.

| Module | Src LOC | Test file | Tested behaviours (%) | Critical gaps |
|---|---|---|---|---|
| `backend/main.py` | 389 | `test_smoke.py` (4 tests), `test_error_handling.py` (registration) | ~10% | lifespan, singleton wiring, ws handler registration, SPA static, all 3 typed exception handlers |
| `backend/config.py` | 91 | `test_config_scraper_headless.py` (2), partial via conftest fixture | ~25% | env file precedence, secret-rendering, validation, `_DEFAULT_*` derivation |
| `backend/database.py` | 183 | none directly | **~0%** | `init_db`, `_migrate_add_columns`, `_seed_default_sources`, `db_session()` rollback semantics, WAL pragma |
| `backend/defaults.py` | 48 | `test_defaults.py` (4) | ~80% | ordering & relations between thresholds — adequate |
| `backend/api/jobs.py` | 274 | `test_api_jobs.py` (5), `test_full_pipeline.py` | ~30% | search happy-path body, GET filtering, score endpoint past 404 |
| `backend/api/queue.py` | 332 | `test_api_jobs.py` (4), `test_full_pipeline.py` | ~25% | enrich endpoint, refresh background task, status transitions, dedup of in-flight enriches |
| `backend/api/applications.py` | 568 | `test_api_routes.py` (7), `test_apply_engine.py` (2 helper), `test_full_pipeline.py` | ~35% | apply endpoint itself (the whole point), event listing pagination, status-change auth |
| `backend/api/documents.py` | 267 | `test_api_routes.py` (5) — all 404 cases | ~15% | PDF streaming bytes, diff JSON shape, regenerate (already flagged RG-01 — but no negative tests either) |
| `backend/api/settings.py` | 778 | `test_api_routes.py` (8), `test_full_pipeline.py` | ~20% | credentials encrypt/decrypt round-trip, custom-sites CRUD, source toggling, profile validators |
| `backend/api/analytics.py` | 151 | `test_api_routes.py` (3), `test_full_pipeline.py` | ~50% | response-rate edge cases (0 apps, all rejected), source/method break-downs |
| `backend/api/ws.py` | 229 | `test_websocket.py` (4) | ~30% | client-message routing (login_done/cancel, confirm_submit, cancel_apply), broadcast under concurrent clients, connection-drop cleanup |
| `backend/api/ws_models.py` | 197 | none directly | ~5% | union dispatch, payload validation, error frames |
| `backend/api/deps.py` | 82 | indirect via TestClient | ~20% | engine/runner getters, missing-singleton fallback |
| `backend/applier/engine.py` | 305 | `test_apply_engine.py` (5) | ~55% | DI signal propagation tested; confirm-await timeout, retry-on-rollback, event-loop cancellation not |
| `backend/applier/daily_limit.py` | 87 | `test_apply_engine.py` (4) | ~85% | strong — adequate |
| `backend/applier/manual_apply.py` | 97 | `test_apply_engine.py` (2) | ~70% | webbrowser-failure path covered |
| `backend/applier/assisted_apply.py` | 311 | `test_apply_engine.py` (3) | ~25% | only Tier-1 path; agent state machine, confirm event, cancel mid-fill untested |
| `backend/applier/auto_apply.py` | 486 | `test_apply_engine.py` (5) | ~30% | Tier-1/Tier-2 routing tested; browser-use Agent task construction, run timeout, two-tier persistence not |
| `backend/applier/form_filler.py` | 533 | `test_form_filler.py` (10) | ~30% | only pure helpers (`_clean_form_html`, `_build_fill_prompt`, `_parse_gemini_response`); `fill_and_submit`/`fill_only` (the actual Playwright drive) untested |
| `backend/applier/captcha_handler.py` | 364 | **none** | **0%** | entire 364-LOC module — captcha detection, screenshot, prompt user, retry — runs in every auto-apply |
| `backend/llm/gemini_client.py` | 293 | `test_gemini_client.py` (4), `test_error_handling.py` (2) | ~45% | rate-limit window + retry tested; model fallback chain, embedding error path, streaming, timeout |
| `backend/llm/job_analyzer.py` | 65 | `test_job_analyzer.py` (3) | ~85% | adequate |
| `backend/llm/cv_modifier.py` | 161 | `test_cv_modifier.py` (4) | ~70% | `modify_from_assessment` happy path; ranking + capping; prompt sanitization not |
| `backend/llm/cv_editor.py` | 122 | **none** | **0%** | entire module — letter editing pipeline |
| `backend/llm/prompts.py` | 226 | **none** | **0%** | template formatting, variable substitution, schema-injection guards |
| `backend/llm/validators.py` | 72 | indirectly via cv_modifier/job_context | ~40% | `top_three()`, `is_applicable()` covered; pydantic validators (length, confidence range) not directly |
| `backend/llm/job_context.py` | 84 | `test_job_context.py` (4) | ~75% | adequate |
| `backend/matching/matcher.py` | 157 | `test_matcher.py` (4) | ~70% | strong on score + rank; freshness boost & posted_date influence not |
| `backend/matching/cv_parser.py` | 276 | `test_cv_parser.py` (6) | ~60% | fallback path covered; LaTeX-table parsing edge cases (commands inside cells), Unicode handling not |
| `backend/matching/job_skill_extractor.py` | 194 | `test_job_skill_extractor.py` (6) | ~65% | knockout detection covered; multilingual section headers (`Exigences`, `Anforderungen`) not |
| `backend/matching/fit_engine.py` | 220 | `test_fit_engine.py` (7), `test_fit_integration.py` (4) | ~75% | strong |
| `backend/matching/embedder.py` | 83 | `test_embedder.py` (4) | ~70% | batch-size limits, embedding-dim mismatch not tested |
| `backend/matching/skill_patterns.py` | 99 | `test_skill_patterns.py` (4) | ~60% | adequate for what's exposed |
| `backend/matching/filters.py` | 38 | indirect | ~30% | `JobFilters` is mostly a dataclass; validation rules untested |
| `backend/latex/parser.py` | 121 | `test_latex_parser.py` (5) | ~70% | marker fallback + injection tested |
| `backend/latex/injector.py` | 87 | `test_latex_parser.py` (1 round-trip) | ~50% | only summary injection; experience bullets injection not |
| `backend/latex/applicator.py` | 99 | `test_cv_applicator.py` (7) | ~85% | strong |
| `backend/latex/pipeline.py` | 397 | `test_latex_pipeline.py` (5), `test_full_pipeline.py` (1) | ~45% | 4 of 5 tests are `pytest.skip` if Tectonic missing → effectively unrun on CI; LetterPipeline 0% |
| `backend/latex/compiler.py` | 107 | **none** | **0%** | Tectonic invocation, timeout, error parsing, output path detection |
| `backend/latex/validator.py` | 127 | **none** | **0%** | LaTeX syntax checks before compile |
| `backend/scheduler/morning_batch.py` | 646 | `test_morning_batch.py` (3), `test_morning_batch_cv_fallback.py` (5) | ~30% | path-resolution helper well-covered; the 646-LOC orchestration body has 3 tests that mock 8+ collaborators each |
| `backend/scraping/adzuna_client.py` | 108 | `test_adzuna_client.py` (3) | ~65% | rate-limit headers, pagination, country-code mapping |
| `backend/scraping/orchestrator.py` | 397 | `test_scraping.py` (3) | ~25% | merge + dedup tested; site-specific dispatch, partial-failure tracking, source-health update not |
| `backend/scraping/adaptive_scraper.py` | 315 | `test_scraping.py` (5 helpers), `test_error_handling.py` (2 retry) | ~40% | retry covered; the full `scrape_job_listings` path with successful agent only via `test_error_handling`'s "second attempt succeeds" |
| `backend/scraping/scrapling_fetcher.py` | 406 | `test_google_jobs_scraping.py` (20+) | ~30% | URL building + selectors + HTML cleaning well-covered for **google_jobs only**; LinkedIn/Indeed/Glassdoor selectors and the actual `fetch()` HTTP path untested |
| `backend/scraping/session_manager.py` | 481 | `test_session_manager.py` (6) | ~25% | list/clear/confirm covered; auto-login flow, cookie persistence, browser-state corruption, timeout untested |
| `backend/scraping/deduplicator.py` | 52 | `test_deduplicator.py` (2) | ~70% | adequate |
| `backend/scraping/json_utils.py` | 212 | indirect via `test_scraping.py::_extract_json_from_text` (5) | ~30% | only the extractor; the rest of the file (normalization, deep-clean, dict-walking) untested |
| `backend/scraping/site_prompts.py` | 705 | `test_google_jobs_scraping.py` partial | ~10% | `format_prompt` covered for one site; SITE_CONFIGS structural integrity not |
| `backend/security/sanitizer.py` | 142 | `test_sanitizer.py` (24, class-organized) | ~95% | strongest module in the audit |
| `backend/utils/retry.py` | 80 | **none** | **0%** | entire retry decorator |
| `backend/utils/browser_path.py` | 80 | **none** | **0%** | Chromium-binary discovery (the very thing Windows tests diagnose externally) |
| `backend/utils/source_health.py` | 175 | **none** | **0%** | source consecutive-failure tracking, mute logic |
| `backend/models/user.py` | 116 | indirect via API routes | ~20% | encrypted-credential field, JSON column validators |
| `backend/models/application.py` | 67 | indirect via API routes | ~20% | enum status transitions, event ordering |
| `backend/models/job.py` | 112 | `test_fit_models.py` (2) | ~25% | column presence asserted, no behavioural validation |
| `backend/models/document.py` | 49 | indirect via API routes | ~10% | nothing direct |
| `backend/models/session.py` | 40 | indirect via API routes | ~10% | nothing direct |
| `backend/models/schemas.py` | 76 | indirect via every test using `RawJob`/`JobDetails` | ~50% | pydantic validators, required-field enforcement not tested directly |
| `backend/models/base.py` | 23 | none | n/a (declarative base) | n/a |

**Modules at 0%** (priority order by risk): `captcha_handler.py`, `cv_editor.py`, `prompts.py`,
`latex/compiler.py`, `latex/validator.py`, `utils/retry.py`, `utils/source_health.py`,
`utils/browser_path.py`, `database.py` (no direct tests).

---

## Findings

| ID | Title | Severity |
|---|---|---|
| TS-01 | Tests share the production SQLite file — no per-test DB isolation | **Critical** |
| TS-02 | `captcha_handler.py` (364 LOC, runs on every auto-apply) has zero tests | **Critical** |
| TS-03 | "in-memory SQLite" docstring in `test_full_pipeline.py` is false — sets a trap | **High** |
| TS-04 | `conftest.py`'s `mock_gemini` and `test_settings` fixtures are never imported | **High** |
| TS-05 | LaTeX-pipeline tests `pytest.skip` when Tectonic missing — CI runs 0 of them | **High** |
| TS-06 | `TestClient(raise_server_exceptions=False)` hides 500s as soft assertions | **High** |
| TS-07 | API tests accept disjunctive status (`in (200, 502)`, `in (200, 404)`) — non-tests | **High** |
| TS-08 | No pytest markers — slow, integration, e2e, requires-tectonic all run together | Medium |
| TS-09 | No coverage tooling configured (`pyproject.toml` has no `[tool.coverage]`, no CI) | Medium |
| TS-10 | Mocks rebuild client internals via `__new__` then poke private attrs — brittle | Medium |
| TS-11 | `test_websocket.test_broadcast_status_helper` doesn't test the helper it names | Medium |
| TS-12 | No property-based testing (`hypothesis`) on CV parsers or skill extractors | Low |
| TS-13 | No snapshot/regression tests for LLM prompts or scraped HTML cleaning | Low |
| TS-14 | Test naming is generally descriptive — but several use generic verbs | Low |
| TS-15 | `test_apply_engine.test_engine_signal_unknown_job_no_error` asserts nothing | Low |
| TS-16 | LetterPipeline (sibling of CVPipeline in `latex/pipeline.py`) is entirely untested | High |
| TS-17 | The apply HTTP endpoint itself (`POST /api/applications/{match_id}/apply`) has no test | **Critical** |

---

## Per-finding details

### TS-01 — Production SQLite file used as test DB

**Evidence**: `backend/database.py:42` opens
`sqlite+aiosqlite:///{settings.jobpilot_data_dir}/jobpilot.db`. `tests/conftest.py` sets
`JOBPILOT_HOST/PORT/GOOGLE_API_KEY/ADZUNA_*` via `monkeypatch.setenv` but **does not** override
`JOBPILOT_DATA_DIR` or substitute a `:memory:` URL. The on-disk file
`/home/mouad/Web-automation/data/jobpilot.db` exists with WAL files.

**Impact**:
- Tests pollute developer state (apps you create in `test_create_application` persist).
- Tests pass non-deterministically based on residual rows. `test_analytics_end_to_end` works around this:
  ```python
  baseline_total = baseline.get("total_apps", 0)
  ... assert summary["total_apps"] >= baseline_total + 2
  ```
  — the `>=` (instead of `==`) and the baseline read are tells that the author knew the DB wasn't clean.
- Running tests in random order (e.g. `pytest --randomly`) will break `test_get_profile_not_found_initially`
  (which already hedges: `assert resp.status_code in (200, 404)`).
- `init_db()` runs on every `TestClient(app)` startup — which mutates schema and seeds sources.

**Fix**: Add a session-scoped autouse fixture in `conftest.py` that points
`settings.jobpilot_data_dir` at `tmp_path_factory.mktemp("db")` BEFORE `backend.main` is imported, OR
swap the engine URL to `sqlite+aiosqlite:///:memory:` with `StaticPool` and `connect_args={"check_same_thread": False}`.

---

### TS-02 — `applier/captcha_handler.py` has zero tests

**Evidence**: `find tests/ -name '*captcha*'` → no results. `grep -r captcha tests/` → no results.
`backend/applier/captcha_handler.py` is 364 LOC, imported by `auto_apply.py:30` and
`assisted_apply.py:24`, invoked on every Playwright apply attempt.

**Impact**: A captcha detection regression silently breaks every auto-apply. The module also makes
LLM calls (vision prompts for solving) and Playwright screenshots — both heavily mockable but
none mocked anywhere.

**Fix**: Add `tests/test_captcha_handler.py` with at minimum: detection on known captcha selectors,
fallback when LLM returns garbage, timeout handling, screenshot path collision.

---

### TS-03 — False "in-memory SQLite" docstring

**Evidence**: `tests/integration/test_full_pipeline.py:4`:
```
using the real FastAPI TestClient with an in-memory SQLite database.
```
But the suite uses `data/jobpilot.db` (see TS-01). A maintainer adding a destructive test on the
strength of that comment will trash dev data.

**Fix**: Either make it true (preferred — fixes TS-01) or delete the comment.

---

### TS-04 — Conftest fixtures `mock_gemini` and `test_settings` are unused

**Evidence**: `grep -rE 'mock_gemini' tests/` shows only the conftest definition + 4 local
`_mock_gemini_client()` redefinitions in `test_embedder.py`. `grep -rE 'test_settings\(' tests/`
finds only the conftest fixture; no test takes `test_settings` as a parameter except `test_smoke.py::test_config_loads`.

**Impact**: 50+ tests roll their own `MagicMock()` + `client.generate_json = AsyncMock(...)`
boilerplate when a shared fixture exists. New tests don't discover the fixture because nothing references it.

**Fix**: Either delete the unused fixtures and standardize on the local pattern, or promote
`_mock_gemini_client` to `conftest.py` and use it everywhere.

---

### TS-05 — Tectonic-skipped tests = ~zero LaTeX coverage on CI

**Evidence**: `tests/test_latex_pipeline.py` has 5 tests; 4 begin with:
```python
if shutil.which("tectonic") is None:
    pytest.skip("Tectonic not installed")
```
On any environment without Tectonic (including default CI runners), these silently skip — but the
test summary shows green. The fifth test (`test_generate_base_cv`) mocks the compiler so it does run.

**Impact**: The whole pipeline-with-modifiers integration is "covered" only when a human runs it
locally. There's no `pytest.mark.requires_tectonic` to make the skip visible in CI summary, no
matrix entry that installs Tectonic.

**Fix**: Either mark all skipped tests with `@pytest.mark.requires_tectonic` + run in a CI job with
Tectonic installed, OR replace the real-Tectonic path with a fake `Compiler` mock everywhere (the
fallback test `test_cv_pipeline_modifier_failure_falls_back` shows it's already doable).

---

### TS-06 — `raise_server_exceptions=False` hides crashes

**Evidence**: `tests/conftest.py:25`:
```python
with TestClient(app, raise_server_exceptions=False) as client:
```
This means unhandled exceptions inside route handlers become **500 responses**, not test failures.
Combined with `test_search_jobs_schema_valid` (`assert resp.status_code in (200, 502)`) and
`test_stub_routes_respond` (`assert resp.status_code in (200, 404)`), the test client cannot
distinguish "route ran cleanly" from "route exploded but EH-05 swallowed it".

**Impact**: Regressions that turn 200s into 500s pass green.

**Fix**: Default `raise_server_exceptions=True`; add a separate fixture for the (very few) tests
that intentionally exercise the global exception handler.

---

### TS-07 — Disjunctive status-code asserts are not tests

**Evidence**: 3 occurrences:
- `test_api_jobs.py:48` — `assert resp.status_code in (200, 502)` ("either succeeds or fails at Adzuna")
- `test_api_routes.py:176` — `assert resp.status_code in (200, 404)` (profile may or may not exist)
- `test_smoke.py:27` — `assert resp.status_code in (200, 404)` (route may or may not be wired)

Each asserts that the server **didn't 5xx**, not that it did the right thing.

**Fix**: Mock Adzuna for the first (already done in `test_full_pipeline.py:48`); use isolated DB to
make the second deterministic; remove the third (covered properly elsewhere).

---

### TS-08 — No marker taxonomy

**Evidence**: Only `pytest.mark.asyncio` is used (13 files). No `slow`, no `integration`, no `e2e`,
no `requires_tectonic`, no `requires_playwright`. `test_windows_playwright.py` actually launches
**real Chromium** (`p.chromium.launch(headless=True)` at line 91) — it's `@pytest.mark.asyncio` only.

**Impact**: A developer running `pytest -x` gets a real-browser launch + a Tectonic check + 200
fast unit tests in the same green/red signal. No way to scope `pytest -m "not slow"`.

**Fix**: Register markers in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "slow: launches a real browser or runs Tectonic",
    "integration: spans multiple modules with a real DB",
    "requires_tectonic: needs Tectonic on PATH",
    "requires_playwright: launches a real Chromium",
]
```
Tag `test_windows_playwright.py` and the 4 Tectonic-skipped tests; default CI runs `-m "not slow and not requires_tectonic"`.

---

### TS-09 — No coverage tooling

**Evidence**: `pyproject.toml` has `pytest>=8.0` in dev deps but no `pytest-cov`, no
`[tool.coverage]`, no `coverage.xml` or `.coveragerc`. No GitHub Actions in `.github/`.

**Impact**: Coverage % cannot be measured, regressions are invisible, this audit has to guess.

**Fix**: Add `pytest-cov` to dev deps; configure `[tool.coverage.run]` with `source = ["backend"]`
and `omit = ["backend/scraping/site_prompts.py"]` (a 705-LOC config blob, not really code); add a CI
gate at e.g. 60% line coverage.

---

### TS-10 — Mocks reach into client internals via `__new__` + private attrs

**Evidence**: `test_gemini_client.py:14`:
```python
client = GeminiClient.__new__(GeminiClient)
client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
client._lock = asyncio.Lock()
```
Same pattern in `test_error_handling.py:174`. Tests bypass `__init__` then set `_call_times`,
`_lock`, `_model_name` by hand.

**Impact**: A refactor that adds a new attribute to `__init__` (e.g. `_max_retries`) breaks every
one of these tests with `AttributeError` from a path far from the change.

**Fix**: Either add a `GeminiClient.for_testing()` factory classmethod, or use `dataclasses` with
defaults so tests can `GeminiClient(api_key="x")` without making real network calls.

---

### TS-11 — `test_broadcast_status_helper` doesn't test broadcast_status

**Evidence**: `tests/test_websocket.py:37–63`:
```python
def test_broadcast_status_helper():
    from backend.api.ws import broadcast_status, manager, ConnectionManager
    test_manager = ConnectionManager()
    ...
    async def run():
        test_manager.active_connections["test-client"] = FakeWS()
        payload = json.dumps({"type": "status", "message": "test", "progress": 0.5})
        await FakeWS().send_text(payload)
        return payload
    result = asyncio.run(run())
```
It creates a fake manager, imports `broadcast_status`, but **never calls** `broadcast_status`. It
manually constructs the payload and sends it to a throw-away `FakeWS()`. Asserts the payload it
just built has the fields it just put in it.

**Fix**: Actually call `await broadcast_status("test", 0.5)` against the real `manager` after
injecting the fake WS, then assert `received` was populated. Or delete the test.

---

### TS-12 — No property-based testing

**Evidence**: `grep -r hypothesis tests/` → empty. None of `cv_parser.py`, `job_skill_extractor.py`,
`sanitizer.py`, `adaptive_scraper._extract_json_from_text`, or `latex/parser.py` use property-based
generators — they all have hand-crafted strings.

**Impact**: Brittle to surprising inputs (e.g. `_extract_json_from_text` is tested with 5 strings;
JSON-with-fences-with-embedded-fences or trailing comma cases will hit it in prod first).

**Fix**: Add `hypothesis` to dev deps; cover at minimum: `sanitize_for_prompt(text)` invariants
(output length ≤ input length, never contains `<|im_start|>`, idempotent); `_extract_json_from_text`
(returns None or valid `json.loads`-able result).

---

### TS-13 — No snapshot tests

**Evidence**: `grep -r syrupy tests/` → empty. No `__snapshots__/` directory. LLM prompts
(`prompts.py`, `cv_modifier._build_prompt`, `form_filler._build_fill_prompt`) are constructed
piecewise and tested by `assert "Alice Dupont" in prompt` — which doesn't catch reordering or
section drift.

**Impact**: A prompt regression (e.g. accidentally dropping "do_not_touch" instructions) won't fail
any test as long as the named fields still appear.

**Fix**: For the 3 prompt builders, add `syrupy` snapshot tests with stable inputs. Review on
intentional change.

---

### TS-14 — Naming is generally good, with edge cases

**Good examples (keep)**:
- `test_apply_engine.py::test_engine_daily_limit_exceeded_returns_cancelled`
- `test_session_manager.py::test_existing_session_no_login_flow`
- `test_error_handling.py::test_gemini_json_retry_on_malformed_output`

**Weak naming (rename)**:
- `test_smoke.py::test_app_starts_without_error` — just asserts `client is not None` (the
  constructor would have raised if it didn't start)
- `test_websocket.py::test_broadcast_status_helper` — doesn't test what it names (see TS-11)
- `test_apply_engine.py::test_engine_signal_unknown_job_no_error` — asserts nothing (see TS-15)

---

### TS-15 — Empty-body assertion tests

**Evidence**: `test_apply_engine.py:193`:
```python
def test_engine_signal_unknown_job_no_error():
    engine = _make_engine()
    engine.signal_confirm(999)  # should not raise
    engine.signal_cancel(999)  # should not raise
```
No `assert` statement. The comment says what's tested but pytest only fails if an exception
actually escapes. Same pattern at `test_morning_batch.py:168` (`assert True`).

**Fix**: Either swap to `with does_not_raise():` (custom helper) or assert that internal state
(e.g. `engine._confirm_events`) remained unchanged.

---

### TS-16 — `LetterPipeline` 0% covered

**Evidence**: `backend/latex/pipeline.py` defines both `CVPipeline` (~250 LOC, partially tested) and
`LetterPipeline` (~80 LOC, sibling class). `grep -r LetterPipeline tests/` → empty.

**Impact**: Cover letter generation is in the user-visible flow (apply with letter); no test exists
for letter editing, compilation, or fallback.

**Fix**: Mirror the `test_latex_pipeline.py` cases for `LetterPipeline`.

---

### TS-17 — The actual apply HTTP endpoint has no test

**Evidence**: `backend/api/applications.py:413` defines `POST /api/applications/{match_id}/apply`
(the apply endpoint that runs the engine, generates docs, records to DB, sends WS). `grep -r
'applications/.*apply' tests/` → no hits. The closest tests are `_resolve_documents` helper unit
tests in `test_apply_engine.py:393` and abstract `ApplicationEngine.apply()` unit tests — neither
exercises the route.

**Impact**: The single most important endpoint in JobPilot is untested at HTTP level. Status code,
request validation, idempotency (see API audit §4), and error contract are all unverified.

**Fix**: Add API-level test mocking the engine, asserting 200/202/409 paths and that the
`Application` row exists after success.

---

## Recommended test additions (prioritised)

1. **DB isolation autouse fixture** — `conftest.py`: redirect `jobpilot_data_dir` to `tmp_path_factory.mktemp(...)` before any backend import. Fixes TS-01, TS-03, TS-07 partially.
2. **`tests/test_captcha_handler.py`** — full coverage of detection + LLM solve + fallback. Highest-risk untested module.
3. **`tests/test_apply_endpoint.py`** — POST `/api/applications/{match_id}/apply` happy-path + daily-limit-exceeded + match-not-found + engine-raises. TS-17.
4. **`tests/test_form_filler_playwright.py`** — `fill_and_submit` with a mocked Playwright `Page` (use `unittest.mock`'s `AsyncMock` to replicate `page.goto/locator/click`). The 533-LOC form filler has ~70% untested branches.
5. **`tests/test_letter_pipeline.py`** — `LetterPipeline` mirror of `test_latex_pipeline.py`. TS-16.
6. **`tests/test_ws_dispatch.py`** — drive `manager.dispatch(message)` for each registered handler (`login_done`, `login_cancel`, `confirm_submit`, `cancel_apply`). Currently 0% on the dispatch path.
7. **`tests/test_session_manager_login_flow.py`** — the auto-login coroutine (`_request_login` → broadcast → `confirm_login` → save state) end-to-end with a fake browser. 481 LOC at ~25%.
8. **`tests/test_source_health.py`** — consecutive-failure tracking, mute threshold, recovery on success. Module is 175 LOC at 0%.
9. **Property tests on `sanitize_for_prompt` and `_extract_json_from_text`** — TS-12.
10. **Snapshot tests on the 3 LLM prompt builders** — `JobAnalyzer._build_prompt`, `CVModifier._build_prompt`, `PlaywrightFormFiller._build_fill_prompt`. TS-13.

---

## Already good — don't break

- **`tests/test_sanitizer.py`** (24 class-organized tests, ~95% coverage) — best test file in the repo. Uses test classes for grouping, asserts both presence and absence, covers injection patterns exhaustively. Keep as the template for other modules.
- **`tests/test_fit_engine.py`** — exemplary unit-test discipline: small focused tests, well-named, single concept per test, uses `pytest.approx` for floats.
- **`tests/test_cv_applicator.py`** — covers happy path, rejection paths (low-confidence, missing-original, new-LaTeX-commands), capping, immutability assertion. Model for "writing a finite-state module's tests".
- **`tests/test_apply_engine.py` Tier-1/Tier-2 routing tests** — the 4 routing tests (`test_auto_apply_tier1_success_no_tier2`, `_falls_back_to_tier2`, `_cancelled_does_not_fall_back`, `_disabled_goes_straight_to_tier2`) cover the state space cleanly with one assertion per concept. Keep this pattern.
- **`tests/test_error_handling.py::test_global_exception_handler_returns_json`** — properly verifies that internal exception text does NOT leak to the response body. Exactly the right shape for security-relevant tests.
- **`tests/test_fit_models.py`** — short, surgical "column exists" tests. The right level of effort for ORM smoke checks.
- **Test naming overall** — most tests are `test_<verb>_<subject>_<expectation>` (`test_clear_session_missing_file_no_error`). Keep this convention; reject PRs that use `test_xxx_1`.
- **Async patterns** — `asyncio_mode = "auto"` in `pyproject.toml` removes the `@pytest.mark.asyncio` boilerplate; the suite uses it correctly.
