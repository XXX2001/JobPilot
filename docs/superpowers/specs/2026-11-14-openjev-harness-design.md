# OpenJev browser harness: design

Date: 2026-11-14. Branch `feat/openjev-harness` (from `feat/monitor-discovery`).

## Intent

JobPilot should apply on company career sites, ATS pages (Greenhouse, Ashby, Lever, Workday,
SmartRecruiters) and LinkedIn Easy Apply by **driving the browser with a small, fast, local
decision model** instead of a large LLM agent or per-site scripts. The owner chose this because it is
cheap (free on their own GPU), fast (0.1–0.7 s per step) and generic (one policy for every site).

The decision model is **OpenJev-4B v0.2**, served by `wev serve` on the owner's second machine
`mouad-ba` (RTX 5060, 8-bit) at `http://100.100.9.110:8009/v1/systemone` over Tailscale. It speaks
TypeSafe's System One wire format, so hosted Jev (`https://api.typesafe.ai/v1/systemone`, bearer key)
is a drop-in replacement by URL.

The second goal: **JobPilot must run without any LLM API key.** Text generation (free-text answers,
CV tailoring, job analysis) goes through the owner's logged-in **Claude Code** and **Codex** CLIs,
the same way `~/brain/hub/runner.py` does (`claude -p --json-schema …`, `codex exec --output-schema …`).

## Evidence behind the choices

Measured on the owner's hardware with JobPilot-shaped cases (scratchpad `openjev_test/run.py`):

| | OpenJev-1.7B | OpenJev-4B v0.2 (8-bit, RTX 5060) |
|---|---|---|
| browser step (operation + target) | 9/14 | 13/14, 0.3–1.1 s |
| form field → profile slot | 8/9 | 9/9, 0.1 s |
| page kind | 6/6 | 6/6, 0.1 s |

OpenJev was trained on **jev-ultrafast's exact request format** (`browser-use/jev-ultrafast`, MIT;
OpenJev's `wev/prompts.py` copies its instruction text verbatim). Accuracy depends on sending that
format unchanged. The remaining miss: on a login wall the model chose TYPE_TEXT instead of BLOCKED,
while the page-kind question classified the same page correctly (0.94).

browser-use 1.0's built-in `mode='ultrafast'` is not usable: it runs an LLM (gpt-6-luna) over
accessibility-tree ids and has no System One model slot.

## Goals

1. A harness that applies through the attached JobPilot Chrome using only System One decisions plus
   code, for single-page and multi-step forms, including LinkedIn Easy Apply modals.
2. Request format identical to jev-ultrafast's `choose()` (`jev_ultrafast/model.py`), with its
   instruction strings (`NEXT_ACTION`, `TARGET`, operation labels) copied verbatim.
3. Extra questions in the same request (speculative fan-out) for what JobPilot needs: page kind, and
   which click (if any) submits the application.
4. Values typed into fields come from the owner's profile, chosen by a System One slot question;
   only free-text questions go to a writing model.
5. Code owns the safety rules: no final submit without the owner's approval through the existing
   review flow; login walls and captchas hand off to the owner; step and stuck limits.
6. Handles what jev-ultrafast leaves out and job sites need: file uploads (CV, letter) by code,
   same-origin and cross-origin iframes (embedded Greenhouse), open shadow roots (Workday-style).
7. Claude Code and Codex CLI providers for `LLMClient`; startup and the UI no longer require an API key.
8. Tested offline (fake decision server, real headless Chromium on local fixture pages) and live
   (OpenJev on mouad-ba against the same fixtures, then fill-only on real public ATS postings).

## Non-goals

- Screenshots or image input (the owner dropped this once DOM reading of JavaScript pages was explained).
- Automated captcha solving, stored passwords, auto-login (unchanged project decisions D13, D14).
- Enabling auto-submit (`JOBPILOT_AUTO_SUBMIT_ENABLED` stays false; D15).
- Migrating to browser-use 1.0, or a browser-use LLM adapter over the CLIs. Tier 2 (browser-use)
  stays available only when a browser LLM key is configured.
- Training or fine-tuning models.

## Anti-goals (failure even if it "works")

- Any click on a final submit button without an explicit owner approval in the review flow.
- Model output turned into selectors, coordinates, JavaScript or shell. Targets are code-owned node ids.
- Typing invented personal data. A field with no profile value and no confident free-text answer is
  left empty and listed in the review.
- Typing into password fields, or touching the owner's everyday Chrome profile.
- Reformatting or rewriting unrelated parts of the codebase.

## Constraints

- Python 3.12, async Playwright over CDP attach (`backend/browser/chrome.py: open_context`), same as
  `form_filler.py`. Attached contexts: close only our page, never the context.
- No new runtime dependencies (httpx, playwright already present).
- English replies, commit format `<type>(<scope>): <desc>`, no co-author trailers (owner's rules).
- Tests run with `env -u PYTHONPATH uv run pytest -q -n auto`; the existing suite stays green
  (1264 passed, 9 skipped before this branch).
- `tests/conftest.py` must keep offline defaults: decision-model URL empty, CLI providers faked.

## How it works

### Package `backend/applier/harness/`

- `snapshot.js`: jev-ultrafast `snapshot.js` (MIT, attribution header), extended:
  - walks **open shadow roots** when collecting controls and visible text;
  - reports `input[type=file]` as `kind: "upload"` actions with their label and `accept` attribute.
    Uploads are never offered to the model; code handles them;
  - keeps skipping password and hidden inputs.
- `snapshot.py`: runs the script in the main frame and in every child frame (Playwright `page.frames`),
  merges results into one element table with globally unique ids (`f<frame>:<node>`), keeps a map from
  id to `(frame, node)` for execution, and computes the page fingerprint (jev-ultrafast `fingerprint`).
- `decide.py`: System One client and request builder.
  - `build_request(snapshot, goal, history)` reproduces jev-ultrafast `choose()` exactly:
    `operation` Choice plus one `<op>_target` Choice per available operation, state
    `{page: {url, title, text}, elements, recent_actions}`.
  - Adds `page_kind` Choice: `application_form`, `login_wall`, `captcha`, `application_received`,
    `job_description`, `error`, `other`.
  - Adds `submit_target` Choice over the click targets plus `none`: "Which click submits the whole
    application? Pick none if no visible control sends it."
  - `slot_request(field, page)` asks which profile slot fills one field (wording from the measured test:
    `Which profile value should be typed into the form field labelled "<label>"?`), slots:
    `first_name, last_name, full_name, email, phone, city, country, linkedin_url, website,
    current_title, years_experience, salary_expectation, needs_written_answer, leave_empty`.
  - `post()` uses httpx async, `DECISION_MODEL_URL`, optional bearer `DECISION_MODEL_API_KEY`,
    timeout 30 s, two retries on 429/503/529, and validates every Choice like jev-ultrafast
    `validate_choice` (choice in ids, probabilities sum to 1). An invalid answer raises; nothing executes.
- `execute.py`: executor ported from jev-ultrafast `browser.py`: freshness guard, visibility and
  hit-test check at the element centre, then Playwright `mouse.click` / select-all + `keyboard.insert_text`,
  native `<select>` via `select_option`, scroll via `mouse.wheel`, uploads via `set_input_files` on the
  file input element. Post-action settle wait: two animation frames or 50 ms, 200 ms for comboboxes,
  plus `domcontentloaded` if the URL changed.
- `agent.py`: `HarnessAgent.run(page, applicant, documents, mode, job_id, review)` loop:
  1. observe; if fingerprint unchanged since a decision, reuse it.
  2. one request; read `page_kind` first:
     - `login_wall` or `captcha` (p ≥ 0.6) → call the existing `check_and_handle_captcha` hand-off
       (notification, wait for the owner); if still blocked → raise `HarnessBlocked`.
     - `application_received` (p ≥ 0.8) → stop with outcome `submitted_by_page` (only reachable if
       the owner submitted).
  3. code-owned pre-steps before trusting the model on a form page: attach the CV / letter to visible
     upload controls whose label or accept attribute fits (CV: "resume", "cv", "curriculum";
     letter: "cover", "lettre", "motivation"); never twice.
  4. operation:
     - `CLICK`: if the target equals `submit_target` with p ≥ 0.3, **or** its label matches
       `submit|send|apply|envoyer|postuler|soumettre` and the page kind is `application_form`, it is the
       final submit → stop the loop and go to review (never clicked by the loop).
     - `TYPE_TEXT`: slot request for that field. Profile slot → type the profile value. Slot
       `needs_written_answer` → writing model (below). `leave_empty`, missing value, or slot probability
       < 0.5 → do not type; mark the field "needs you" and exclude it from later TYPE_TEXT targets.
     - `SELECT`: execute (the option is code-owned).
     - `SCROLL_*`, `WAIT`: execute.
     - `DONE`: treat as "ready for review" on an application form; elsewhere as blocked.
     - `BLOCKED`: raise `HarnessBlocked`.
  5. limits: 60 actions, 120 requests, three consecutive actions with no page change (not WAIT) →
     `HarnessStuck`; wall clock 300 s.
- Review and submit: reuse the Tier 1 review rounds (`PlaywrightFormFiller._publish_review`,
  `_wait_decision`, the correction/read-back rules) through a small shared helper so both tiers share
  one implementation. On approval the harness clicks the code-owned submit node once and classifies the
  result with `backend.applier.verification.verify_page`; post-click outcomes are `confirmed` or
  `needs_verification`, never retried (phase-4 rule). In ASSISTED mode it stops at the submit point,
  leaves the tab open and returns `awaiting_user`.
- The writing model: `LLMClient.generate_text` with the question label, nearby page text, job title and
  company, and the owner's CV text; at most 1,200 characters; the answer is shown in the review.

### Integration

- `AutoApplyStrategy.apply` and `AssistedApplyStrategy.apply`: when `settings.DECISION_MODEL_URL` is
  set, run the harness first, for every site including multi-step ones. On `HarnessBlocked`,
  `HarnessStuck`, a model or transport error, or any exception **before a submit click**, log it and
  fall through to the existing Tier 1 / Tier 2 path unchanged. After a submit click the result is final.
- Settings (`backend/config.py`): `DECISION_MODEL_URL: str = ""`, `DECISION_MODEL_API_KEY: SecretStr`,
  `DECISION_MODEL_NAME: str = "jev-latest"`, `HARNESS_MAX_ACTIONS: int = 60`. `.env.example` documents
  the mouad-ba URL and the hosted Jev URL.

### CLI LLM providers

- `backend/llm/providers/cli_agents.py`: `ClaudeCLIClient` and `CodexCLIClient` implement `LLMClient`.
  - Claude: `claude -p --output-format json --model <LLM_MODEL or "sonnet"> --tools ""`
    (no tools: generation only; fall back to `--disallowedTools` if `--tools` is unknown), prompt on stdin,
    `--json-schema` for `generate_json`; parse the `result` field.
  - Codex: `codex exec --json -s read-only --skip-git-repo-check -C <empty temp dir>
    --output-last-message <file> [--output-schema <file>] -`; model from `LLM_MODEL` or Codex's own default.
  - Both: `asyncio.create_subprocess_exec`, `PYTHONPATH` removed from the env (ROS pollution), timeout
    `LLM_TIMEOUT_SECONDS`, non-zero exit → `LLMCallFailed` with the tail of stderr; bad JSON → `LLMJSONError`.
- `LLM_PROVIDER` accepts `claude | codex | openai | anthropic`; the default becomes `auto`: Claude if
  `claude` is on PATH, else Codex, else openai/anthropic if a key is set.
- `validate_runtime_config()` checks the chosen CLI is on PATH instead of keys. A missing browser LLM
  no longer fails startup; it disables Tier 2 with a warning. `llm_configured()` is true for a found CLI,
  so the health chip and onboarding stop asking for a key.

## Testing

- Unit, offline: request builder matches jev-ultrafast's format for a fixed snapshot (golden JSON);
  answer validation; slot routing; submit detection; limits; CLI providers with fake executables on PATH.
- Real headless Chromium (Playwright's bundled Chromium, no Chrome attach) against local fixture pages
  served from `tests/fixtures/harness/`: single-page Greenhouse-like form with CV upload; two-step
  Easy-Apply-like modal; form inside an iframe; fields inside an open shadow root; custom ARIA dropdown;
  login wall; cookie banner. With a **scripted fake System One** they must reach the review point with
  the right values and never click submit before approval.
- Live (marked `live`, skipped unless `DECISION_MODEL_URL` answers): the same fixtures with OpenJev on
  mouad-ba; report per-fixture success, steps and time.
- Live fill-only on 3+ real public ATS postings from the monitor database (Greenhouse/Ashby/
  SmartRecruiters), ASSISTED mode, stopping at the submit point. **Never submit.** Screenshots kept in
  the scratchpad, not the repo.

## Decisions after the builder check (assistant's calls, owner asleep; they override the sections above)

Raw 4B results: scratchpad `openjev_test/results-4b-8bit.json` (copied from mouad-ba `~/openjev/`).

1. **Goal string carries the owner's values.** The copied rules ("do not choose a field that already
   contains the requested value", "fill required fields before submitting") only work when the goal
   names the values, as in the measured test. Template:
   `Fill in this job application for <job title> at <company> as <full name>, email <email>, phone
   <phone>, location <location>[, LinkedIn <url>]. Attach the CV when asked. Do not submit the
   application: when every field you can fill is filled, choose DONE.`
   The decision server is the owner's own machine over Tailscale. Pointing `DECISION_MODEL_URL` at
   hosted Jev sends these values to TypeSafe; `.env.example` says so.
2. **Submit guard, strongest form.** The loop never clicks a *form-submitting control* (`button` /
   `input` with `type=submit`, a `button` without `type` inside a `form`, or `role=button` whose label
   matches the submit pattern) unless its label matches the advance pattern
   (`next|continue|suivant|continuer|weiter|review|save and continue|étape suivante`) **and** does not
   match the submit pattern
   (`submit|send|apply|envoyer|postuler|soumettre|finish|complete|terminer|bewerben|absenden|valider ma candidature`)
   **and** `submit_target` does not pick it with p ≥ 0.3. Any other such click ends the loop at the
   submit point. After every executed click, `page_kind` is re-read on the next step; an unexpected
   `application_received` stops the run with `needs_verification`.
3. **Submit node.** The node recorded when the loop stopped at the submit point. If the loop ended on
   DONE: the `submit_target` of that request when p ≥ 0.3; else the single form-submitting control on
   the page that matches the submit pattern; else none (AUTO then returns `awaiting_user_submission`).
4. **AUTO approved click lands on another form step** → `needs_verification`, no continuation
   (phase-4 rule, unchanged). AUTO stays behind `JOBPILOT_AUTO_SUBMIT_ENABLED=false`; it is built and
   tested with fakes, but **ASSISTED is the production path and is built and tested first.**
5. **`application_received` before any click** → `needs_verification` with evidence
   "page already shows a confirmation"; never a new status string.
6. **Profile slots come only from stored data.** `first_name` = first whitespace token of
   `full_name`, `last_name` = the rest (empty if one token → slot unavailable); `full_name`, `email`,
   `phone`, `location` from `ApplicantInfo`; `linkedin_url` from `UserProfile` (thread it into
   `ApplicantInfo`); one slot per key of `additional_answers_json` / `UserProfile.additional_info`
   (criteria text = the key and value). Plus `needs_written_answer`, `leave_empty`. A slot whose value is
   empty is not offered. No country, years, salary unless the owner stored them as additional answers.
7. **Writing model inputs and ASSISTED typing.** Inputs: the field label, up to 1,500 characters of
   nearby page text, job title, company, and the base CV text from the database (whatever the existing
   CV pipeline stores for the owner); job data loaded by `job_match_id` (thread it to ASSISTED too).
   The prompt tells it to answer only from the CV and profile and to return an empty string when the
   answer needs facts not in them (eligibility, salary, notice period, visa) → field left empty.
   ASSISTED types generated answers and returns a message listing every generated field and every
   field left for the owner. AUTO shows the same lists in the review note.
8. **Login wall hand-off is its own path.** On `page_kind = login_wall` (p ≥ 0.6): broadcast the
   existing `LoginRequired` WS frame and an ntfy push ("Login needed on <host>"), then poll every 5 s
   with a page-kind-only request until it is no longer `login_wall` with p ≥ 0.6, the cancel event is
   set, or 10 minutes pass (→ `HarnessBlocked`, fall through). Captcha: call
   `check_and_handle_captcha`; if it returns False while `page_kind = captcha` (p ≥ 0.6), use the same
   generic wait with a "Captcha" message.
9. **Uploads with hidden inputs.** Code collects every `input[type=file]` in every frame, visible or
   not, and matches it to CV or letter by its own and nearby label text, `name`, `id` and `accept`.
   `set_input_files` works on hidden inputs. A `page.on("filechooser")` handler is installed for the
   run: any chooser the loop's click opens is answered with the matching document (CV by default),
   so no native dialog ever appears on the owner's screen. A click on an "attach/upload/resume/CV"
   control after that document is attached is skipped and recorded.
10. **Structured output from Claude** is `structured_output` in the JSON result when `--json-schema` is
    given; `result` otherwise. `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`
    keep it free of the owner's hooks and MCP servers (verified 2026-11-14: about 2 s per call;
    Codex about 8 s).
11. **Verification across frames.** After an AUTO submit click: `confirmed` when `verify_page` confirms
    **or** a page-kind request over the text of all frames returns `application_received` with
    p ≥ 0.8; otherwise `needs_verification`.
12. **Request size.** Element labels clipped to 100 characters, at most 60 options per `<select>`
    (keep the current one), at most 250 elements (jev-ultrafast). On HTTP 422: retry once with page text
    cut to 3,000 characters and elements cut to 120; a second 422 → `HarnessBlocked`.
13. **Review addressing.** The harness keeps its own field keys (`<frame index>:<node id>` plus label)
    and its own read-back and correction fill through its node map. Only publishing and waiting
    (`_publish_review`, `_wait_decision`) are shared with Tier 1; Tier 1's selector-based behaviour does
    not change. Fields inside cross-origin frames are listed in the review as read-only.
14. **Measured format vs. bundled format.** The live fixture run measures the bundled request (page kind
    and submit questions inside the browser request) with the 4B server; if page-kind accuracy drops
    below the separate-request result, page kind moves to its own request (0.1 s).

## Left to the builder

File-internal structure, helper names beyond those above, fixture page markup, exact log wording,
how the shared review helper is factored out of `form_filler.py` (behaviour must not change).

## Calls made by the assistant (not discussed with the owner)

- Harness first in line for all sites, existing tiers as fallback (the owner said "Yes" to the design
  without picking between "first in line" and "per job").
- Thresholds 0.6 / 0.8 / 0.3 / 0.5 and the slot list.
- Claude default model `sonnet` for generation (speed and quota); Codex uses its configured default.
- Tier 2 disabled when no browser LLM key exists, rather than writing a CLI adapter for browser-use.

## Decisions after the first live run (2026-11-15, assistant's calls)

Live run of the loop with OpenJev-4B on the fixtures (scratchpad `live/live_fixtures.py`): fields were
filled correctly (slot p 0.73–0.95), but after the fields were done the model wandered (re-toggling a
checkbox, "Open <field>" clicks on filled fields) instead of choosing DONE, and on a two-step form it
named "Next" as `submit_target` with p 0.97. The Task 4 critic also clicked unrecognised JS-only final
buttons ("Je postule", "Bewerbung einreichen", "Confirm", icon-only) through the old guard.

15. **Default-deny clicks in a form context.** Form context = `page_kind` application_form (p ≥ 0.5),
    or anything typed or attached in this run, or the page offers a `fill` action. In a form context
    a CLICK executes only when the target is one of:
    - an advance control: its **full** accessible name (not the 100-character clip) matches
      `ADVANCE_PATTERN` and no strict submit word;
    - a non-submitting control: role checkbox, radio, option, combobox, switch, tab, menuitem(radio),
      gridcell (date pickers), and only if not clicked before in this run for checkbox/switch;
    - an "Open <field>" click on a field not yet filled or excluded;
    - an upload control (attach/upload/resume/CV/cover letter), answered through the file-chooser handler;
    - a consent/dismiss control (accept|agree|ok|got it|close|dismiss|accepter|j'accepte|fermer|
      tout accepter|alle akzeptieren|schließen).
    A strict submit word in the full name → stop at `submit_point` with that action. Any other click
    → stop with `stop="ready"` and `submit_action = submit_for_done(...)`. `submit_target` alone never
    stops an advance control (the model cannot tell "Next" from the final submit).
16. **Form complete.** On a form page, when no `fill` target is left and every matching upload is done,
    a DONE, a WAIT, or three unchanged steps end the run with `stop="ready"` instead of `HarnessStuck`.
17. **Outside a form context** (job description, cookie wall, landing page) clicks are allowed
    except strict submit words.
18. **Clicks are recorded.** `HarnessResult.clicked` counts executed clicks; every `HarnessBlocked` /
    `HarnessStuck` carries `.result`. The runner falls through to other tiers only when no click on a
    submits=True control happened after anything was typed.

## Status and measured results (2026-11-15)

Built on branch `feat/openjev-harness`. Implementation by Codex (GPT-6 Astra) task by task; every
task reviewed by an Opus critic, findings fixed test-first; the click guard got three review passes.

Live runs, OpenJev-4B (8-bit, RTX 5060 on mouad-ba), headless Chromium, test identity
(`Test Candidate`, `test.candidate@example.com`), nothing submitted. On real sites every
non-read request was aborted unless it was page loading or telemetry (scratchpad `live/live_real.py`).

| Page | Result | Time |
|---|---|---|
| Fixture: Greenhouse-like single page | all 5 fields, CV attached, stopped before Submit (0 submit clicks) | 17 s |
| Fixture: Easy-Apply-like two steps | 3 fields, "Next", select, written answer, stopped before "Submit application" | 9.5 s |
| Fixture: form in iframe / in shadow root | fields filled, CV attached, stopped | 5 s each |
| Fixture: ARIA combobox | typed city, picked "Paris" | 4 s |
| Fixture: cookie banner + form | banner accepted, form filled, stopped | 18 s |
| Fixture: confirmation page | `already_received` | 0.6 s |
| Real Greenhouse (Mirakl) | 6 text fields, CV, 3 yes/no dropdowns, LinkedIn; left salary, AI-experience question, privacy, country, hybrid policy for the owner; stopped at "Submit application" | 71 s |
| Real Ashby (Pennylane, French) | names, email, phone, LinkedIn, 2 written answers, CV; left salary and region; stopped at "Submit Application" | 33 s |
| Real SmartRecruiters (Dataiku) | site's bot wall ("Access is temporarily restricted") for headless Chromium → BLOCKED → falls back; not bypassed | 6 s |

Writing model through the CLIs, no API key: Claude 2–3 s, Codex 3–5 s per answer; both answer
"NONE" for salary questions, which leaves the field for the owner.

Known limits: a `<select>` whose change event submits the form is not detected; dropdown/radio answers
the model picks are listed for review ("Chosen") but can be claims (e.g. right to work); headless
browsers meet bot walls on some ATS (the production path is the owner's real JobPilot Chrome);
`DECISION_MODEL_URL` pointing at hosted Jev sends the goal's personal data to TypeSafe.
