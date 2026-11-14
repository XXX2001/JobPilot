# OpenJev Browser Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JobPilot fills job applications by driving the JobPilot Chrome with a System One decision model (OpenJev-4B on mouad-ba), and runs with no LLM API key by using the Claude Code / Codex CLIs for text.

**Architecture:** A new package `backend/applier/harness/` ports jev-ultrafast's observe → one typed request → validated code-owned action loop (MIT) onto async Playwright, adds frames, shadow roots, uploads, a code-owned submit guard and owner hand-offs, and runs first in the ASSISTED/AUTO strategies with the existing tiers as fallback. New `LLMClient` providers shell out to `claude -p` / `codex exec` like `~/brain/hub/runner.py`.

**Tech Stack:** Python 3.12, FastAPI, async Playwright (CDP attach), httpx, pytest + pytest-asyncio, headless Chromium from `~/.cache/ms-playwright`.

**Spec:** `docs/superpowers/specs/2026-11-14-openjev-harness-design.md` — read the whole file; the section "Decisions after the builder check" overrides anything above it.

**Reference code (read, port, do not import):** `/tmp/claude-1000/-home-mouad/5b63171c-96ad-47ed-a284-3b3b9aa98bec/scratchpad/jev-ultrafast/` (`jev_ultrafast/snapshot.js`, `model.py`, `browser.py`, `agent.py`, `questions.py`, `docs/design.md`). OpenJev's copy of the instruction text: `/tmp/claude-1000/-home-mouad/5b63171c-96ad-47ed-a284-3b3b9aa98bec/scratchpad/openjev/.venv/lib/python3.12/site-packages/wev/prompts.py` and `mind2web.py` (`build_questions`, `LABEL_CHARS = 100`, `MAX_SELECT_OPTIONS = 60`).

## Global Constraints

- Run tests with `env -u PYTHONPATH .venv/bin/python -m pytest -q -n auto` from the worktree root `/home/mouad/JobPilot-harness`. The full suite must stay green (baseline: 1264 passed, 9 skipped).
- No new runtime dependencies. httpx and playwright are already installed.
- Instruction strings `NEXT_ACTION`, `TARGET`, the operation labels, `DONE`/`BLOCKED` labels and `TEXT_VALUE` are copied **verbatim** from jev-ultrafast `questions.py` / `model.py`. Changing them breaks the model's accuracy.
- Model output never becomes a selector, coordinate, script or shell command. Every executed target is a code-owned node from the latest snapshot.
- The harness never clicks a final submit without an owner approval (spec decision 2). Never type into password inputs.
- Attached Chrome: close only the page you opened, never the context (`backend/browser/chrome.py: open_context` returns `(context, attached)`).
- `tests/conftest.py` keeps tests offline: `DECISION_MODEL_URL=""`, and `LLM_PROVIDER` resolved to a fake in tests. Live tests are marked `@pytest.mark.live` and skipped unless `HARNESS_LIVE_URL` is set.
- Python files are auto-formatted by the owner's hook (ruff format); keep line length ≤ 110; follow the surrounding style. Add an MIT attribution header to every file ported from jev-ultrafast.
- Do not commit. The coordinator commits after review. Do not edit files outside the task's list without saying why.

## Review Focus

1. A "Submit application" button rendered as `<div role="button">` or `<a>` with no form: the guard must still stop before it (label pattern + `submit_target`), not only `type=submit` controls.
2. A Greenhouse-style form whose `input[type=file]` is `display:none` behind an "Attach" button: the CV is attached by code and no native file chooser stays open (test with the hidden input and a click on "Attach").
3. A page that changes between decision and action (an element replaced by React): execution raises `StalePage`, the loop re-observes, and nothing is typed into the replacement node.
4. The decision server down or answering 422/500 mid-run: the run falls back to the existing tiers **only if no submit click happened**, and leaves no half-typed state claimed as done.
5. The `claude` binary missing from PATH while `LLM_PROVIDER=auto` and no keys: startup still succeeds, health says no writing model, and the harness leaves free-text fields empty and listed.

---

### Task 1: Claude/Codex CLI providers; no API key needed

**Files:**
- Create: `backend/llm/providers/cli_agents.py`
- Modify: `backend/llm/factory.py`, `backend/config.py` (`LLM_PROVIDER`, `llm_configured`, `validate_runtime_config`, new `LLM_CLI_MODEL: str = ""`), `backend/main.py` (startup: browser-LLM problems become warnings), `backend/applier/auto_apply.py` and `backend/applier/assisted_apply.py` (Tier 2 skipped with a warning when `make_browser_llm()` raises `ValueError`), `.env.example`, `docs/configuration.md`
- Test: `tests/test_cli_agents.py`, update `tests/test_config_validation.py`, `tests/test_llm_factory.py` where they pin the old behaviour

**Interfaces:**
- Produces: `ClaudeCLIClient(model: str = "sonnet", timeout: float = settings.LLM_TIMEOUT_SECONDS)` and `CodexCLIClient(model: str = "", timeout: float = ...)`, both satisfying `backend.llm.base.LLMClient` (`generate_text(prompt, *, response_mime_type=None, response_schema=None) -> str`, `generate_json(prompt, schema: type[BaseModel]) -> BaseModel`).
- Produces: `backend.llm.factory.resolve_llm_provider() -> str` returning one of `claude|codex|openai|anthropic|none`; `make_llm_client()` raises `ValueError("no writing model")` for `none`.
- Produces: `settings.writing_model_available() -> bool` (true for a found CLI or a configured key); `llm_configured()` returns it.

- [ ] **Step 1: Write failing tests** in `tests/test_cli_agents.py`, using fake `claude`/`codex` executables written to `tmp_path` and put first on `PATH` via `monkeypatch.setenv`:
  - `test_claude_generate_text_reads_result`: fake prints `{"type":"result","subtype":"success","is_error":false,"result":"hello"}` → `generate_text` returns `"hello"`; assert the argv contained `-p`, `--output-format json`, `--tools ""` (as an empty argument), `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`, `--model sonnet`, and the prompt arrived on stdin.
  - `test_claude_generate_json_reads_structured_output`: fake prints `{"result":"","structured_output":{"answer":4},"is_error":false}`; `generate_json(prompt, Answer)` returns `Answer(answer=4)`; argv has `--json-schema` with `Answer.model_json_schema()`.
  - `test_codex_generate_json_uses_output_schema_and_last_message`: fake codex writes `{"answer":4}` to the path after `-o`; argv contains `exec --json -s read-only --skip-git-repo-check --ephemeral -C <dir> --output-schema <file> -o <file> -`.
  - `test_cli_nonzero_exit_raises_llm_call_failed` (stderr tail in message) and `test_cli_bad_json_raises_llm_json_error`.
  - `test_child_env_has_no_pythonpath` (fake echoes its env).
  - `test_auto_provider_prefers_claude_then_codex_then_keys` (PATH with only codex → `codex`; neither and no keys → `none`).
  - `test_startup_needs_no_key`: `validate_runtime_config()` returns `[]` with a fake `claude` on PATH and every key empty, and also with nothing at all (no writing model is a warning, not a startup error).
- [ ] **Step 2: Run** `env -u PYTHONPATH .venv/bin/python -m pytest -q tests/test_cli_agents.py` — expect failures (module missing).
- [ ] **Step 3: Implement.** `asyncio.create_subprocess_exec`, prompt on stdin, `env` without `PYTHONPATH`, `asyncio.wait_for(..., timeout)`, kill on timeout → `LLMCallFailed("timed out")`. Claude reads `structured_output` when a schema was sent, else `result`; `is_error: true` → `LLMCallFailed`. Codex runs in a fresh `tempfile.TemporaryDirectory()` and reads the `-o` file. `generate_text(response_schema=...)` passes that schema the same way. `LLM_PROVIDER` default becomes `"auto"`; keep `openai`/`anthropic` paths unchanged.
- [ ] **Step 4: Run** the new tests, then the full suite. Expect: all pass.

### Task 2: Snapshot and executor on async Playwright (frames, shadow roots, uploads)

**Files:**
- Create: `backend/applier/harness/__init__.py`, `backend/applier/harness/snapshot.js`, `backend/applier/harness/snapshot.py`, `backend/applier/harness/execute.py`
- Create fixtures: `tests/fixtures/harness/greenhouse.html` (single page, required fields, hidden `input[type=file]` behind an "Attach" button, a "Submit Application" `type=submit` button), `easy_apply.html` (two-step modal dialog: step 1 contact fields + "Next" `type=submit`; step 2 a `<select>` and a free-text question + "Submit application" `<div role="button">`), `iframe_host.html` + `iframe_form.html` (form inside a same-origin iframe), `shadow.html` (fields inside an open shadow root), `aria_dropdown.html` (custom `role=combobox` + `role=option` list), `login.html`, `cookie.html`, `received.html`
- Test: `tests/test_harness_snapshot.py`

**Interfaces:**
- Produces (`snapshot.py`): `async def observe(page) -> dict` returning `{url, title, text, actions, uploads, fingerprint, frames}`. `actions` are jev-ultrafast action dicts (`id`, `kind` in `click|fill|select|scroll|wait`, `node`, `role`, `label`, `value`, `current_value`, `checked`/`selected`/`expanded`, `submits: bool`) plus `frame: int` (index into `page.frames` order at observation); ids are unique across frames (`e1…`, scroll/wait ids unchanged). `uploads` is a list of `{id, frame, node, label, name, accept, filled: bool}` for every file input, visible or hidden. `submits` is true for form-submitting controls as defined in spec decision 2 (type=submit, button without type inside a form, or a button/link whose label matches the submit pattern).
- Produces (`snapshot.py`): `async def fresh(page, snap: dict, action: dict | None = None) -> bool` (jev-ultrafast `Browser.fresh` semantics, per frame).
- Produces (`execute.py`): `class StalePage(ValueError)`; `async def act(page, snap: dict, action: dict, text: str | None = None) -> None` (hit-test then `mouse.click`, select-all + `keyboard.insert_text` for fill, `select_option` by value for select, `mouse.wheel` for scroll, 100 ms for wait, then the settle wait from spec "How it works"); `async def attach(page, snap: dict, upload: dict, path: Path) -> None` (`set_input_files` on the resolved element handle).
- Constants in `snapshot.py`: `SUBMIT_PATTERN`, `ADVANCE_PATTERN` (compiled, case-insensitive, values from spec decision 2), `LABEL_CHARS = 100`, `MAX_SELECT_OPTIONS = 60`, `MAX_ACTIONS = 250`.

- [ ] **Step 1: Write failing tests** with a module-scoped fixture launching `playwright.chromium.launch(headless=True)` and loading fixtures via `page.goto(file://…)` (or a `http.server` thread for the iframe pair):
  - `test_observe_numbers_controls_like_jev_ultrafast` (greenhouse: First Name is `fill` + `click` "Open First Name", Submit has `submits=True`).
  - `test_hidden_file_input_is_reported_as_upload_not_action`.
  - `test_iframe_fields_get_frame_index_and_unique_ids`, `test_shadow_root_fields_are_observed`.
  - `test_select_offers_at_most_60_options_keeping_current`.
  - `test_act_fill_replaces_value`, `test_act_select_by_value`, `test_act_click_in_iframe`.
  - `test_act_raises_stale_when_node_replaced` (fixture script replaces the input after observation).
  - `test_attach_sets_hidden_input_files`.
  - `test_password_inputs_never_observed` (login.html).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** by porting `snapshot.js` (keep its cache object on `window`, rename to `__jobpilotHarness`) and the `browser.py` executor. Add the shadow-root walk to both the control query and the text walker; add the upload list and `submits`. `observe()` evaluates the script in each frame (`frame.evaluate`), skips frames that throw (cross-origin detach), merges, re-numbers, recomputes the fingerprint over all frames.
- [ ] **Step 4: Run** new tests, then the full suite.

### Task 3: System One client and request builder

**Files:**
- Create: `backend/applier/harness/questions.py` (verbatim strings), `backend/applier/harness/decide.py`
- Modify: `backend/config.py` (`DECISION_MODEL_URL: str = ""`, `DECISION_MODEL_API_KEY: SecretStr = SecretStr("")`, `DECISION_MODEL_NAME: str = "jev-latest"`, `HARNESS_MAX_ACTIONS: int = 60`), `tests/conftest.py` (`DECISION_MODEL_URL=""`)
- Test: `tests/test_harness_decide.py`, golden file `tests/fixtures/harness/golden_request.json`

**Interfaces:**
- Produces: `class DecisionError(RuntimeError)`, `class RequestTooLong(DecisionError)`.
- Produces: `class SystemOneClient(url: str, api_key: str = "", model: str = "jev-latest", timeout: float = 30.0)` with `async def post(self, body: dict) -> dict` (httpx.AsyncClient; retries 429/503/529 twice with 0.5·2ⁿ s; 422 → `RequestTooLong`; other errors → `DecisionError`) and `aclose()`.
- Produces: `def build_request(snap: dict, goal: str, history: list[dict], model: str) -> tuple[dict, dict, dict]` → `(body, targets, controls)` exactly like jev-ultrafast `choose()` plus `page_kind` and `submit_target` questions (spec "How it works" → `decide.py`, criteria ids `application_form, login_wall, captcha, application_received, job_description, error, other`; `submit_target` criteria = click targets + `"none"`).
- Produces: `@dataclass Decision(operation: str, action: dict | None, page_kind: str, page_kind_p: float, submit_action: dict | None, submit_p: float, operation_p: float, target_p: float, latency_ms: int)` and `async def decide(client, snap, goal, history) -> Decision` (validates every used Choice like `validate_choice`; on `RequestTooLong` shrinks text to 3,000 chars and elements to 120 once).
- Produces: `async def pick_slot(client, field: dict, page_title: str, slots: dict[str, str]) -> tuple[str, float]` (state `{"page": page_title, "field_label": field["label"]}`, instruction `Which profile value should be typed into the form field labelled "<label>"?`, criteria = slot ids → description) and `async def classify_page(client, text: str) -> tuple[str, float]`.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`:
  - `test_build_request_matches_jev_ultrafast_shape`: for a fixed snapshot dict, the `operation` / `*_target` questions and `state` equal `golden_request.json` (generate the golden by running jev-ultrafast `model.choose`'s body construction on the same actions — copy the logic into the test only, not the app).
  - `test_page_kind_and_submit_questions_present`.
  - `test_invalid_choice_raises_and_executes_nothing` (choice not in criteria; probabilities not summing to 1).
  - `test_retry_on_529_then_success`, `test_422_shrinks_once_then_raises_request_too_long`.
  - `test_bearer_header_only_when_key_set`.
  - `test_pick_slot_sends_label_in_instruction`.
- [ ] **Step 2–4:** run (fail), implement, run new + full suite.

### Task 4: The harness loop

**Files:**
- Create: `backend/applier/harness/agent.py`
- Test: `tests/test_harness_agent.py` (real headless Chromium + the Task 2 fixtures + a scripted fake decider)

**Interfaces:**
- Consumes: Task 2 `observe`, `fresh`, `act`, `attach`, `StalePage`; Task 3 `decide`, `pick_slot`, `classify_page`, `Decision`, `DecisionError`.
- Produces: `class HarnessBlocked(RuntimeError)`, `class HarnessStuck(HarnessBlocked)`.
- Produces: `def profile_slots(full_name: str, email: str, phone: str, location: str, linkedin_url: str, extra: dict[str, str]) -> dict[str, tuple[str, str]]` → slot id → `(description, value)`, only non-empty values, per spec decision 6, plus `needs_written_answer` and `leave_empty` with empty values.
- Produces: `def build_goal(job_title: str, company: str, full_name: str, email: str, phone: str, location: str, linkedin_url: str) -> str` (spec decision 1 template).
- Produces: `@dataclass HarnessResult(stop: str, submit_action: dict | None, typed: dict[str, dict], needs_you: list[str], written: list[str], uploads: list[str], steps: int, clicked_submit: bool = False)` where `stop` ∈ `submit_point|ready|already_received` and `typed` maps `"<frame>:<node>"` → `{label, value, source}`.
- Produces: `async def run_harness(page, *, goal: str, slots: dict, documents: dict[str, Path], writer: Callable[[dict], Awaitable[str]] | None, client, job_id: int | None, cancel_event: asyncio.Event | None = None, max_actions: int = 60, notify: Callable[[str, str], Awaitable[None]] | None = None, wall_clock_s: float = 300) -> HarnessResult`. `writer` gets `{label, page_text, …}` and returns text or `""`. `notify(kind, message)` sends the owner hand-off (kind `login`/`captcha`).

- [ ] **Step 1: Write failing tests** (fake decider = an object whose `decide`/`pick_slot`/`classify_page` return scripted answers keyed by step, injected via a `client` stub; monkeypatch the module functions if simpler):
  - `test_fills_greenhouse_and_stops_at_submit_point_without_clicking` (assert the submit button's click counter in the page is 0, values typed equal profile values, CV attached to the hidden input).
  - `test_easy_apply_next_is_clicked_submit_div_is_not` (step 2 reached; `<div role=button>Submit application` never clicked even when the fake says CLICK on it with `submit_target` = none — label pattern stops it).
  - `test_filechooser_is_answered_with_cv` (fake says CLICK "Attach"; no dialog left; CV recorded once).
  - `test_low_slot_probability_leaves_field_and_lists_it` and `test_needs_written_answer_uses_writer_and_records_written`; `test_writer_empty_string_leaves_field_empty`.
  - `test_login_wall_notifies_and_waits_until_resolved` (fake classify returns login_wall twice then application_form; `notify` called once with `"login"`), `test_login_wall_timeout_raises_blocked` (tiny timeout).
  - `test_three_unchanged_steps_raise_stuck`, `test_max_actions_raises_stuck`.
  - `test_already_received_page_returns_already_received`.
  - `test_stale_page_reobserves_without_typing`.
  - `test_never_types_into_password` (login.html, fake says TYPE_TEXT on whatever is offered; password is never offered).
- [ ] **Step 2–4:** run (fail), implement per spec decisions 2, 3, 6, 7, 8, 9, run new + full suite.

### Task 5: Wire into ASSISTED and AUTO, review, settings, docs

**Files:**
- Create: `backend/applier/harness/runner.py`
- Modify: `backend/applier/engine.py` (`ApplicantInfo.linkedin_url: str = ""`; pass `job_id` to ASSISTED too), `backend/api/applications.py` (fill `linkedin_url` and additional info from `UserProfile` where `ApplicantInfo` is built), `backend/applier/assisted_apply.py`, `backend/applier/auto_apply.py`, `backend/applier/form_filler.py` (move `_publish_review` / `_wait_decision` into a shared module function both use; Tier 1 behaviour unchanged), `.env.example`, `docs/browser.md`, `docs/configuration.md`, `CHANGELOG.md`, `README.md` (one paragraph)
- Test: `tests/test_harness_runner.py`, keep `tests/test_form_filler.py`, `tests/test_apply_safety.py`, `tests/test_patch_fields.py` green

**Interfaces:**
- Consumes: Task 4 `run_harness`, `profile_slots`, `build_goal`, `HarnessBlocked`; Task 3 `SystemOneClient`, `classify_page`; Task 1 `make_llm_client`.
- Produces: `async def harness_apply(*, mode: str, apply_url: str, job_id: int | None, applicant, cv_pdf: Path | None, letter_pdf: Path | None, confirm_event=None, cancel_event=None, on_review=None, on_get_patches=None, on_browser=None) -> ApplicationResult | None`. `None` = fall through to the existing tiers (only possible when no submit click happened). ASSISTED → `ApplicationResult(status=RESULT_AWAITING_USER, method="assisted", message=<typed / written / left-for-you lists>)`, tab left open. AUTO → review rounds through the shared publisher with harness field keys (spec decision 13), click the recorded submit node once on approval, classify per spec decision 11 → `RESULT_CONFIRMED` / `RESULT_NEEDS_VERIFICATION`; `already_received` → `RESULT_NEEDS_VERIFICATION`.
- The strategies call `harness_apply` first when `settings.DECISION_MODEL_URL` is non-empty, for every site including `is_multi_step_site` ones.

- [ ] **Step 1: Write failing tests:** `test_assisted_runs_harness_first_and_returns_awaiting_user`, `test_harness_error_before_submit_falls_back_to_tier1`, `test_harness_disabled_without_url_keeps_old_path`, `test_auto_review_approval_clicks_submit_once_and_verifies` (fixture `greenhouse.html` posts to `received.html`), `test_auto_cancel_never_clicks`, `test_auto_correction_refills_by_harness_key`, `test_job_and_linkedin_reach_the_goal`.
- [ ] **Step 2–4:** run (fail), implement, run new + full suite. Update the docs listed above (configuration keys, how to point at mouad-ba or hosted Jev and what each sends where).

### Task 6: Live verification (coordinator runs this, not the implementer)

- [ ] `HARNESS_LIVE_URL=http://100.100.9.110:8009/v1/systemone` live tests over every fixture with the real OpenJev-4B; record success, steps, seconds per fixture.
- [ ] Fill-only (ASSISTED) on 3+ real public postings from `data/jobpilot.db` (Greenhouse, Ashby, SmartRecruiters) in a separate headless Chromium; stop at the submit point; never submit; screenshots to the scratchpad.
- [ ] Full suite, frontend check, push branch, open a PR into `feat/monitor-discovery`.
