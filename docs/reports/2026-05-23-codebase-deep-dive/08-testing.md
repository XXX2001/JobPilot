# 08 — Testing Infrastructure

**Scope**: `tests/conftest.py`, all `tests/test_*.py` plus `tests/integration/test_full_pipeline.py`, `pyproject.toml` `[tool.pytest.ini_options]` + `[tool.coverage.*]`, the `.coverage` artifact, and a hunt for frontend tests.

**Numbers at a glance**

- 55 Python test files (54 under `tests/`, 1 under `tests/integration/`).
- ~470 individual test functions (raw `def test_…` plus class-method tests in `test_sanitizer.py` and `test_google_jobs_scraping.py`).
- 0 frontend tests. No `vitest`, `jest`, `playwright`, `@testing-library/*` in [`frontend/package.json`](../../../frontend/package.json) — only `svelte-check` for type checking.
- 1 binary coverage cache: [`.coverage`](../../../.coverage) (52 KB, branch-mode, `backend/` source).

---

## 1. Purpose & philosophy

The suite is a textbook "fast unit + selective integration" pyramid that gave up on the top of the pyramid (no E2E browser tests, no frontend tests, no contract tests against real Gmail/Adzuna/Gemini). What is here is dense and reasonably well-engineered for the backend half of the product:

- **Unit tests** dominate. Pure functions (the classifier, sanitizer, fit engine, matcher, prompt prefixes, helpers in `adaptive_scraper`, `scrapling_fetcher`, `form_filler`) are exercised with no I/O. Examples: [`test_sanitizer.py`](../../../tests/test_sanitizer.py), [`test_matcher.py`](../../../tests/test_matcher.py), [`test_fit_engine.py`](../../../tests/test_fit_engine.py), [`test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py), [`test_google_jobs_scraping.py`](../../../tests/test_google_jobs_scraping.py).
- **Component / integration tests** drive real FastAPI routes via `starlette.testclient.TestClient` against a real session-scoped `aiosqlite` file under `tempfile.mkdtemp(prefix="jobpilot-test-")`. Examples: [`test_api_routes.py`](../../../tests/test_api_routes.py), [`test_today.py`](../../../tests/test_today.py), [`test_correspondence_api.py`](../../../tests/test_correspondence_api.py), [`test_apply_http.py`](../../../tests/test_apply_http.py).
- **Concurrency / race-window regression tests** for hand-built code paths — [`test_daily_limit.py`](../../../tests/test_daily_limit.py) (TOCTOU at the SQLite level), [`test_gemini_client.py`](../../../tests/test_gemini_client.py:68) (rate-limiter doesn't hold the lock across `asyncio.sleep`).
- **Smoke tests** — [`test_smoke.py`](../../../tests/test_smoke.py) (4 trivial endpoint pings) and [`test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) (the only true end-to-end happy path; see §8).
- **Diagnostic tests** that are not really pytest tests in the unit-test sense — [`test_windows_playwright.py`](../../../tests/test_windows_playwright.py) is an ordered manual-run troubleshooting guide (§10).

Coverage goals are **implicit**: there is a `.coverage` file in the repo root but no badge, no CI threshold gate in [`pyproject.toml`](../../../pyproject.toml), no `--cov-fail-under` in `addopts`. The configuration says "tell us numbers if asked", not "fail under N%".

---

## 2. Configuration

[`pyproject.toml`](../../../pyproject.toml:37):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-p no:launch_testing -p no:launch_ros -p no:ament_flake8 …"
norecursedirs = [".venv", "node_modules", "__pycache__", ".git", "frontend"]
```

- `asyncio_mode = "auto"` — every plain `async def test_…` is automatically wrapped, no `@pytest.mark.asyncio` decorator needed. About a third of tests still decorate them anyway (e.g. [`test_apply_engine.py:25`](../../../tests/test_apply_engine.py:25)), which is harmless but inconsistent.
- The `-p no:launch_testing -p no:launch_ros …` block disables ROS / `ament` plugins that ship with system-wide ROS installs, otherwise pytest would refuse to collect on machines that have ROS in `PYTHONPATH`. Pragmatic and worth keeping.
- `norecursedirs` includes `frontend` — they're not going to add Vitest by accident.
- No custom markers are declared (no `markers = […]`), no marker selection strategy. There is no `slow`, `e2e`, `network`, or `windows` marker — Windows tests live in a file but are not tag-isolated.
- No `--cov` is in `addopts`; coverage runs are opt-in (`uv run pytest --cov`).
- No `-n auto` / `pytest-xdist` — the suite is **single-threaded by design** because of the shared session-scoped tmp DB (§4).

`[tool.coverage.run]` ([`pyproject.toml:43`](../../../pyproject.toml:43)):

```toml
source   = ["backend"]
branch   = true
omit     = ["backend/**/__init__.py", "*/tests/*"]
```

`[tool.coverage.report]` excludes `pragma: no cover`, `if TYPE_CHECKING`, `raise NotImplementedError`, `if __name__ == "__main__":`. `show_missing = true`, `skip_covered = false`.

The pyright sidecar ([`pyrightconfig.json`](../../../pyrightconfig.json)) is `basic` mode with `tests` included — so pyright sees the test code but in basic mode tolerates the wall of `MagicMock` / `# type: ignore` shrapnel (§10).

---

## 3. Conftest fixtures

There is exactly **one conftest** at [`tests/conftest.py`](../../../tests/conftest.py) — no subdirectory conftests, no shared `fixtures/conftest.py`. The contents are tiny:

| Item | Scope | File:line | What it does | Teardown |
|---|---|---|---|---|
| `_TEST_DATA_DIR` env override | module (import-time) | [`conftest.py:22`](../../../tests/conftest.py:22) | `os.environ["JOBPILOT_DATA_DIR"] = tempfile.mkdtemp(prefix="jobpilot-test-")` **before any backend import**. Forces `backend.database` to build its SQLAlchemy engine against a tmp dir. Also sets `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` placeholders so `Settings()` won't refuse to load on fresh checkouts. | `atexit.register(shutil.rmtree)` — runs at process exit, not per-test. |
| `test_app` | function (default) | [`conftest.py:32`](../../../tests/conftest.py:32) | Builds a `starlette.testclient.TestClient(app, raise_server_exceptions=False)`. `raise_server_exceptions=False` is deliberate so `test_apply_engine_raises_propagates_as_5xx` ([`test_apply_http.py:183`](../../../tests/test_apply_http.py:183)) can assert on the JSON body of the 500. | TestClient context-manager exit closes the lifespan. **Does not** wipe DB. |
| `test_settings` | function | [`conftest.py:47`](../../../tests/conftest.py:47) | Returns a fresh `Settings()` with deterministic monkey-patched env vars (`JOBPILOT_HOST=127.0.0.1`, `JOBPILOT_PORT=8000`, etc.). | `monkeypatch` auto-reverts at function teardown. |

Beyond these two, every other fixture is **local to its test module**. The two recurring patterns:

1. **`@pytest.fixture(autouse=True) async def _db(): await init_db()`** — used in every Gmail/correspondence test ([`test_gmail_credentials.py:15`](../../../tests/test_gmail_credentials.py:15), [`test_gmail_sync.py:14`](../../../tests/test_gmail_sync.py:14), [`test_gmail_auth.py:14`](../../../tests/test_gmail_auth.py:14), [`test_gmail_ws.py:13`](../../../tests/test_gmail_ws.py:13), [`test_gmail_scheduler.py:13`](../../../tests/test_gmail_scheduler.py:13), [`test_gmail_models.py:13`](../../../tests/test_gmail_models.py:13), [`test_correspondence_api.py:15`](../../../tests/test_correspondence_api.py:15)). Idempotent — `init_db()` only creates tables if missing.
2. **Per-file `app_with_gmail` fixture** — used in [`test_gmail_oauth_routes.py:11`](../../../tests/test_gmail_oauth_routes.py:11) and [`test_gmail_smoke.py:19`](../../../tests/test_gmail_smoke.py:19). Re-binds `backend.config.settings` and `backend.gmail.auth.settings` after `monkeypatch.setenv` because `gmail.auth` imports `settings` by name at module load. This is a re-occurring quirk worth documenting (§10).

`tests/fixtures/` contains a single static asset, [`sample_cv.tex`](../../../tests/fixtures/sample_cv.tex) (600 B). There are no factory-boy / Hypothesis strategies / Polyfactory test-data factories — every test that needs an `Application`, `Job`, or `GmailMessage` builds it inline (§10).

---

## 4. DB isolation strategy

**Strategy chosen: single session-scoped tmp SQLite file, no per-test rollback, no per-test wipe.** The whole suite shares one `jobpilot.db` under `_TEST_DATA_DIR`.

Trace:

- [`conftest.py:22`](../../../tests/conftest.py:22) sets `JOBPILOT_DATA_DIR=<tmp>` before any import.
- [`backend.database`](../../../backend/database.py) reads `settings.jobpilot_data_dir` at module import to construct `create_async_engine("sqlite+aiosqlite:///{data_dir}/jobpilot.db")`.
- Test fixtures call `await init_db()` (idempotent `Base.metadata.create_all`) at the top of each Gmail-related test.
- Rows from one test **persist into the next**. The DB is wiped exactly once: at `atexit` (process exit), via `shutil.rmtree(_TEST_DATA_DIR)`.

**Implications:**

- Tests must self-isolate by **unique key prefixes**. The Gmail tests do this religiously — every email address is prefixed with the file's purpose: `creds-…`, `auth-u1…`, `sync-u1…`, `corr-u…`, `sched-a…`, `ws-u…`, `oauth-…`, `smoke@…`. The unique constraint on `gmail_credentials.email_address` ([`backend/models/gmail.py`](../../../backend/models/gmail.py)) is what enforces collision detection. See §7.
- The one test that **does** need a clean world — `test_phase_1_happy_path` ([`test_gmail_smoke.py:36-50`](../../../tests/test_gmail_smoke.py:36)) — manually `DELETE`s `ApplicationCorrespondence`, `GmailMessage`, `GmailCredential` rows inside its fixture before it runs. The comment is candid: "*The test DB is shared across the whole pytest session. Other gmail-tests leave behind … rows that would shadow our connected account in /api/gmail/status*."
- [`test_daily_limit.py`](../../../tests/test_daily_limit.py) **opts out of the shared DB entirely** — it builds its own `tempfile.mkdtemp` + `create_async_engine` factory per fixture ([`test_daily_limit.py:34-56`](../../../tests/test_daily_limit.py:34)) because it needs concurrent writers against one DB to prove the TOCTOU fix.

**Parallel-safety**: zero. Running with `pytest-xdist -n auto` would corrupt this design — every worker would import `backend.database` and try to share the same file from different processes, but the test ordering assumptions would break and you'd get random unique-constraint violations. There's no per-test transaction wrapper and no `tmp_path`-per-worker isolation hook.

**The non-isolated DB is a deliberate trade-off**: tests run fast (no `Base.metadata.drop_all`/`create_all` between tests), but you pay in test-data hygiene and parallel impossibility.

---

## 5. Mocking patterns

The suite leans hard on `unittest.mock` — `from unittest.mock import AsyncMock, MagicMock, patch` appears in ~20 files. There is no `responses`, `respx`, `pytest-httpx`, or `httpx.MockTransport`. Each external dependency has its own ad-hoc shape:

### Gemini (`backend.llm.gemini_client.GeminiClient`)

- The most-mocked external. Three styles in use, all of which have problems (§10):
  1. **Construct without `__init__`**: `client = GeminiClient.__new__(GeminiClient)` then set `_call_times`, `_lock` manually ([`test_gemini_client.py:14-17`](../../../tests/test_gemini_client.py:14)). Used for testing the internal rate-limiter logic.
  2. **Patch `generate_text`**: `patch.object(client, "generate_text", new=AsyncMock(return_value=…))` ([`test_gemini_client.py:20`](../../../tests/test_gemini_client.py:20)).
  3. **Hand a `MagicMock` with `generate_json = AsyncMock(return_value=expected)`** in tests that consume the client through a higher-level class — e.g. [`test_cv_modifier.py:40-43`](../../../tests/test_cv_modifier.py:40), [`test_embedder.py:14-21`](../../../tests/test_embedder.py:14).

### browser-use (`browser_use.Agent`, `browser_use.Browser`)

- Patched at the **package** level — `patch("browser_use.Agent", return_value=fake_agent)` ([`test_error_handling.py:44`](../../../tests/test_error_handling.py:44)) — to dodge the local-import-time binding inside `auto_apply.py`.
- Tests also flip `_BROWSER_USE_AVAILABLE = False` at module level to take the fallback branch ([`test_apply_engine.py:95`](../../../tests/test_apply_engine.py:95)).

### httpx (Gmail OAuth, Gmail REST, Adzuna)

- Every test patches `httpx.AsyncClient` at the **module where it's imported**, not on the `httpx` namespace itself. Examples:
  - `patch("backend.api.gmail_auth.httpx.AsyncClient")` — [`test_gmail_oauth_routes.py:72`](../../../tests/test_gmail_oauth_routes.py:72)
  - `patch("backend.gmail.auth.httpx.AsyncClient")` — [`test_gmail_auth.py:38`](../../../tests/test_gmail_auth.py:38)
  - `patch("backend.scraping.adzuna_client.httpx.AsyncClient")` — [`test_adzuna_client.py:42`](../../../tests/test_adzuna_client.py:42)
- The mock instance is shaped as the **`__aenter__` return value**: `MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=fake_response)`. This is fragile and easy to get wrong, but there's no helper to abstract it.

### scrapling / playwright

- `scrapling` is **never mocked**. Tests that exercise `scrapling_fetcher.ScraplingFetcher` only call the pure helpers (`_build_search_url`, `_clean_html`, prompt assembly) via `ScraplingFetcher.__new__(ScraplingFetcher)` to bypass `__init__` ([`test_google_jobs_scraping.py:19-23`](../../../tests/test_google_jobs_scraping.py:19)).
- Real Playwright is **exercised only in [`test_windows_playwright.py`](../../../tests/test_windows_playwright.py)** which is a diagnostic file (§10).

### Gmail REST client (`backend.gmail.client.GmailRestClient`)

- Replaced with an in-file `_FakeClient` async-context-manager that implements `messages_list`, `history_list`, `messages_get`. Pattern is repeated in [`test_gmail_sync.py:40`](../../../tests/test_gmail_sync.py:40), [`test_gmail_ws.py:37`](../../../tests/test_gmail_ws.py:37), [`test_gmail_smoke.py:112`](../../../tests/test_gmail_smoke.py:112) — three independent copies of the same fake (factory absence; §10).

### WebSocket broadcaster

- Replaced with a `_FakeWS` class that captures `send_text` into a `sent: list` ([`test_ws_broadcaster.py:37`](../../../tests/test_ws_broadcaster.py:37)). Then **validates the wire format against `WSMessage` discriminated union via `TypeAdapter`** ([`test_ws_broadcaster.py:34`](../../../tests/test_ws_broadcaster.py:34)) — this is the strongest mock pattern in the repo because it pins both behaviour and serialisation contract.

### Database session

- For pure-engine tests: `db = AsyncMock(spec=AsyncSession)` then `db.execute.return_value.scalar_one_or_none = MagicMock(return_value=…)` ([`test_apply_engine.py:28-30`](../../../tests/test_apply_engine.py:28)). Brittle: the chain depends on internal call shape (§10).

---

## 6. Test inventory

Test counts are total `def test_…` declarations including class methods. "Subsystem" is my grouping by the backend module under test.

| Subsystem | File | Tests | What it covers |
|---|---|---|---|
| **smoke / health** | [`test_smoke.py`](../../../tests/test_smoke.py) | 4 | `/api/health`, `Settings()` loads, app starts, stub routes don't crash |
| | [`test_health.py`](../../../tests/test_health.py) | 6 | Real DB ping, 503 on DB outage, no exception leakage, ISO timestamp, HealthOut schema |
| **api / applications** | [`test_api_routes.py`](../../../tests/test_api_routes.py) | 24 | CRUD on `/api/applications`, `/api/documents`, `/api/settings`, `/api/analytics`; F-Q4 partial-PUT regression |
| | [`test_apply_http.py`](../../../tests/test_apply_http.py) | 6 | `POST /api/applications/{match_id}/apply` happy path + engine raise/missing/422 |
| | [`test_applications_export.py`](../../../tests/test_applications_export.py) | 3 | `/api/applications/export` CSV format & content |
| | [`test_limit_status.py`](../../../tests/test_limit_status.py) | 3 | `/api/applications/limit-status` fresh / mid-day / at-cap |
| | [`test_settings_cv_upload.py`](../../../tests/test_settings_cv_upload.py) | 13 | `POST /api/settings/profile/cv-upload` — happy, extensions, size, dedup, replacing |
| | [`test_today.py`](../../../tests/test_today.py) | 5 | `/api/today` shape: `new_matches`, `blocked_actions`, `week_stats` |
| **api / jobs** | [`test_api_jobs.py`](../../../tests/test_api_jobs.py) | 9 | `/api/jobs`, `/api/queue` listing, search 422, skip 404, status updates |
| **apply engine** | [`test_apply_engine.py`](../../../tests/test_apply_engine.py) | 24 | `DailyLimitGuard`, `ManualApplyStrategy`, `AssistedApplyStrategy`, `AutoApplyStrategy` Tier-1/Tier-2 routing, cancel/confirm signals, `_resolve_documents` |
| | [`test_apply_state.py`](../../../tests/test_apply_state.py) | 16 | FSM driver (`Statechart`), every transition edge, terminals, error paths, FAILED fallback |
| | [`test_daily_limit.py`](../../../tests/test_daily_limit.py) | 3 | TOCTOU race on `DailyLimitGuard.reserve_slot` using a real concurrent SQLite |
| | [`test_form_filler.py`](../../../tests/test_form_filler.py) | 12 | `_clean_form_html`, `_build_fill_prompt`, file-upload mention, additional-answers JSON formatting |
| | [`test_follow_up.py`](../../../tests/test_follow_up.py) | 5 | Reminder scanner — empty DB, 3d/8d cutoffs, idempotency, suppression after manual event |
| **scraping** | [`test_scraping.py`](../../../tests/test_scraping.py) | 13 | `_extract_json_from_text`, `AdaptiveScraper._parse_agent_result`, `ScrapingOrchestrator.scrape_batch` (mocked Adzuna+Adaptive) |
| | [`test_adzuna_client.py`](../../../tests/test_adzuna_client.py) | 3 | HTTP 200 path, 401 raises `AdzunaAPIError`, empty results |
| | [`test_deduplicator.py`](../../../tests/test_deduplicator.py) | 2 | URL-based deduplication of `RawJob` |
| | [`test_session_manager.py`](../../../tests/test_session_manager.py) | 11 | `BrowserSessionManager` — list/clear/get_or_create across legacy + canonical layouts |
| | [`test_google_jobs_scraping.py`](../../../tests/test_google_jobs_scraping.py) | 33 | URL builders, selectors, prompts, HTML cleaning for Google Jobs / LinkedIn / Indeed / WTTJ |
| | [`test_config_scraper_headless.py`](../../../tests/test_config_scraper_headless.py) | 2 | `SCRAPER_HEADLESS` default + env override |
| **LLM** | [`test_gemini_client.py`](../../../tests/test_gemini_client.py) | 6 | `generate_json` happy/invalid, rate-limiter, lock-around-sleep regression, `embed()` |
| | [`test_cv_modifier.py`](../../../tests/test_cv_modifier.py) | 4 | `CVModifier.modify` returns shape, caps at three, error propagation, `modify_from_assessment` |
| | [`test_job_analyzer.py`](../../../tests/test_job_analyzer.py) | 3 | `JobAnalyzer.analyze` with mocked Gemini |
| | [`test_job_context.py`](../../../tests/test_job_context.py) | 5 | `JobContext` schema validation + roundtrip |
| | [`test_prompts.py`](../../../tests/test_prompts.py) | 5 | **Prefix-cache eligibility** — common prefix between two prompts must exceed 4500 chars |
| **matching** | [`test_matcher.py`](../../../tests/test_matcher.py) | 4 | `JobMatcher.score` + `rank_and_filter`, excluded keywords/companies |
| | [`test_fit_engine.py`](../../../tests/test_fit_engine.py) | 9 | Gap-severity algorithm, cosine, perfect/complete gap, modification decision |
| | [`test_fit_integration.py`](../../../tests/test_fit_integration.py) | 4 | `FitEngine` + `Embedder` + extractor end-to-end with mocked embeddings |
| | [`test_fit_models.py`](../../../tests/test_fit_models.py) | 2 | Pydantic models for fit assessment |
| | [`test_cv_parser.py`](../../../tests/test_cv_parser.py) | 6 | CV → `CVProfile` with skill weighting by section |
| | [`test_job_skill_extractor.py`](../../../tests/test_job_skill_extractor.py) | 6 | Job → `JobProfile` extraction with mocked LLM |
| | [`test_skill_patterns.py`](../../../tests/test_skill_patterns.py) | 6 | Skill alias canonicalisation rules |
| | [`test_embedder.py`](../../../tests/test_embedder.py) | 4 | `Embedder.embed_cv_profile/embed_job_profile`, skip-already-embedded, empty input |
| **LaTeX** | [`test_latex_parser.py`](../../../tests/test_latex_parser.py) | 5 | JOBPILOT marker parsing into `CVSection`s |
| | [`test_latex_pipeline.py`](../../../tests/test_latex_pipeline.py) | 6 | `CVPipeline.generate_tailored_cv` — skips when Tectonic missing |
| | [`test_cv_applicator.py`](../../../tests/test_cv_applicator.py) | 7 | Inject replacements into TeX, skip-when-original-missing |
| | [`test_cv_parser.py`](../../../tests/test_cv_parser.py) | (counted above) | |
| **gmail** (Phase 1; §7) | [`test_gmail_credentials.py`](../../../tests/test_gmail_credentials.py) | 4 | Fernet roundtrip, save/load, upsert, delete |
| | [`test_gmail_auth.py`](../../../tests/test_gmail_auth.py) | 3 | Token refresh, in-memory cache, expiry path, KeyError on missing credential |
| | [`test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py) | 4 | Heuristic classifier — 11 parametrised patterns, vendor extraction, rejection beats ATS |
| | [`test_gmail_models.py`](../../../tests/test_gmail_models.py) | 4 | Tables created, `last_correspondence_at` column added, unique constraint enforced |
| | [`test_gmail_sync.py`](../../../tests/test_gmail_sync.py) | 4 | First-run backfill, second-run history-list delta, IntegrityError dedup, disabled-cred skip |
| | [`test_gmail_oauth_routes.py`](../../../tests/test_gmail_oauth_routes.py) | 5 | OAuth start/callback/disconnect; state forgery rejected; 503 when unconfigured |
| | [`test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) | 2 | APScheduler iterates all enabled creds, skips disabled |
| | [`test_gmail_ws.py`](../../../tests/test_gmail_ws.py) | 2 | `broadcast_gmail_message_received` and `_sync_status` emit on new rows; WS union includes Gmail variants |
| | [`test_correspondence_api.py`](../../../tests/test_correspondence_api.py) | 5 | `/api/correspondence/unlinked`, `/link`, `/{app_id}`, `DELETE`, `/api/gmail/status` |
| | [`test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) | 1 | **End-to-end Phase-1 happy path** — see §8 |
| **WebSocket** | [`test_websocket.py`](../../../tests/test_websocket.py) | 4 | Connect/disconnect, ping-pong, unknown-message resilience, broadcast helper |
| | [`test_ws_broadcaster.py`](../../../tests/test_ws_broadcaster.py) | 6 | Every helper's wire payload validates against `WSMessage` discriminated union |
| **scheduler** | [`test_batch_runner.py`](../../../tests/test_batch_runner.py) | 4 | Full batch happy path, daily-limit pause, daily-limit-exceeded broadcast, no-CV path |
| | [`test_batch_runner_cv_fallback.py`](../../../tests/test_batch_runner_cv_fallback.py) | 5 | `base_cv_path` resolution from profile / templates / fallback |
| **security** | [`test_sanitizer.py`](../../../tests/test_sanitizer.py) | 27 | Truncation, control chars, prompt-injection patterns (ignore/disregard/you-are-now/`<\|im_start\|>`), `sanitize_url`, `wrap_untrusted` |
| **misc** | [`test_defaults.py`](../../../tests/test_defaults.py) | 4 | `backend/defaults.py` invariants (thresholds ordered, etc.) |
| | [`test_logging.py`](../../../tests/test_logging.py) | 4 | JSON rotating file handler, lowercase level strings, idempotency, exc_info serialisation |
| | [`test_error_handling.py`](../../../tests/test_error_handling.py) | 7 | Scraper retry, CV pipeline fallback, Gemini JSON retry, global exception handler |
| **diagnostic** | [`test_windows_playwright.py`](../../../tests/test_windows_playwright.py) | 13 | Stage-1-through-5 Playwright/browser-use deadlock troubleshooting (§10) |
| **integration** | [`integration/test_full_pipeline.py`](../../../tests/integration/test_full_pipeline.py) | 10 | Adzuna→queue flow, CV tailoring with mocked Gemini, manual apply lifecycle, settings persistence, analytics |

Totals: ~470 tests across 55 files. Gmail subsystem alone contributes 44 tests across 10 files.

---

## 7. Recently-added Gmail tests

The most recent commits (`gm-6` through `gm-12`) added 10 Gmail-focused test files totalling 44 tests. They establish patterns the rest of the suite did not have:

### 7.1 Email-prefix isolation

Because the test DB is session-scoped (§4), every Gmail test seeds credentials with a **file-specific email prefix** so the unique constraint on `gmail_credentials.email_address` can never collide:

| File | Prefix | Examples |
|---|---|---|
| [`test_gmail_credentials.py`](../../../tests/test_gmail_credentials.py) | `creds-` | `creds-user@example.com`, `creds-u@e.com` |
| [`test_gmail_auth.py`](../../../tests/test_gmail_auth.py) | `auth-u<N>` | `auth-u1@e.com`, `auth-u2@e.com` |
| [`test_gmail_sync.py`](../../../tests/test_gmail_sync.py) | `sync-u<N>` | `sync-u1@e.com`, `sync-u2@e.com`, `sync-u3@e.com`, `sync-u4@e.com` |
| [`test_correspondence_api.py`](../../../tests/test_correspondence_api.py) | `corr-` | `corr-u@e.com`, `corr-m1`, `corr-m-noise` |
| [`test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) | `sched-<a/b/c>` | `sched-a@e.com`, `sched-b@e.com` |
| [`test_gmail_ws.py`](../../../tests/test_gmail_ws.py) | `ws-` | `ws-u@e.com` |
| [`test_gmail_oauth_routes.py`](../../../tests/test_gmail_oauth_routes.py) | `oauth-` | `oauth-user@example.com`, `oauth-disconnect@e.com` |
| [`test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) | `smoke@` | `smoke@example.com` |

Gmail **message IDs** are prefixed the same way (`m-sync-1`, `m-ws-1`, `m-smoke`, `corr-m1`). This is the cleanest workaround for the shared-DB design and the new code should keep it.

### 7.2 `app_with_gmail` per-test setting rebind

Both [`test_gmail_oauth_routes.py:11`](../../../tests/test_gmail_oauth_routes.py:11) and [`test_gmail_smoke.py:19`](../../../tests/test_gmail_smoke.py:19) have to do:

```python
monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client.apps.googleusercontent.com")
monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-secret")
import backend.config as cfg
cfg.settings = cfg._load_settings()
import backend.gmail.auth as _gmail_auth
monkeypatch.setattr(_gmail_auth, "settings", cfg.settings)
```

…because `backend.gmail.auth` does `from backend.config import settings` at import time, which pins the (empty) settings instance from when the *previous* test imported it. This is a smell that traces back to a production module pattern, not a test-only quirk.

### 7.3 Patching `GmailRestClient` per-call

Every sync-related test goes:

```python
with patch("backend.gmail.sync.GmailRestClient", return_value=_Fake()): …
```

where `_Fake` is a local class implementing `__aenter__`, `__aexit__`, `messages_list`, `history_list`, `messages_get`. This is **duplicated three times** in nearly-identical form across [`test_gmail_sync.py:40`](../../../tests/test_gmail_sync.py:40), [`test_gmail_ws.py:37`](../../../tests/test_gmail_ws.py:37), [`test_gmail_smoke.py:112`](../../../tests/test_gmail_smoke.py:112) — see §10 critique.

### 7.4 Heuristic classifier parametrisation

[`test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py) is the only `@pytest.mark.parametrize`-heavy file in the suite — 11 (from, subject, category, vendor) tuples for the heuristic ATS/rejection/interview/offer/noise/unknown decision tree.

---

## 8. End-to-end / smoke tests

There are **two** files calling themselves "smoke" and one set of integration tests; their scope differs sharply.

### 8.1 [`test_smoke.py`](../../../tests/test_smoke.py)

Four trivial pings: `/api/health` returns `status: ok`, `Settings()` loads, `TestClient(app)` doesn't raise, three stub routes return 200/404. This is **liveness, not smoke** — no business logic exercised. Useful as a CI sanity check on machines that haven't installed `tectonic` / browser-use.

### 8.2 [`test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py)

The only true end-to-end happy-path test in the suite. **One test function** ([`test_phase_1_happy_path`](../../../tests/test_gmail_smoke.py:57)) that drives:

1. **Seed** a manual `Application` row.
2. **OAuth start** — hits `/api/gmail/oauth/start`, parses `state` out of the redirect URL.
3. **OAuth callback** — patches `backend.api.gmail_auth.httpx.AsyncClient` to return a fake token + profile, hits `/api/gmail/oauth/callback?code=auth-code&state=…`, asserts a 302/303 to `/settings`.
4. **Sync trigger** — patches both `backend.gmail.sync.GmailRestClient` and `backend.gmail.auth.httpx.AsyncClient`, hits `POST /api/gmail/sync`, asserts `synced == 1`.
5. **List unlinked** — hits `/api/correspondence/unlinked`, asserts the new message is in the list.
6. **Link** — hits `POST /api/correspondence/link`, asserts 201.
7. **Application detail** — hits `/api/correspondence/{app_id}`, asserts the message thread contains the linked message.
8. **Gmail status** — hits `/api/gmail/status`, asserts `connected: true`, `email_address: smoke@example.com`, `message_count >= 1`, `history_id is not None`.

This is excellent — it pins the entire Phase-1 user journey end to end with all external HTTP mocked at the boundary. The DB-wipe in its own fixture ([`test_gmail_smoke.py:36-50`](../../../tests/test_gmail_smoke.py:36)) is necessary precisely because of the shared-DB design (§4).

### 8.3 [`integration/test_full_pipeline.py`](../../../tests/integration/test_full_pipeline.py)

Ten tests that each exercise one "lane" (job search, CV tailoring, manual apply, settings, health, analytics) but **none chain all the way through**. Closer to thick component tests than true integration — there's no scrape→match→apply→follow-up walk.

---

## 9. Coverage gaps

The repo ships a `.coverage` cache but no human-readable summary. From the test inventory vs. the [`backend/`](../../../backend/) module tree, here are likely untested-or-thinly-tested areas:

1. **[`backend/applier/captcha_handler.py`](../../../backend/applier/captcha_handler.py)** (10.9 KB) — no `test_captcha_handler.py`. CAPTCHA-detection WS broadcast is touched only in [`test_ws_broadcaster.py`](../../../tests/test_ws_broadcaster.py).
2. **[`backend/applier/recorder.py`](../../../backend/applier/recorder.py)** (6.0 KB, application-event recorder) — no dedicated test file.
3. **[`backend/api/applications.py`](../../../backend/api/applications.py)** internals — 22 KB module is exercised via `test_api_routes.py` and `test_apply_http.py`, but the document-resolution helper and the CV/letter PDF streaming paths are tested only happy-path 404.
4. **[`backend/api/queue.py`](../../../backend/api/queue.py)** (9.6 KB) — only listing + skip-404 are covered ([`test_api_jobs.py`](../../../tests/test_api_jobs.py)). Refresh, status transitions, and confirm-from-queue flows are untested.
5. **[`backend/api/documents.py`](../../../backend/api/documents.py)** (6.6 KB) — only 404 paths tested; regeneration body, diff rendering, PDF streaming on existing match are untested.
6. **[`backend/scraping/scrapling_fetcher.py`](../../../backend/scraping/scrapling_fetcher.py)** (15.8 KB) — pure helpers are covered in [`test_google_jobs_scraping.py`](../../../tests/test_google_jobs_scraping.py); the actual `fetch()` async path against a mock `StealthyFetcher` is not.
7. **[`backend/scraping/orchestrator.py`](../../../backend/scraping/orchestrator.py)** (17.2 KB) — only `scrape_batch` is exercised in [`test_scraping.py`](../../../tests/test_scraping.py). Other entry points (per-site dispatch, prompt-template selection) appear untested.
8. **[`backend/llm/cv_editor.py`](../../../backend/llm/cv_editor.py)** (3.1 KB) — no `test_cv_editor.py`.
9. **[`backend/llm/job_analyzer.py`](../../../backend/llm/job_analyzer.py)** — only 3 tests in [`test_job_analyzer.py`](../../../tests/test_job_analyzer.py); error/fallback branches probably uncovered.
10. **[`backend/latex/compiler.py`](../../../backend/latex/compiler.py)** + **[`validator.py`](../../../backend/latex/validator.py)** — pipeline tests skip when Tectonic missing ([`test_latex_pipeline.py:57`](../../../tests/test_latex_pipeline.py:57)). On most CI machines these tests are no-ops.
11. **[`backend/utils/browser_path.py`](../../../backend/utils/browser_path.py)** — no dedicated test file; exercised only indirectly via Playwright tests.
12. **[`backend/api/today.py`](../../../backend/api/today.py)** (10 KB) — covered only by empty-DB shape assertions in [`test_today.py`](../../../tests/test_today.py); populated-DB branching (`high_confidence`/`worth_reviewing`/`skipped` bucket boundaries) untested.
13. **[`backend/main.py`](../../../backend/main.py:1)** (17.9 KB) — only the scheduler hook and lifecycle through `TestClient` are exercised; startup-error paths, shutdown cleanup, and the global exception handler's non-trivial branches mostly untested.
14. **APScheduler error paths** — [`test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) covers happy-path iteration and disabled-skip. A `sync_now` raising for one account does not have a test that asserts the loop continues for the others.
15. **`GmailMessageBody` extraction** — `messages_get` is mocked everywhere; the multipart-MIME / quoted-printable / base64-decoding helpers in `backend/gmail/sync.py` are likely untested.

These should be the next 12-15 issues if the team chases coverage.

---

## 10. Critique

### **HIGH** — Tests that mock the thing they're meant to test

- **[`test_apply_engine.py:139-159`](../../../tests/test_apply_engine.py:139)** — `test_engine_manual_apply_records_application` asserts `db.commit.assert_called()`, but `db` is an `AsyncMock(spec=AsyncSession)` and the entire row insertion is `db.add(MagicMock())`. There is no statement that the engine actually persisted a row with the right shape — only that *some* `add()` happened and *some* `commit()` happened. The test would pass if the engine called `db.add(None); await db.commit()`. The fix is to use the real `aiosqlite` engine, which the suite already wires up — but the test stayed on the mock-AsyncSession path for speed.
- **[`test_apply_engine.py:243-272`](../../../tests/test_apply_engine.py:243)** — `test_engine_cancel_apply_returns_cancelled` patches `engine._auto.apply` to a function that just returns `cancelled` — i.e. the test mocks the strategy *and* then asserts the strategy was cancelled. It proves the engine returns whatever the strategy returns, not that cancellation logic works.
- **[`test_gemini_client.py:39-66`](../../../tests/test_gemini_client.py:39)** — `test_rate_limiter_tracks_calls` patches `asyncio.sleep` and asserts `len(sleep_calls) == 1`. It does prove the rate limiter computed a sleep value, but it does not prove the sleep value would actually space out concurrent callers in production.

### **HIGH** — `AsyncMock(spec=AsyncSession)` chain-chasing is brittle

The pattern `db.execute.return_value.scalar_one_or_none = MagicMock(return_value=N)` ([`test_apply_engine.py:30`](../../../tests/test_apply_engine.py:30), [`test_apply_state.py:46`](../../../tests/test_apply_state.py:46)) is repeated **dozens** of times. It pins the test to the **exact call shape** the SUT uses today — change a `scalar_one_or_none` to `scalar_one` or insert a `.scalars()` and the test silently misbehaves (the mock attribute access just returns a MagicMock instead of raising AttributeError because `spec=AsyncSession` covers `execute` but not the deeply nested chain).

There is no `AsyncSession` fake helper; every test builds its own.

### **HIGH** — Test data factories are absent

`Application`, `JobMatch`, `Job`, `GmailMessage`, `RawJob`, `JobDetails`, `JobContext`, `CVProfile`, `JobProfile`, `FitAssessment` are constructed inline in every test that needs one. Examples:

- [`test_correspondence_api.py:24-43`](../../../tests/test_correspondence_api.py:24): `_seed_app()` and `_seed_msg(mid, category)` helpers, redefined in every Gmail test file.
- [`test_matcher.py:9-27`](../../../tests/test_matcher.py:9): `_make_job(...)` helper, redefined.
- [`test_scraping.py:147-152`](../../../tests/test_scraping.py:147): `_make_raw_job` and `_make_filters` helpers, redefined.
- [`test_fit_engine.py:31-46`](../../../tests/test_fit_engine.py:31): inline construction of `CVProfile` + `SkillEntry` + `JobProfile` + `JobSkill` with hand-crafted embeddings.

A `tests/factories.py` with `make_application(**overrides)`, `make_gmail_message(**overrides)`, `make_job_match(**overrides)` would eliminate hundreds of lines of duplicated construction and remove a class of "I forgot to set required field X" failures.

### **HIGH** — `_FakeClient` Gmail-REST mock is duplicated three times

[`test_gmail_sync.py:40-63`](../../../tests/test_gmail_sync.py:40), [`test_gmail_ws.py:37-43`](../../../tests/test_gmail_ws.py:37), [`test_gmail_smoke.py:112-118`](../../../tests/test_gmail_smoke.py:112) each declare an almost-identical async-context-manager fake for `GmailRestClient`. If the production interface adds a `threads_list` or a `users_history_list` arg, three test files drift independently. Lift it into `tests/fakes/gmail.py`.

### **HIGH** — Lack of frontend tests entirely

The SvelteKit frontend has **zero** test files:

- [`frontend/package.json`](../../../frontend/package.json) lists no `vitest`, `@testing-library/svelte`, `playwright`, or `jest`. Only `svelte-check` (type-check, not behavioural).
- No `*.test.ts` or `*.spec.ts` anywhere under [`frontend/src/`](../../../frontend/src/).
- The Gmail UI, the Today dashboard, the application list, the OAuth-connect flow, the WebSocket subscription — none have any test.

The whole user-visible layer is verified manually. Given the WebSocket discriminated-union contract is already enforced in [`test_ws_broadcaster.py`](../../../tests/test_ws_broadcaster.py) on the server side, a tiny set of `@testing-library/svelte` tests against the WS-store would close the loop cheaply.

### **MED** — `asyncio_mode = "auto"`: what it gives, what it costs

**Gives**: every `async def test_…` runs without a decorator; cleaner code.

**Costs**:
- Hides the marker from grep — you can't easily find async vs. sync tests.
- Mixing `asyncio.run(...)` inside an async test (which the smoke suite does, e.g. [`test_correspondence_api.py:47`](../../../tests/test_correspondence_api.py:47)) creates a nested-loop risk — `TestClient` already drives the lifespan loop, then test code calls `asyncio.run()` on top. It works because `TestClient` runs on a separate thread, but it's a footgun. A `db_session` fixture that yields an `AsyncSession` would let the test write `await db_session.execute(...)` instead of `asyncio.run(_seed_app())`.
- `@pytest.mark.asyncio` still gets used on ~50% of async tests anyway — the conventions are mixed.

### **MED** — Slow tests + no parallel suite

Total runtime is bounded by `init_db()` overhead per Gmail test (creating tables in SQLite each run) plus all the LLM/browser-use mocks being set up via `patch(...)` rather than constructor injection. The suite has ~470 tests; an `-n auto` run is **not possible** because of the shared session-scoped DB. Either move to a per-test-database engine fixture (slow per test but parallelisable) or keep the shared DB but mark and isolate the few tests that need a clean world.

### **MED** — Brittle string-match assertions

- [`test_apply_engine.py:213`](../../../tests/test_apply_engine.py:213): `assert "limit" in result.message.lower()` — passes whether the message is "daily limit exceeded" or "no limit on that".
- [`test_apply_engine.py:82`](../../../tests/test_apply_engine.py:82): `assert "Could not open browser" in result.message` — brittle if the message gets i18n'd or rephrased.
- [`test_today.py:58`](../../../tests/test_today.py:58): `assert "Gmail" in ws["response_rate"]` — asserts on a placeholder string. If product copy changes, this breaks.
- [`test_form_filler.py`](../../../tests/test_form_filler.py) makes assertions like `"cv" in prompt.lower() or "resume" in prompt.lower()` — the disjunction admits both correct and "almost correct" prompts.

### **MED** — Fixtures that swallow errors / no DB cleanup

- The `app_with_gmail` fixture in [`test_gmail_smoke.py:36-50`](../../../tests/test_gmail_smoke.py:36) wraps the DELETE inside `asyncio.run(_wipe())` — if `_wipe()` raises (e.g. tables don't exist on a fresh run), the test fails with a confusing async error before the smoke flow even starts. It does call `await _init()` inside to mitigate (the gm-12 fixup commit), but the pattern is fragile.
- [`test_logging.py:36-52`](../../../tests/test_logging.py:36) snapshots and restores root logger handlers in a `try/finally`, with `try: h.close() except: pass` swallowing close errors silently. The pattern is correct, but a misbehaving handler will hide its failure.
- No fixture clears DB state on teardown. Every test that creates rows lives forever until process exit.

### **MED** — `test_windows_playwright.py` — what is this and is it run?

[`test_windows_playwright.py`](../../../tests/test_windows_playwright.py) is a **manual diagnostic suite**, not a regression test:

- The file docstring says: "*Run on the Windows machine with: `uv run pytest tests/test_windows_playwright.py -v -s`. Each test is independent and prints diagnostic info … run them in order and stop at the first failure.*"
- It's numbered `test_01_python_version`, `test_02_event_loop_policy`, …, `test_12_adaptive_scraper_storage_path` — pytest ordering is alphabetical so this works incidentally.
- It calls `pytest.fail("Run: uv run playwright install chromium")` with installation instructions on missing binaries — i.e. the failure messages are user docs.
- On Linux CI it will: pass tests 1, 3, 4, 5, 6; skip test 2 (Windows-only); attempt tests 7-9 (browser-use) which **launch a real Chromium** if installed; test 10 (storage_state path) passes; test 11 does another Chromium launch.

So on Linux dev machines, **this file launches a real headless Chromium twice on every test run**, adding ~5-10 s. There is no `@pytest.mark.diagnostic` to exclude it. The cleanest fix is `@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only diagnostic")` on the whole module, or move it to `scripts/diagnostics/windows_playwright.py` and out of the pytest path entirely.

### **MED** — Pyright violations in test code masked by config

[`pyrightconfig.json`](../../../pyrightconfig.json) is `typeCheckingMode: "basic"` with `tests` included. In basic mode, pyright tolerates:

- `mgr.active_connections["c1"] = fake  # type: ignore[assignment]` ([`test_ws_broadcaster.py:58`](../../../tests/test_ws_broadcaster.py:58)) — these `# type: ignore` are everywhere in tests.
- `client = GeminiClient.__new__(GeminiClient)` + manual attribute assignment ([`test_gemini_client.py:14`](../../../tests/test_gemini_client.py:14)) bypasses `__init__` typing entirely.
- `db: object = None` then `mock_db = AsyncMock()` with `# type: ignore[arg-type]` ([`test_apply_state.py:35`](../../../tests/test_apply_state.py:35)).

This is fine in basic mode but if the team ever wants to flip to `strict`, the test directory will explode. The pragmatic answer is: keep `basic`, add a `# pyright: basic` marker per test file, and write a real `AsyncSession` fake instead of `# type: ignore`-ing everywhere.

### **LOW** — No property/fuzz tests for the classifier or matcher

- The Gmail classifier ([`backend/gmail/classifier_heuristics.py`](../../../backend/gmail/classifier_heuristics.py)) is exercised with 11 hand-picked (from, subject) tuples. The same tests would fly through a tiny [`hypothesis`](https://hypothesis.readthedocs.io/) strategy generating random subjects to assert:
  - Subjects containing "unfortunately" with random surrounding noise → always classified as `rejection`.
  - Sender domain `@greenhouse.io` with arbitrary subject → vendor always `greenhouse`.
- The matcher ([`backend/matching/matcher.py`](../../../backend/matching/matcher.py)) — `test_matcher.py` has 4 hand-picked cases. Property-style: for any `JobFilters`, "containing an excluded keyword → score 0" is invariant. Same for blacklisted company. Hypothesis would find boundary cases (case sensitivity, partial-word matches) that the current tests don't cover.

The cost of adding `hypothesis` is one dev dep and one decorator per test. The payoff for pure-function components like the classifier and matcher is large.

### **LOW** — Missing markers + missing CI gates

- No `slow`, `network`, `windows`, `requires_tectonic` markers — the suite either runs everything or nothing.
- No `--cov-fail-under` in `addopts`. Coverage is captured but never enforced. The `.coverage` blob in the repo root is a stale artifact, not a CI output.
- `pytest.ini_options` has no `filterwarnings = […]`, so DeprecationWarnings from `datetime.utcnow()` (used in [`test_daily_limit.py:94`](../../../tests/test_daily_limit.py:94)), pydantic deprecation warnings, etc. accumulate as silent noise.

### **LOW** — `test_smoke.py` is misnamed

[`test_smoke.py`](../../../tests/test_smoke.py) doesn't smoke anything — it's a 4-test liveness check. Rename to `test_liveness.py` and make `test_smoke.py` the home of one or two end-to-end happy-paths (Gmail already has its own).

---

## 11. Inventory — one-line description per test file

| File | What it tests in one line |
|---|---|
| [`tests/conftest.py`](../../../tests/conftest.py) | Session-scoped tmp `JOBPILOT_DATA_DIR`, dummy env vars, `test_app` and `test_settings` fixtures. |
| [`tests/fixtures/sample_cv.tex`](../../../tests/fixtures/sample_cv.tex) | Sample CV LaTeX used by latex / cv_parser tests. |
| [`tests/integration/test_full_pipeline.py`](../../../tests/integration/test_full_pipeline.py) | Multi-layer integration — job search → queue, CV tailoring with mocked Gemini, manual apply, settings, analytics, health. |
| [`tests/test_adzuna_client.py`](../../../tests/test_adzuna_client.py) | `AdzunaClient.search` 200 / 401-raises / empty-results against a mocked `httpx.AsyncClient`. |
| [`tests/test_api_jobs.py`](../../../tests/test_api_jobs.py) | `/api/jobs`, `/api/queue`, `/api/jobs/search` happy + 404 + 422. |
| [`tests/test_api_routes.py`](../../../tests/test_api_routes.py) | `/api/applications` CRUD, `/api/documents` 404s, `/api/settings/profile|search`, `/api/analytics/{summary,trends}`, F-Q4 partial-PUT regression. |
| [`tests/test_applications_export.py`](../../../tests/test_applications_export.py) | `/api/applications/export` CSV format & headers. |
| [`tests/test_apply_engine.py`](../../../tests/test_apply_engine.py) | `DailyLimitGuard`, `Manual/Assisted/AutoApplyStrategy` Tier-1/Tier-2 routing, signal events, browser-use fallbacks. |
| [`tests/test_apply_http.py`](../../../tests/test_apply_http.py) | `POST /api/applications/{match_id}/apply` happy / unknown-match / engine-raises / 422 / 503. |
| [`tests/test_apply_state.py`](../../../tests/test_apply_state.py) | Apply-flow FSM driver — every transition edge, terminals, compensation, error paths. |
| [`tests/test_batch_runner_cv_fallback.py`](../../../tests/test_batch_runner_cv_fallback.py) | `base_cv_path` resolution from profile / templates / fallback alphabetical-first. |
| [`tests/test_batch_runner.py`](../../../tests/test_batch_runner.py) | `BatchRunner.run_batch` — scrape→match→store→CV-generate, daily-limit pause, no-CV path. |
| [`tests/test_config_scraper_headless.py`](../../../tests/test_config_scraper_headless.py) | `SCRAPER_HEADLESS` config default + env override. |
| [`tests/test_correspondence_api.py`](../../../tests/test_correspondence_api.py) | `/api/correspondence/{unlinked,link,{app_id}}` + `DELETE` + `/api/gmail/status`. |
| [`tests/test_cv_applicator.py`](../../../tests/test_cv_applicator.py) | `CVApplicator.apply` — replacement injection into TeX, skip-when-original-missing. |
| [`tests/test_cv_modifier.py`](../../../tests/test_cv_modifier.py) | `CVModifier.modify` + `modify_from_assessment` with mocked Gemini. |
| [`tests/test_cv_parser.py`](../../../tests/test_cv_parser.py) | TeX → `CVProfile` with skill weighting by section. |
| [`tests/test_daily_limit.py`](../../../tests/test_daily_limit.py) | `DailyLimitGuard.reserve_slot` TOCTOU race regression against a real concurrent SQLite. |
| [`tests/test_deduplicator.py`](../../../tests/test_deduplicator.py) | `JobDeduplicator.deduplicate` by URL. |
| [`tests/test_defaults.py`](../../../tests/test_defaults.py) | Invariants on `backend/defaults.py` thresholds (ordering, embedding model). |
| [`tests/test_embedder.py`](../../../tests/test_embedder.py) | `Embedder.embed_cv_profile/embed_job_profile` with mocked Gemini, skip-already-embedded, empty input. |
| [`tests/test_error_handling.py`](../../../tests/test_error_handling.py) | Scraper retry + graceful degradation, CV-pipeline fallback when Gemini fails, JSON retry, global exception handler. |
| [`tests/test_fit_engine.py`](../../../tests/test_fit_engine.py) | Gap-severity algorithm, cosine helper, perfect-fit / complete-gap, modification decision. |
| [`tests/test_fit_integration.py`](../../../tests/test_fit_integration.py) | `FitEngine` + `Embedder` + extractor end-to-end with mocked embeddings. |
| [`tests/test_fit_models.py`](../../../tests/test_fit_models.py) | Pydantic models for fit assessment. |
| [`tests/test_follow_up.py`](../../../tests/test_follow_up.py) | Follow-up reminder scanner — empty / 3d / 8d / idempotent re-run / suppressed by manual event. |
| [`tests/test_form_filler.py`](../../../tests/test_form_filler.py) | `PlaywrightFormFiller._clean_form_html` + `_build_fill_prompt` pure helpers. |
| [`tests/test_gemini_client.py`](../../../tests/test_gemini_client.py) | `GeminiClient.generate_json`, rate-limiter, lock-around-sleep regression, `embed()`. |
| [`tests/test_gmail_auth.py`](../../../tests/test_gmail_auth.py) | `GmailTokenManager.access_token` — refresh, cache hit, expiry, missing-cred KeyError. |
| [`tests/test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py) | Heuristic ATS/rejection/interview/offer/noise/unknown classifier with 11 parametrised patterns. |
| [`tests/test_gmail_credentials.py`](../../../tests/test_gmail_credentials.py) | Fernet roundtrip, save/load/upsert/delete of `GmailCredential`. |
| [`tests/test_gmail_models.py`](../../../tests/test_gmail_models.py) | Tables created, `last_correspondence_at` column added, `gmail_message_id` unique constraint. |
| [`tests/test_gmail_oauth_routes.py`](../../../tests/test_gmail_oauth_routes.py) | `/api/gmail/oauth/{start,callback}` redirect / state-forgery rejected / `/api/gmail/disconnect`. |
| [`tests/test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) | APScheduler `_run_gmail_poll` iterates all enabled creds; skips disabled. |
| [`tests/test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) | **End-to-end Phase-1**: OAuth callback → sync → unlinked → link → application detail → status. |
| [`tests/test_gmail_sync.py`](../../../tests/test_gmail_sync.py) | `GmailSyncWorker.sync_now` — first-run backfill, history-list delta, IntegrityError dedup, disabled-cred skip. |
| [`tests/test_gmail_ws.py`](../../../tests/test_gmail_ws.py) | `broadcast_gmail_message_received/_sync_status` emit on new rows; `WSMessage` union includes Gmail variants. |
| [`tests/test_google_jobs_scraping.py`](../../../tests/test_google_jobs_scraping.py) | URL builders, selectors, prompt assembly, HTML cleaning for Google Jobs / LinkedIn / Indeed / WTTJ. |
| [`tests/test_health.py`](../../../tests/test_health.py) | `/api/health` real DB ping, 503 on outage, no-exception-leak, ISO timestamp, HealthOut schema. |
| [`tests/test_job_analyzer.py`](../../../tests/test_job_analyzer.py) | `JobAnalyzer.analyze` with mocked Gemini. |
| [`tests/test_job_context.py`](../../../tests/test_job_context.py) | `JobContext` Pydantic schema validation + roundtrip. |
| [`tests/test_job_skill_extractor.py`](../../../tests/test_job_skill_extractor.py) | Job → `JobProfile` skill extraction with mocked LLM. |
| [`tests/test_latex_parser.py`](../../../tests/test_latex_parser.py) | JOBPILOT marker parsing into `CVSection`s. |
| [`tests/test_latex_pipeline.py`](../../../tests/test_latex_pipeline.py) | `CVPipeline.generate_tailored_cv` end-to-end — skips when Tectonic missing. |
| [`tests/test_limit_status.py`](../../../tests/test_limit_status.py) | `/api/applications/limit-status` fresh / mid-day / at-cap. |
| [`tests/test_logging.py`](../../../tests/test_logging.py) | JSON rotating file handler, lowercase level strings, `configure_logging` idempotent, exc_info serialised as string. |
| [`tests/test_matcher.py`](../../../tests/test_matcher.py) | `JobMatcher.score` + `rank_and_filter` — excluded keywords, blacklisted companies, descending sort. |
| [`tests/test_prompts.py`](../../../tests/test_prompts.py) | LLM-01 — every prompt template's shared prefix between two distinct jobs must exceed 4500 chars (Gemini implicit cache). |
| [`tests/test_sanitizer.py`](../../../tests/test_sanitizer.py) | `sanitize_for_prompt`/`sanitize_url`/`wrap_untrusted` — truncation, control chars, 11 prompt-injection patterns. |
| [`tests/test_scraping.py`](../../../tests/test_scraping.py) | `_extract_json_from_text`, `AdaptiveScraper._parse_agent_result`, `ScrapingOrchestrator.scrape_batch` with mocks. |
| [`tests/test_session_manager.py`](../../../tests/test_session_manager.py) | `BrowserSessionManager` — list/clear/get_or_create across legacy + canonical layouts. |
| [`tests/test_settings_cv_upload.py`](../../../tests/test_settings_cv_upload.py) | `POST /api/settings/profile/cv-upload` — happy path, allowed/blocked extensions, size, replace, sets `base_cv_path`. |
| [`tests/test_skill_patterns.py`](../../../tests/test_skill_patterns.py) | Skill alias canonicalisation rules. |
| [`tests/test_smoke.py`](../../../tests/test_smoke.py) | 4 liveness pings — `/api/health`, `Settings()` loads, `TestClient(app)` constructs, stub routes don't 500. |
| [`tests/test_today.py`](../../../tests/test_today.py) | `/api/today` response shape on empty DB — `new_matches`, `blocked_actions`, `week_stats`. |
| [`tests/test_websocket.py`](../../../tests/test_websocket.py) | WS connect/disconnect, ping-pong, unknown-message resilience, `broadcast_status` helper. |
| [`tests/test_windows_playwright.py`](../../../tests/test_windows_playwright.py) | **Manual diagnostic** — Windows Playwright / browser-use / patchright deadlock troubleshooting, ordered tests 01-12. |
| [`tests/test_ws_broadcaster.py`](../../../tests/test_ws_broadcaster.py) | Every WS helper's wire payload validates against `WSMessage` discriminated union via `TypeAdapter`. |
