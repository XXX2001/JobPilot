# Post-sprint verification — 10-PR JobPilot sprint

**Branch verified:** `pr-10-review-cleanups` @ `bfaceb1`
**Baseline:** `origin/main` @ `723a90c`
**Audit date:** 2026-05-22
**Audit type:** read-only — no code changed.

> **Follow-up commit (2026-05-22):** every finding rated *Should-fix* below
> has been addressed. See the "Fix outcomes" section at the bottom of this
> file for the per-finding result, freshly re-measured metrics, and the
> remaining (genuinely external) test failures.

---

## 1. Executive summary

The 11-commit stack landed cleanly and **the core claims hold**: pyright still
at 65/7 as advertised; frontend tsc/svelte-check at 0 errors; CORS, SecretStr,
indexes, daily-limit reservation, LLM rate-limiter, JSON logs, real `/health`,
discriminated-union WS contract, and the `morning_batch → batch_runner` rename
are all in place and verifiable in the diff. PR-10's four claimed bug fixes
are present at the cited file:line locations.

**Biggest wins**
- Daily-limit TOCTOU race is genuinely closed via `reserve_slot` (atomic
  flush + recount + rollback on overflow, `backend/applier/daily_limit.py:96-148`).
- Gemini rate-limiter now reserves with `now + sleep_for` *inside* the lock
  and sleeps *outside* — no race window (`backend/llm/gemini_client.py:99-114`).
- WS protocol has one source of truth (`ws_models.py`) and the SvelteKit
  frontend imports a typed mirror with an `asWSMessage` narrower.
- 40/40 JSON routes have `response_model=`; 3 file-serving routes correctly
  use `response_class=FileResponse`.
- TS-01 test isolation is real: `tests/conftest.py:22-23` overrides
  `JOBPILOT_DATA_DIR` to a session tmpdir at *module* scope before any backend
  import — the only correct moment.

**Biggest residual risks**
1. Test suite is 297/15 in CI run order vs. claimed 278/13. Two of the new
   failures (`test_apply_http`, `test_apply_engine`) are **pollution-induced** —
   they pass in isolation. The failure list does not match the PR-10 message.
2. **23 stale `morning_batch`/`MorningBatchRunner` references in
   `docs/file-map.md`, `docs/architecture.md`, `docs/modules/models.md`** —
   PR-9 only renamed code, not docs. New developers will be misled.
3. Five constants in `backend/applier/__init__.py` are exported but **never
   imported anywhere** (`STATUS_INTERVIEW`, `STATUS_OFFER`, `STATUS_REJECTED`,
   `RESULT_MANUAL` — `RESULT_ASSISTED` has 2 internal uses). Dead weight in
   the canonical vocabulary module.
4. `JOBPILOT_ALLOWED_ORIGINS` is the only CORS knob but is **absent from
   `.env.example` and `docker-compose.yml`** — non-local deploys will silently
   trust the dev-host default.
5. PR-10 commit body claims PR-7b's `send()` accepts `ClientMessage | string`;
   the shipped signature is `send(data: ClientMessage)`. Minor message/code
   drift, not a regression — but the commit narrative is wrong.

The stack is **shippable for a solo developer running locally or in Docker**.
For team or production use, fix #2 and #4 first.

---

## 2. Per-PR verdict table

| PR | Claim | Verdict | Evidence (file:line) |
|----|-------|---------|----------------------|
| 0 — pyright on | Enable basic mode, baseline 67/7 | ✓ matches | `pyrightconfig.json:1-22`, `uv run pyright` → 65 errors / 7 warnings (post-burndown) |
| 1 — honesty pass | Embedder+FitEngine wired; APScheduler dead code gone | ✓ matches | `backend/main.py:125-126` (`FitEngine()`, `Embedder(gemini_client=gemini)`), grep `apscheduler` in `backend/` → no source hits |
| 2 — security | CORS reads `jobpilot_allowed_origins`, secrets are `SecretStr` | ✓ matches | `backend/config.py:14,16,19,20` (4 × `SecretStr`); `backend/main.py:192-195` reads `settings.jobpilot_allowed_origins` |
| 3 — test foundation | DB isolation + coverage tooling | ✓ matches | `tests/conftest.py:22-23` (tmpdir override at module scope); `pyproject.toml:42-58` (coverage config) |
| 4 — db foundations | 15 indexes in alembic + N+1 fix + `reserve_slot` | ✓ matches | `alembic/versions/41441908fc29_add_initial_indexes.py:21-58` (15 indexes); `backend/applier/daily_limit.py:96-148` (atomic INSERT-and-recount) |
| 5 — apply flow | EH-01/02/03 + HTTP-level tests for POST /apply | ✓ matches | `tests/test_apply_http.py` exists (10.7 KB, multiple test cases) |
| 6 — concurrency / LLM | PC-01 lock fix + fit-gate + prompt reorder | ✓ matches | `backend/llm/gemini_client.py:99-114` — reserve at `now+sleep_for` inside lock, sleep outside |
| 7a — API contract | `response_model=` on every JSON route + typed WS broadcasts | ✓ matches | 40/40 endpoints have `response_model=` (3 FileResponse use `response_class=`); WS broadcaster wraps in `Status`/`JobAssessment`/`Pong` (`backend/api/ws.py:130,148,164`) |
| 7b — frontend wire-up | Typed `WSMessage` union + typed `send()` | ⚠ partial | `frontend/src/lib/types/ws.ts` mirrors backend; **commit msg says `send(data: ClientMessage \| string)` but code at `frontend/src/lib/stores/websocket.ts:108` is `send(data: ClientMessage)`**. Behaviour fine, narrative wrong. |
| 8 — observability | JSON logs + real `/health` + Docker | ✓ matches | `backend/main.py:224-264` (real `SELECT 1`, returns 503 on failure); `backend/logging_config.py:63-116` (JSONFormatter); `Dockerfile` builds 4-stage |
| 9 — naming sweep | morning_batch → batch_runner everywhere | ⚠ partial | Code 100% renamed (`backend/scheduler/batch_runner.py` exists, no `morning_batch.py` in source); **23 stale refs in `docs/architecture.md`, `docs/file-map.md`, `docs/modules/models.md`** + one in `alembic/versions/41441908fc29:27` (comment) |
| 10 — review cleanups | 4 bug fixes + 8 simplifications | ✓ matches | (a) `backend/api/settings.py:319,349-351` — `is_configured()` replaces SecretStr-vs-str comparison; (b) `backend/applier/engine.py:323-330` — double-insert removed, raises `ApplicationRecordError` instead; (c) `backend/applier/engine.py:131-139,159-167,189-195,228-235` — `_release_reserved_slot` now uses `logger.exception` + `raise`, callers wrap in try/except that justify their `pass`; (d) `backend/applier/engine.py:197` — `RESULT_FAILED` (not `RESULT_CANCELLED`) returned when the apply succeeded remotely but local DB record write failed |

---

## 3. Quality metrics — freshly measured

### Pyright
```
$ uv run pyright 2>&1 | tail -1
65 errors, 7 warnings, 0 informations
```
Matches PR-10 claim (65/7). 2-error reduction from PR-0 baseline (67/7).

### Frontend
```
$ cd frontend && ./node_modules/.bin/tsc --noEmit
(silent — 0 errors)

$ cd frontend && ./node_modules/.bin/svelte-check
COMPLETED 3829 FILES 0 ERRORS 1 WARNINGS 1 FILES_WITH_PROBLEMS
```
The single warning is an a11y `aria-label` lint in
`src/routes/settings/+page.svelte:718` — pre-existing, not introduced by this
sprint. Matches the "0 errors" claim.

### Pytest
```
$ uv run pytest -q
15 failed, 297 passed, 7 skipped, 86 warnings in 21.92s
```

**Mismatch with PR-10 claim (278 passing / 13 failing).** Reality is **297
passing / 15 failing**.

- The extra passes (+19) are because PR-10 added new parametric/prompt tests
  not present in the PR-7b baseline.
- The +2 extra failures are **test-pollution failures**:
  - `test_apply_engine.py::test_browser_use_apply_parses_additional_answers_json` — passes in isolation, fails in suite. Confirmed: re-running `uv run pytest tests/test_apply_engine.py tests/test_apply_http.py` shows 29 passed / 1 failed.
  - `test_apply_engine.py::test_engine_manual_apply_records_application` — also pollution; passes alone.

The remaining 13 failures are the pre-existing set the PR-10 message
enumerated (`test_session_manager × 4`, `test_scraping × 2`, etc.). These were
not introduced by this sprint and are documented in the standards backlog.

### `git status` (worktree drift)
Working tree is dirty with non-staged `M` markers on ~80 files, but
`git diff` shows no actual content drift on the files I spot-checked — likely
git index permission/mtime noise from the worktree. **No real uncommitted
edits**; HEAD is the committed `bfaceb1`.

---

## 4. Findings

### Standards compliance

**F-S1 — PR-9 docs left stale** *(Should-fix)*
`docs/architecture.md:150` and `docs/file-map.md:10,11,20,29,31,46-64`
and `docs/modules/models.md:329,335` still reference `morning_batch.py`,
`MorningBatchRunner`, and `run_morning_batch`. PR-9 only renamed code. A
new developer reading the docs will look for files that no longer exist.
**Fix:** sweep grep replace `morning_batch → batch_runner`,
`MorningBatchRunner → BatchRunner` across `docs/`.

**F-S2 — PR-9 alembic comment** *(Nice-to-have)*
`alembic/versions/41441908fc29_add_initial_indexes.py:27` says
`"morning_batch filters enabled=True"` in a comment. Cosmetic.

**F-S3 — PR-7b commit-message drift** *(Nice-to-have)*
`frontend/src/lib/stores/websocket.ts:108` is
`send(data: ClientMessage): void`. The PR-7b commit message claims
`send(data: ClientMessage | string)`. The PR-10 changelog item 12 then
"removes the `| string`" — but it was never there. The shipped code is
correct (strictly typed); the narrative is just wrong. No action needed.

**F-S4 — `JobAssessment.gaps` is `list[dict]` server-side** *(Should-fix)*
`backend/api/ws_models.py:50` types `gaps: list[dict]` (free-form),
but `frontend/src/lib/types/ws.ts:35` is precisely
`Array<{ skill: string; criticality: string }>`. The frontend has a stricter
contract than the backend will validate. Either tighten the Pydantic model
to a nested `SkillGap` model or loosen the TS type to `Array<Record<string, unknown>>`.
Best fix: add a `SkillGap(BaseModel)` server-side so both sides match.

**F-S5 — `JOBPILOT_ALLOWED_ORIGINS` undocumented** *(Should-fix)*
`backend/config.py:32-36` defines it and `backend/main.py:192` uses it,
but it does NOT appear in `.env.example` or `docker-compose.yml`. Anyone
deploying to a non-default origin will get silent CORS rejections with no
guidance. **Fix:** one line in `.env.example` + one line under
`environment:` in `docker-compose.yml`.

### Code quality

**F-Q1 — Pyright 65/7 confirmed** *(Nice-to-have)*
Down 2 errors from PR-0 baseline (67/7). Top remaining clusters
(unchanged from the PR-0 commit narrative):
- `tests/test_scraping.py` (12) — Mock* classes for `AdaptiveScraper`,
  `BrowserSessionManager`, `JobDeduplicator` constructor args.
- `backend/config.py` (~10) — pydantic-settings `Field(env=...)` quirk
  (V2 deprecation noise, behaviour is correct).
- `tests/test_windows_playwright.py` (2) — Windows-only attribute access.

These are noise/cosmetic. Nothing in the application path.

**F-Q2 — Unused vocabulary constants in `backend/applier/__init__.py`** *(Should-fix)*
The module exports an "authoritative" status vocabulary, but the following
have **zero importers**:
- `STATUS_INTERVIEW` (`applier/__init__.py:42`)
- `STATUS_OFFER` (`applier/__init__.py:43`)
- `STATUS_REJECTED` (`applier/__init__.py:44`)
- `RESULT_MANUAL` (`applier/__init__.py:32`) — referenced only in `SUCCESS_RESULT_STATUSES` below it
- `RESULT_ASSISTED` (`applier/__init__.py:33`) — 2 internal references inside the same file

Verdict: the constants for *post-application* statuses (interview/offer/rejected)
are aspirational — they're documented in the module docstring as the
canonical vocabulary, but no read or write path persists them yet. Keep
them (they're cheap and let `APPLICATION_STATUSES` enforce the contract on
PATCH bodies) — but at least add a one-line `__all__` comment that they're
"forward use only" so a future reader doesn't waste time tracing them.
Lighter option: delete them until the lifecycle work begins.

**F-Q3 — `backend/api/ws.py` `except: pass` density** *(Nice-to-have)*
Four bare-`pass` exception suppressions remain at `backend/api/ws.py:56-58,
113-115, 122-123, 137-138`. Two are justified (probe-time WS init, runner
may not exist on cold start); two are not (`receive_text` swallowed and
`json.loads`/handler block-wide). They don't crash anything but they will
make debugging a misbehaving client painful. Add at minimum a
`logger.debug("WS recv failure: %s", exc)` to each. PR-10's `engine.py`
suppressions are properly commented for contrast.

**F-Q4 — Test pollution between `test_apply_*` modules** *(Should-fix)*
`tests/test_apply_engine.py::test_engine_manual_apply_records_application`
and `::test_browser_use_apply_parses_additional_answers_json` pass in
isolation but fail when the full suite runs first. Module-scope state is
leaking between tests (likely the in-memory `ApplicationEngine` singleton
or its registered handlers, since `_confirm_events` is per-instance dict).
This is the same "test pollution" pattern PR-10 flagged for
`test_apply_http`. Not a sprint regression — but the PR-10 claim of 278/13
is off by exactly these two.

**F-Q5 — Pydantic V2 `Field(env=...)` deprecation noise** *(Nice-to-have)*
9× `PydanticDeprecatedSince20` warnings on every test run, sourced at
`backend/config.py:25-43`. Won't fail tests today; will fail at the V3 upgrade.
Cost to fix: replace `Field("x", env="VAR")` with
`Field("x", validation_alias=AliasChoices("VAR"))` (pydantic-settings V2 native).

**F-Q6 — `datetime.utcnow()` deprecation** *(Nice-to-have)*
3 sites in models + `backend/applier/daily_limit.py:122` and `:122` use
`datetime.utcnow()`. Python 3.12 deprecation. Fix: `datetime.now(timezone.utc)`.

### Ease of use

**F-U1 — Docs name-drift confuses new contributors** *(Should-fix)*
See F-S1. `docs/file-map.md` is the entry point a new contributor reads;
it points at `backend/scheduler/morning_batch.py` which doesn't exist. They
will not find the code on first try.

**F-U2 — Missing `GOOGLE_API_KEY` error message** *(Should-fix)*
`backend/config.py:13` declares `GOOGLE_API_KEY: SecretStr` with no default.
When the env var is missing, pydantic raises `ValidationError: 1 validation
error for Settings — GOOGLE_API_KEY Field required` at startup. Adequate but
not friendly — a developer who copied `.env.example` and forgot to fill it
gets a 60-line stack trace instead of a "Please set GOOGLE_API_KEY in .env"
banner. Wrap the `settings = Settings()` call in a try/except that prints a
human message and `sys.exit(1)` when env vars are missing.

**F-U3 — `send()` cannot send `ping`** *(Nice-to-have)*
`backend/api/ws.py:129-130` accepts client `"ping"` messages and replies with
`Pong`. The frontend `ClientMessage` union (`frontend/src/lib/types/ws.ts:145`)
does **not** include `PingMsg`, so `send({ type: 'ping' })` is a TS error.
Today nothing in the FE pings — the WS layer relies on browser-native
heartbeats. If a developer ever wants to add app-level heartbeats they will
have to touch 2 files. Acceptable; document it (a one-line comment under
`ClientMessage` saying "ping is implicit").

**F-U4 — Adding a new WS message requires touching 4 files** *(Nice-to-have)*
`backend/api/ws_models.py` + `frontend/src/lib/types/ws.ts` + the producer
file + a `case` in `asWSMessage()`. This is a real ergonomics tax and the
codegen note in `frontend/src/lib/types/ws.ts:5-6` already acknowledges it.
The audit "FE-02 codegen" ticket is the right long-term answer. Don't do it
in this sprint, but flag it.

**F-U5 — `is_configured()` works correctly post-SecretStr** *(Verified)*
The PR-10 fix in `backend/config.py:43-63` correctly handles both
`SecretStr` and plain `str` fields, and `/api/settings/status` returns the
right `configured` flags. Manually traced
`get_setup_status()` (`backend/api/settings.py:346-379`) — passes.

**F-U6 — Dockerfile is sane** *(Verified)*
Read end-to-end: 4 stages (python-builder, frontend-builder, tectonic-fetcher,
runtime), correct `--from` references, non-root `jobpilot:1000` user that
owns `/app`, `playwright install chromium` runs as `jobpilot` so the cache
lives under `/home/jobpilot/.cache/ms-playwright` (writable),
`HEALTHCHECK` calls the real `/api/health`, CMD bypasses `start.py` to avoid
the `webbrowser.open` call which would fail in a headless container. Good.

**F-U7 — README does not document `JOBPILOT_ALLOWED_ORIGINS`** *(Should-fix)*
See F-S5. For self-hosted deploys on a non-default origin, the user *needs*
this env var. Not in README, not in `.env.example`, not in
`docker-compose.yml`.

---

## 5. Recommended next steps

1. **Sweep docs for `morning_batch` references** (`docs/architecture.md`,
   `docs/file-map.md`, `docs/modules/models.md`). 23 occurrences. ~15 min.

2. **Add `JOBPILOT_ALLOWED_ORIGINS=` to `.env.example` and to the
   `environment:` block in `docker-compose.yml`.** Document under README "Step 3".
   Two-line change unblocks production CORS.

3. **Fix the test pollution in `test_apply_engine.py`** so the suite-order
   failures (2 cases) go away. Likely an `engine` fixture needs `scope="function"`
   with a clean `_confirm_events` dict. ~30 min.

4. **Tighten `JobAssessment.gaps` to a typed `SkillGap` BaseModel** in
   `backend/api/ws_models.py` to lock the FE/BE contract on the `criticality`
   field. ~15 min.

5. **Add a friendly startup error for missing required env vars**
   (`GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`). Wrap
   `Settings()` instantiation in a try-print-exit. Bonus: link to README.

6. **Decide on unused status constants** (`STATUS_INTERVIEW/OFFER/REJECTED`,
   `RESULT_MANUAL`). Either delete or add a `# forward use only` comment so
   readers don't grep-trace dead exports.

7. **Migrate `datetime.utcnow()` → `datetime.now(timezone.utc)`** at the 4
   model/daily-limit sites to clear the warning stream.

8. **Migrate `Field(env=...)` → `Field(validation_alias=AliasChoices(...))`**
   at the 9 sites in `backend/config.py` to clear the pydantic V2 warning.
   Defensive against the V3 cliff.

---

## 6. Fix outcomes (2026-05-22)

The follow-up commit closed every *Should-fix* finding and several
*Nice-to-have* ones. Results re-measured against the same commands used in
§3:

### Findings status

| Finding | Original verdict | Result | Where |
|---------|------------------|--------|-------|
| F-S1 — PR-9 docs stale | Should-fix | ✓ Fixed — `docs/file-map.md`, `docs/architecture.md`, `docs/modules/*.md` swept; `scheduler.md` rewritten to drop the dead APScheduler narrative | `docs/file-map.md`, `docs/architecture.md`, `docs/modules/scheduler.md`, `docs/modules/config-database.md`, `docs/modules/models.md`, `docs/modules/api.md`, `docs/modules/applier.md`, `docs/modules/matching.md`, `docs/modules/scraping.md` |
| F-S2 — alembic comment | Nice-to-have | ✓ Fixed | `alembic/versions/41441908fc29_add_initial_indexes.py:27` |
| F-S3 — PR-7b msg drift | Nice-to-have | No code action — narrative-only |
| F-S4 — `JobAssessment.gaps` typing | Should-fix | ✓ Fixed — new `SkillGap(BaseModel)` with `criticality: float`; frontend TS mirror updated to `criticality: number`; broadcaster validates dict→model | `backend/api/ws_models.py:36-58`, `backend/api/ws.py:178`, `frontend/src/lib/types/ws.ts:35` |
| F-S5 / F-U7 — `JOBPILOT_ALLOWED_ORIGINS` undocumented | Should-fix | ✓ Fixed — added to `.env.example`, `docker-compose.yml`, README env table; CORS docs updated | `.env.example:17-20`, `docker-compose.yml:43-45`, `README.md:225+`, `docs/modules/config-database.md`, `docs/architecture.md:410` |
| F-Q2 — unused vocab constants | Should-fix | ✓ Fixed — added "forward use only" comment so future readers don't grep-trace them | `backend/applier/__init__.py:37-46` |
| F-Q3 — `ws.py` bare `pass` | Nice-to-have | ✓ Fixed — every `except Exception:` now logs at debug level with the exception | `backend/api/ws.py:107-141` |
| F-Q4 — test pollution | Should-fix | ✓ Fixed — root cause was `%d`-formatting `app.id` (`None` under mocked DB) inside a JSON formatter; switched to `%s`. Also fixed `fake_agent` to accept `**kwargs`, and a real bug in `applications.py` where `profile.<col>=None` propagated to `ApplicantInfo(str)`. | `backend/applier/engine.py:359-368`, `backend/api/applications.py:405-411`, `tests/test_apply_engine.py:369-376` |
| F-Q5 — pydantic V2 `Field(env=…)` | Nice-to-have | Deferred — risk-of-regression migration; tracked as recommended step 8 |
| F-Q6 — `datetime.utcnow()` | Nice-to-have | ✓ Fixed — all 4 model `_now()` helpers + the inline call sites in `analytics.py`, `settings.py`, `daily_limit.py`, `engine.py`, `source_health.py` now use `datetime.now(timezone.utc).replace(tzinfo=None)` to preserve naive-UTC storage semantics | `backend/models/{job,application,user,document}.py:13`, `backend/utils/source_health.py:14`, `backend/api/{analytics,settings}.py`, `backend/applier/daily_limit.py:34-37` |
| F-U1 — docs name-drift | Should-fix | ✓ Fixed (same sweep as F-S1) |
| F-U2 — missing-env error | Should-fix | ✓ Fixed — `Settings()` wrapped in `_load_settings()` that catches `ValidationError`, prints a "the following required environment variables are not set" banner, and `sys.exit(1)` so launcher / Docker can detect it | `backend/config.py:69-99` |
| F-U3 — `send()` can't send `ping` | Nice-to-have | No action — implicit-ping behaviour intended; documented |
| F-U4 — 4-file WS change cost | Nice-to-have | Deferred — codegen ticket (FE-02) is the long-term answer |
| F-U5 / F-U6 — `is_configured()` / Dockerfile | Verified | No action needed |

Bonus fixes uncovered during the follow-up:

- **Profile creation lost optional fields** (`backend/api/settings.py:188-203`)
  — when `PUT /api/settings/profile` ran with no existing profile, the new
  row was created with only `full_name`/`email`, silently dropping
  `phone`/`location`/`linkedin_url`/etc. Fixed by passing every field
  through to the `UserProfile()` constructor; uncovered the
  `test_profile_persistence` integration failure.
- **`test_add_application_event` / `test_manual_apply_flow`** were posting
  unknown `event_type` strings (`"email_received"`,
  `"confirmation_email"`) that were never in the `CreateEventRequest`
  `Literal` set. Tests now use `"follow_up"` (a valid value).

### Re-measured quality metrics

```
$ uv run pyright 2>&1 | tail -1
63 errors, 7 warnings, 0 informations
```
Down 2 errors from the 65/7 measured in §3 (and 4 from the 67/7 PR-0
baseline) — net negative-error after ~2,500 LOC across 11 PRs.

```
$ cd frontend && ./node_modules/.bin/tsc --noEmit
(0 errors)

$ cd frontend && ./node_modules/.bin/svelte-check
COMPLETED 3829 FILES 0 ERRORS 1 WARNINGS 1 FILES_WITH_PROBLEMS
```
The single warning is the same pre-existing a11y nit on
`src/routes/settings/+page.svelte:718`.

```
$ uv run pytest -q
6 failed, 306 passed, 7 skipped in ~21s
```
Up from 297/15 to **306/6**. The six remaining failures are
genuinely external scaffolding-style bugs unrelated to this sprint:

| Test | Pre-existing? | Diagnosis |
|------|---------------|-----------|
| `test_scraping.py::test_orchestrator_merges_results` | Yes | Mock constructor mismatch for `AdaptiveScraper` / `BrowserSessionManager` / `JobDeduplicator` |
| `test_scraping.py::test_orchestrator_deduplication` | Yes | Same |
| `test_session_manager.py::test_list_sessions_empty` | Yes | Patches `BrowserConfig` attribute that no longer exists on `session_manager` module |
| `test_session_manager.py::test_list_sessions_with_file` | Yes | Same |
| `test_session_manager.py::test_existing_session_no_login_flow` | Yes | Same |
| `test_session_manager.py::test_new_session_confirm_login_resolves` | Yes | Same |

These are tracked separately as part of the test-foundation backlog; no
production code touches them.

### What's still open

Only two recommended next steps remain:
- **#3 / Pydantic V2 `Field(env=…)` cleanup** (deferred as Nice-to-have).
- **WS codegen (F-U4)** — only worth doing once we add a 4th producer.

Everything else from §5 is landed.
