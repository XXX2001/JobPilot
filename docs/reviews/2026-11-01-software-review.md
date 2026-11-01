# JobPilot software review and intelligence improvement plan

Review date: 1 November 2026. Reviewed revision: `8c5123a09313fd9eedac60cca1c17c4a0c0a8c8d`.

JobPilot has an appropriate stack for its stated goal: a self-hosted, single-user assistant for discovering jobs, evaluating fit, tailoring documents, and helping the user apply. Its main weakness is the gap between the certainty communicated to the user and the evidence established by the implementation. Preserve the core stack; prioritize reliable outcomes, factual document edits, and measurable reductions in repeated inference.

This review consolidates the software critique and intelligence, latency, and token recommendations from the project discussion. Findings describe the reviewed revision. Proposed changes are not implemented by this document.

The [engineering improvement and cleanup plan](2026-11-01-improvement-plan.md) translates these findings into implementation work, dependencies, acceptance tests, migrations, and release gates.

## Evidence and validation

Evidence labels used below:

- **Reproduced** means a local, isolated probe demonstrated the behavior.
- **Code confirmed** means the execution path establishes the issue; its live external consequence was not exercised.
- **Design concern** means a limitation or tradeoff requiring a product or architecture decision.
- **Proposal** means an improvement whose benefits require evaluation.

Local checks during the review:

| Check | Result |
| --- | --- |
| Backend tests excluding live integration tests | 635 passed, 5 skipped, 14 warnings |
| Frontend Vitest tests | 50 passed across 5 files |
| Frontend type checking with warnings treated as failures | 0 errors, 0 warnings |
| Frontend production build | Passed |
| Backend Pyright | 0 errors, 8 warnings |
| Backend Ruff | 217 violations |

The backend command was `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ANONYMIZED_TELEMETRY=false uv run pytest -p pytest_asyncio.plugin -m 'not integration' --ignore=tests/test_local_provider_smoke.py -q`. Plugin autoload was disabled to isolate the run from unrelated tools installed on the host. External calls in the full-pipeline integration tests are mocked. These results do not establish reliability against live job boards, real model responses, or a deployed Docker browser workflow.

The three initial targeted probes reproduced the job/document association error, an onsite job passing the remote-only preference, and an invented qualification passing the CV replacement validator. No real applications were submitted.

Nine additional isolated scenarios exercised real persistence/API functions with temporary SQLite databases, stubbed browser actions, or an in-process WebSocket client:

| Scenario | Observed result |
| --- | --- |
| Manual browser opening returns `False` | Response says opened; application and match become `applied` |
| Assisted strategy reports prefilled only | Application and match become `applied` |
| Configured limit is 1 and usage is already 1 | Default engine permits another application; reported usage becomes 2 of 1 |
| Application task raises `CancelledError` | Pending reservation remains, usage is 1, live registry is empty |
| Two applications interleave browser creation | Both contexts capture browser B; A is never closed and B is closed twice |
| Newest job fails score filter and an older job passes | First page with limit 1 is empty; total still reports 2 |
| Base CV points to an existing absolute `.tex` path | Apply route passes the `.tex` path as `cv_pdf` |
| WebSocket client supplies an untrusted Origin | Connection accepted and confirmation handler invoked; engine also accepts confirmation before a review exists |
| Forced document regeneration with one existing document | Response says queued, zero tasks scheduled, zero document rows remain |

The concurrent-browser probe controls task interleaving with fake handles; it establishes incorrect ownership in the engine without launching real browsers. The WebSocket probe establishes server-side acceptance and dispatch, not the behavior of every browser's private-network restrictions. Migration failure handling, ignored CV choice, dependency telemetry, and the other code-confirmed findings below were inspected without exercising real external services.

P1 findings should be corrected before relying on the affected workflow. P2 findings are important correctness or operational work for the next hardening iteration. A priority is conditional on using the affected feature.

## Technology choices to retain or constrain

| Technology | Assessment and direction |
| --- | --- |
| Python, FastAPI, Pydantic | Appropriate for API integration, document processing, and asynchronous browser workflows. Keep the stack and strengthen domain contracts. |
| SQLite, SQLAlchemy, Alembic | Appropriate for one local user. Migrations, foreign keys, uniqueness constraints, and atomic quota reservations are useful foundations. Improve recovery and transaction semantics before changing databases. |
| SvelteKit, TypeScript, Tailwind | Sensible interface stack. Static output served by FastAPI keeps distribution simple. Runtime contracts and component behavior still need verification even when type checking passes. |
| Playwright, browser-use, Scrapling | Useful capabilities with a large integration burden across arbitrary sites. Support a small, explicit source and application-form set first; make fallbacks visible. |
| LaTeX and Tectonic | Good for preserving an existing CV layout. For broader adoption, keep structured resume facts as the source of truth and use LaTeX as a rendering option. |
| Embeddings | Useful for related-skill retrieval. Similarity alone does not establish competence, seniority, eligibility, or recruiting outcomes. |
| Provider abstraction | Worth keeping, with tested capabilities per role. The browser factory's claim that browser-use has no `ChatAnthropic` conflicts with the installed, locked browser-use 0.13.1 package. Correct the capability matrix and adapter assumptions. |

Useful existing engineering includes subsystem boundaries, migrations and database constraints, provider adapters, document diffs, an explicit application lifecycle model, bounded inference concurrency, cheap-first scraping, embedding batches, and a fit-assessment path that skips redundant job analysis.

References: [dependencies](../../pyproject.toml), [provider factory](../../backend/llm/factory.py), [database](../../backend/database.py), [frontend adapter](../../frontend/svelte.config.js).

## Product critique

The current scope combines discovery, ranking, document generation, arbitrary website interaction, Gmail classification, analytics, and tracking. Each adds distinct failure modes. The first dependable experience should be a useful shortlist, factual document suggestions, a reviewed application, and confirmed tracking.

Make assisted application the default until supported flows have measured success rates. Distinguish observed facts, inferred fit, missing information, and verified external outcomes throughout the interface. Keep a qualification gap distinct from a presentation gap: rewriting a CV cannot supply a missing qualification.

Choose a clear interaction model for local desktop use and container hosting. A browser running in a server or container is not automatically visible to the person using the web interface. Downloads and manual browser opening should reach the client the user is actually operating.

Measure relevance, factual-edit errors, confirmed submission rate, time saved per completed application, and AI cost. Track interview or callback outcomes as delayed, noisy product signals; they do not directly establish that a particular rewrite caused success.

## Initial correctness and trust findings

### F01 Job details can be paired with another match

**Priority P1. Reproduced.** `_store_matches()` omits applied or skipped jobs, but the assessment and generation paths pair the returned IDs with the original ranked sequence by position. If A is skipped before B, B can receive A's assessment and tailored document. The probe produced a Rust Engineer match paired with Python Engineer details.

Return explicit `(match_id, job_details)` records and carry that identity through assessment, generation, and persistence. Add a regression with a skipped first item and multiple eligible items, checking the final persisted document association.

Code: [batch runner](../../backend/scheduler/batch_runner.py), especially `_store_matches`, `match_to_jd`, and the document-generation `pairs` construction.

### F02 Submission success lacks destination evidence

**Priority P1. Code confirmed.** The Playwright path returns `applied` immediately after `page.click`. The browser-use path returns `applied` after `submit_agent.run()` without inspecting its result. Validation errors, another form page, or an unsuccessful agent outcome can therefore produce a local success record.

Represent submission attempted, confirmed, failed, and needs verification separately. Persist evidence such as a destination confirmation message, receipt, or application identifier. Agent completion or agent-reported success alone is insufficient. [Browser Use output documentation](https://docs.browser-use.com/open-source/customize/agent/output-format).

Code: [form filler](../../backend/applier/form_filler.py), [auto strategy](../../backend/applier/auto_apply.py).

### F03 User corrections do not reliably gate submission

**Priority P1. Code confirmed.** `_apply_patches()` logs failed corrections and continues to submission. A correction the user approved can therefore fail to reach the actual form. The Tier 2 browser path also stores the patch callback but does not consume it before its submission agent runs.

Read back corrected fields and require them to match the approved values before submitting. Return to review if a correction fails. Bind approval to the reviewed application, document versions, and current form values.

Code: [form filler](../../backend/applier/form_filler.py), [auto strategy](../../backend/applier/auto_apply.py), [engine review state](../../backend/applier/engine.py).

### F04 Container networking defeats the local access assumption

**Priority P1. Code confirmed configuration.** The application has no authentication while Compose publishes `8000:8000`, normally exposing the service on all host interfaces. Actual reachability depends on the host network and firewall. CORS is not authentication.

Default local installs to `127.0.0.1:8000:8000`; require an authenticated access design for remote hosting. Docker documents the distinction between unrestricted publication and localhost binding. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/).

Code: [Compose](../../docker-compose.yml), [application middleware](../../backend/main.py).

### F05 Visible browser flows are incomplete in Docker

**Priority P1 for the recommended Docker workflow. Code confirmed design mismatch.** Tier 1 form filling hardcodes `headless=False`; the image contains no display or remote viewing interface. Tier 2 defaults to a visible application browser as well. A running HTTP service does not establish that these application workflows work in a normal headless container.

Separate supported desktop and container modes. Honor browser configuration consistently and provide an explicit interaction path for login, CAPTCHA handling, and review. Add a real container smoke test covering browser launch and a local fixture form. [Playwright CI guidance](https://playwright.dev/python/docs/ci).

Code: [form filler](../../backend/applier/form_filler.py), [configuration](../../backend/config.py), [Dockerfile](../../Dockerfile).

### F06 CV validation permits unsupported factual changes

**Priority P1. Reproduced with synthetic model output.** Validation checks model-supplied confidence, source-text presence, and new LaTeX commands. A replacement changing two years of experience into ten years plus a PhD passed. This proves the validator lacks a factual boundary; it does not measure how frequently a live model invents claims.

Store verified facts and evidence spans. Lock dates, employers, qualifications, and experience amounts in code; require explicit review for substantive changes. Generated statements should reference supporting fact IDs. Formatting validity and model confidence are not factual verification.

Code: [CV applicator](../../backend/latex/applicator.py), [replacement schemas](../../backend/llm/validators.py).

### F07 Mandatory preferences behave as score adjustments

**Priority P1 for explicit exclusions. Reproduced.** An onsite Python job scored approximately 75 with `remote_only=True`, exceeding the default threshold of 30. The location mismatch removes points instead of excluding the job.

Apply explicit constraints before ranking. Distinguish incompatible from unknown when the posting lacks information. Preserve soft preferences for factors that may legitimately compensate for each other.

Code: [matcher](../../backend/matching/matcher.py), [filters](../../backend/matching/filters.py).

### F08 The ATS label overstates what the score establishes

**Priority P2. Design concern supported by the formula.** The simulated ATS score is `(1 - weighted_embedding_gap) * 100`. It is not a measured ATS outcome. Fixed cosine thresholds also require evaluation across embedding models, languages, and skill vocabularies.

Present a skill coverage estimate with evidence, missing requirements, and unknowns. Evaluate against 50 to 100 manually reviewed representative postings, including French and English if both are target markets. Track shortlist quality and eligibility mistakes separately from formatting or parsing checks.

Code: [fit engine](../../backend/matching/fit_engine.py), [skill patterns](../../backend/matching/skill_patterns.py).

## Additional software and code findings

### F09 Regeneration reports queued without scheduling work

**Priority P1. Reproduced.** `regenerate_documents()` accepts `BackgroundTasks` but never adds a task or invokes a pipeline. With `force=True`, it deletes existing CV and letter records before returning `queued`. The probe observed zero scheduled tasks and zero remaining document rows. Files are not deleted by this function, but the normal document endpoints lose their records.

Actually schedule durable work and return its identity. Keep the last working document until a replacement has compiled and been persisted successfully. Test a successful regeneration through to a retrievable PDF, and test failure preserving the old document. The separate letter-only regeneration route has a real implementation; it does not repair this generic route.

Code: [document regeneration](../../backend/api/documents.py), `regenerate_documents` at lines 290 to 324.

### F10 The Use base CV decision is discarded

**Priority P1. Code confirmed.** `CVReviewPanel` includes both approved and base-CV decisions in `approvedMatches`, then sends only `{method}` to the apply endpoint. `ApplyRequest` has no document-selection field, and the backend resolves the latest tailored document first. When a tailored CV exists, selecting Use base CV therefore does not select the base document for application.

Persist the user's document choice and pass an explicit immutable document/version ID or a validated base-CV selection to the apply workflow. Test the final file supplied to the browser after declining a tailored CV. Rejected edits must not reappear through a fallback.

Code: [CV review panel](../../frontend/src/lib/components/CVReviewPanel.svelte), `approvedMatches` and `runApplications`; [apply endpoint](../../backend/api/applications.py), `ApplyRequest`, `_resolve_documents`, and `apply_to_job`.

### F11 WebSocket confirmation has no origin or review boundary

**Priority P1. Reproduced at the server boundary.** The WebSocket route accepts an arbitrary Origin and forwards recognized commands without authentication. `signal_confirm()` sets any live application's event even if no pending review exists. An in-process client with `Origin: https://untrusted.example` was accepted and reached a confirmation handler. A separate engine probe confirmed that a live operation can be approved before its review is created. Browser and network restrictions may affect external reachability; the server itself enforces neither boundary.

Validate Origin and the local authenticated session or per-installation token. Validate inbound command schemas and require a review ID/version matching an operation currently awaiting confirmation. Make approval an acknowledged, idempotent operation. Do not let an early signal act as advance approval of a future form.

The frontend compounds delivery uncertainty: `send()` silently does nothing while disconnected, but `confirmApply()` immediately dismisses the modal and removes its stored pending ID. Preserve review state until the server acknowledges the intended action. Reconnect recovery can help recover a pending review, but it is not delivery acknowledgment.

Code: [WebSocket route](../../backend/api/ws.py), [command wiring](../../backend/main.py), [engine](../../backend/applier/engine.py), [frontend socket store](../../frontend/src/lib/stores/websocket.ts), [queue page](../../frontend/src/routes/queue/+page.svelte).

### F12 Manual and assisted preparation becomes a completed application

**Priority P1. Reproduced.** `SUCCESS_RESULT_STATUSES` includes `manual` and `assisted`. The recorder normalizes both to `applied`, timestamps them, and marks the job match applied. Manual mode only opens a URL; assisted mode explicitly leaves submission to the user. Even a `False` return from `webbrowser.open()` produces an opened message and a persisted applied record.

Represent opened, prefilled, awaiting user submission, and confirmed submitted separately. Require a user completion action or independent destination evidence before updating application analytics and removing the job from actionable results. Check the boolean result of `webbrowser.open()` and return a client-facing link for remote/container use.

Code: [status vocabulary](../../backend/applier/__init__.py), [recorder](../../backend/applier/recorder.py), [manual strategy](../../backend/applier/manual_apply.py), [assisted strategy](../../backend/applier/assisted_apply.py).

### F13 Configured quotas differ from enforced quotas

**Priority P1. Reproduced.** Startup constructs the engine with the constant daily limit, while the UI limit endpoint and batch runner read `SearchSettings.daily_limit`. The probe configured a limit of one, inserted one completed application, and the default engine allowed a second. Increasing the UI limit also cannot raise the engine's fixed limit without separate wiring.

Use one policy resolver at reservation time, and keep the count-and-reserve operation atomic. Define the date boundary consistently: the guard uses local `date.today()`, whereas the limit-status endpoint uses UTC. Test changed settings and midnight boundaries through the actual apply route.

Code: [startup](../../backend/main.py), [engine](../../backend/applier/engine.py), [daily limit](../../backend/applier/daily_limit.py), [limit status](../../backend/api/applications.py).

### F14 Concurrent applications share mutable browser ownership

**Priority P1 when applications overlap. Reproduced with deterministic stubs.** The engine owns one auto strategy and one assisted strategy. Each stores its live browser in a single `_active_browser` attribute. Different jobs may run concurrently because the registry only blocks the same match. The dispatch cleanup later reads that shared attribute.

The controlled interleaving created A, then B, then returned both operations. Both contexts captured B; cleanup closed B twice and never closed A. This can close another application's browser or leak the first one.

Give each application operation its own strategy context and browser handle. Return or register ownership per attempt, with cleanup in the owning scope. Consider per-profile locking for persistent browser directories as a separate constraint.

Code: [engine strategy construction and dispatch](../../backend/applier/engine.py), [auto strategy](../../backend/applier/auto_apply.py), [assisted strategy](../../backend/applier/assisted_apply.py).

### F15 Cancellation leaves a durable pending reservation

**Priority P2. Reproduced.** `CancelledError` bypasses the statechart's `except Exception` compensation. The engine catches `BaseException` to close a browser and clears its in-memory registries, but does not reconcile the committed quota reservation. The probe ended with a pending application consuming one slot and no live operation.

Use cancellation-safe finalization and persisted attempt phases. Release a slot when submission is known not to have occurred. If cancellation occurs during a possibly completed remote submission, retain a needs-verification record rather than blindly releasing and retrying. Add startup reconciliation for interrupted attempts.

Code: [engine abnormal exit](../../backend/applier/engine.py), [statechart](../../backend/applier/state.py), [reservation](../../backend/applier/daily_limit.py).

### F16 The base document fallback can upload a source file

**Priority P2. Reproduced for an absolute path.** The apply endpoint assigns an existing `base_cv_path` directly to `cv_pdf`, although that profile field commonly names a LaTeX source. The probe passed a `.tex` file to the engine as the CV attachment. Relative paths are also resolved inconsistently: the uploader stores paths relative to the data directory, but this fallback checks them relative to the process working directory.

Centralize resolution and distinguish template sources from compiled artifacts in the data model. Require an existing validated PDF before filling an upload field. Reuse a compiled base PDF or compile it explicitly; do not select an unrelated alphabetically first PDF.

Code: [apply fallback](../../backend/api/applications.py), [CV uploader](../../backend/api/settings.py), [document path resolution](../../backend/api/documents.py).

### F17 Job filtering occurs after pagination

**Priority P2. Reproduced.** `list_jobs()` paginates all jobs, loads their latest scores, then filters the page by `min_score`. Its total counts all jobs. With a low-scoring newest job, a high-scoring older job, and limit one, the first page was empty and total was two.

Join the selected latest match score, filter in SQL, and apply the same predicate to the count before pagination. Define a deterministic tie-breaker for latest matches. Test page contents, counts, and navigation together.

Code: [job listing](../../backend/api/jobs.py), `list_jobs`.

### F18 State transitions do not surround their named operations

**Priority P2. Code confirmed architecture concern.** CAPTCHA_CHECK, FILLING, AWAITING_CONFIRM, and SUBMITTING are pass-through states. The real browser workflow executes inside RECORDING via `_dispatch`. The enum sequence therefore does not describe what the operation is doing at that moment, and hooks on the named phases cannot enforce those phases' invariants.

Make transitions own actual actions: prepare, fill, validate, await approval, submit, verify, persist. Persist state changes before external side effects where needed. Alternatively simplify the current outer wrapper until real phase ownership is implemented. Avoid maintaining two competing lifecycles in the engine and strategies.

Code: [transition builder](../../backend/applier/engine.py), `_build_transitions`; [statechart](../../backend/applier/state.py).

### F19 Preview can still spend model tokens

**Priority P2. Code confirmed conditional behavior.** The preview route and batch docstrings promise no LLM calls. They run the normal scraper before the dry-run cutoff. For configured browser sources, Tier 1 extraction calls a model and Tier 2 runs an agent. Preview skips assessment/document generation, but that does not make discovery free of inference. The existing dry-run test uses a fake scraper and cannot establish that promise.

Define preview explicitly as either cached/API-only with no inference, or live discovery with a stated budget. Thread that policy into the scraper and test it with an instrumented inference client that fails if called in a no-inference mode.

Code: [queue refresh](../../backend/api/queue.py), [batch dry run](../../backend/scheduler/batch_runner.py), [orchestrator](../../backend/scraping/orchestrator.py), [Tier 1 extraction](../../backend/scraping/scrapling_fetcher.py).

### F20 Database migration errors do not abort startup

**Priority P1 for upgrades. Code confirmed.** `init_db()` propagates migration errors, but the application lifespan catches all exceptions from it, logs a message, and continues initialization. The database helper's promise that migration failure aborts startup is therefore not enforced by the caller. An existing database with a failed upgrade can leave routes using the wrong schema.

Fail startup on required schema failures and report a concrete migration error. Keep optional integrations separate from mandatory initialization. Test an injected migration failure as well as singleton-construction failure; the current lifespan test covers the latter.

Code: [lifespan](../../backend/main.py), lines 87 to 94; [database initialization](../../backend/database.py); [lifespan tests](../../tests/test_main_lifespan.py).

### F21 Dependency telemetry contradicts the privacy promise

**Priority P1 for the stated local privacy guarantee. Code and upstream documentation confirmed.** The locked browser-use 0.13.1 package defaults `ANONYMIZED_TELEMETRY` to true. Its agent telemetry includes `task=self.task`, visited URLs, action history, and results, and its telemetry service forwards those properties to PostHog. JobPilot embeds applicant details in browser-agent tasks and does not set a project-level opt-out in the reviewed startup/Compose configuration.

Consequently, the README claim that outbound data goes only to the configured AI and job-search APIs is not supported by the default dependency configuration. This review did not transmit applicant data to verify delivery. Disable telemetry before importing the library in local-privacy mode, inspect other optional outbound integrations, and document the actual data flow. Browser Use documents the telemetry contents and `ANONYMIZED_TELEMETRY=false` control. [Browser Use telemetry](https://docs.browser-use.com/open-source/development/monitoring/telemetry).

Code: [locked dependency](../../uv.lock), [agent task construction](../../backend/applier/auto_apply.py), [configuration](../../backend/config.py), [Compose](../../docker-compose.yml), [privacy statement](../../README.md). Dependency implementation inspected: `browser_use/config.py`, `browser_use/agent/service.py`, and `browser_use/telemetry/{service,views}.py` in the installed locked package.

### F22 Search settings are not consistently wired into matching

**Priority P2. Code confirmed.** Search settings expose experience bounds, job types, and languages. The batch creates `JobFilters` without those fields; the matcher also does not consume `job_types` or `languages`. Stored settings can therefore suggest controls the ranking pipeline does not enforce. Extracted job knockout filters likewise are not consumed by `FitEngine.assess`.

Define which preferences are supported, map all supported settings into the same domain policy, and show unsupported filters explicitly until implemented. Test settings changes through a real batch to resulting eligibility and ranking, not just round-trip storage.

Code: [settings schemas](../../backend/api/settings.py), [batch filter construction](../../backend/scheduler/batch_runner.py), [filters](../../backend/matching/filters.py), [matcher](../../backend/matching/matcher.py), [fit engine](../../backend/matching/fit_engine.py).

## Code quality and maintainability assessment

The strongest design is the modular monolith with one database and explicit external-service adapters. The weakest design is the contract between user intent, workflow state, and persistence. The recurring issue is that successful function execution or HTTP delivery is treated as successful completion of the user's action.

- **Domain outcomes need explicit types.** The CV review panel ignores the apply response body and treats any HTTP 200 as success, although the endpoint returns `cancelled` and `failed` outcomes with 200. Batch completion reports the number of selected top IDs as ready even when document generation failed. Render domain outcomes and counts of verified-ready artifacts.
- **User decisions need durable identity.** Document selection and form approval should reference an application attempt and exact document/form versions. Keep job IDs, match IDs, and attempt IDs distinct. Current names such as `job_id` sometimes mean match ID, increasing the risk of positional or cross-layer mistakes.
- **Boundary validation is incomplete.** Frontend `asWSMessage` checks the type discriminator, then casts; it does not validate the full payload. The store uses `any[]`, which permits StatusBar branches for message types the backend does not emit. Inbound WebSocket commands are raw dictionaries. Use shared/generated schemas plus runtime validation at network boundaries.
- **Policy is duplicated.** Quota resolution, status vocabulary, document paths, and CV source selection appear in several layers and have already diverged. Centralize each domain rule, with routes translating requests and orchestrators owning the operation.
- **Recovery is weaker than the happy path.** In-memory events, pending reviews, source health, and background task state vanish on restart. Track background tasks, cancellation, and cleanup explicitly. Persist only the state needed to recover safely; retain per-operation browser ownership.
- **Prompt filtering does not enforce execution permissions.** Regex removal of suspicious phrases is a useful input layer, but website text must not gain authority over browser actions. Keep domain, upload, and final-submit policies in execution code. Test adversarial page fixtures against fake tools and require exact review-state approval.
- **Initialization and fallback code are too permissive in some places.** Required dependency fallbacks and broad catches can turn initialization defects into incomplete features. Fail clearly for mandatory components and degrade explicitly for optional integrations.
- **Documentation and tests sometimes preserve implementation assumptions.** The ATS label, no-LLM preview claim, provider capability matrix, fake lifecycle phases, and regeneration response all overstate actual behavior. Remove obsolete ticket narratives and references to missing reports when they obscure current contracts. Describe invariants and move history to version control.
- **Quality gates need to match reality.** Ruff is not enforced in CI despite the code-review guide's clean-lint requirement. CI still allows 19 Pyright errors although the reviewed code reports zero. Reduce the ceiling after establishing the clean baseline; address the 217 lint violations without mixing broad formatting into correctness fixes.

Sources: [CV review panel](../../frontend/src/lib/components/CVReviewPanel.svelte), [batch completion](../../backend/scheduler/batch_runner.py), [WebSocket types](../../frontend/src/lib/types/ws.ts), [socket store](../../frontend/src/lib/stores/websocket.ts), [StatusBar](../../frontend/src/lib/components/StatusBar.svelte), [sanitizer](../../backend/security/sanitizer.py), [CI](../../.github/workflows/ci.yml), [review guide](../code-review.md).

## Tests that would improve confidence

Preserve the existing unit and migration tests. Add behavior tests at the boundaries where assumptions currently escape:

1. Mixed skipped and eligible jobs retain the right job identity through persisted assessments and documents.
2. Declining tailored edits selects the base PDF actually uploaded; a failed patch blocks submission.
3. Manual opening and assisted prefilling remain incomplete until completion is confirmed.
4. Submission validation errors, ambiguous agent completion, cancellation, and restarts never silently become confirmed success.
5. Configured quota changes affect the reservation used by the apply route, including concurrency and day boundaries.
6. Two simultaneous applications own independent browser handles and clean up exactly once.
7. Regeneration produces a retrievable replacement and preserves the previous artifact on failure.
8. WebSocket origin/session checks, premature approval, disconnected confirmation, duplicate commands, and stale review versions are exercised.
9. A no-inference preview makes zero model calls through the real orchestrator; ordinary live preview reports its actual budget.
10. A built container can launch the supported browser mode and complete a local fixture form with an explicit confirmation page.
11. A fixed, labeled job/CV set measures ranking, unknown handling, French/English extraction where relevant, factual edits, and cost together.
12. Privacy-mode subprocess tests intercept outbound telemetry attempts without sending real data.

Avoid tests that only assert HTTP 200, a queued string, or a mock's successful return. Verify the resulting artifact, persisted outcome, and final operation arguments. Existing passing tests are useful coverage, but the reproduced cross-layer defects show that they are not an acceptance test for the product promise.

## Proposals for intelligence and efficiency

### I01 Store reusable candidate evidence

Parse the CV into verified facts linked to original source spans and stable evidence IDs. Retrieve relevant facts for each job requirement and classify the relationship as supported, related experience, missing, or unknown. Give the writer relevant facts and editable paragraphs instead of repeatedly interpreting the entire CV.

Expose useful advice such as an existing accomplishment that supports a requirement but is poorly emphasized. Retrieve with high recall and preserve access to the full source when evidence is ambiguous.

### I02 Escalate reasoning only when it can change a decision

Use deterministic constraints, then inexpensive semantic judgments, then deeper analysis for promising but uncertain cases. Unknown salary or unclear remote policy should remain unknown until evidence supports a decision.

TypeSafe/Jev is an optional candidate for narrow judgments behind the existing provider boundary. Its [extraction cascade](https://docs.typesafe.ai/cookbooks/sde_cascade) illustrates small-model extraction, verification, and selective escalation. Batch independent questions over shared evidence; measure the verifier's extra latency and tokens. Calibrate thresholds on JobPilot examples. Typed outputs and probability concentration do not guarantee truth or authorize submission. [Confidence guidance](https://docs.typesafe.ai/confidence).

### I03 Reuse separate ranking dimensions

Persist technical fit, seniority fit, work preference, location compatibility, and evidence strength as separate judgments. Combine them in code so changes in user preferences can reorder existing jobs without new inference. Keep mandatory constraints outside the compensating weighted score. [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring).

Learn from explicit reasons such as too managerial, wrong industry, or interesting work. Treat unopened jobs as weak feedback. Ask a clarification when one answer would resolve uncertainty across many postings, and update only dependent judgments.

### I04 Prefer approved content operations before generating prose

Choose between reusing an approved variant, reordering skills, selecting supported bullets, editing one paragraph, and requesting missing information. The first three can often run in code. Maintain evidence-backed variants for recurring role families and reassess the specific posting before reuse. Keep company details and job identity separate from reusable prose.

Generate only when an edit can improve the presentation of actual qualifications. Preserve the current skip-analysis path where valid. A qualification gap should not automatically trigger a rewrite.

### I05 Show useful results before preparing every document

Stream the shortlist as sources complete and prepare one or two likely selections in the background. Generate remaining documents when selected. Bound concurrency per resource and provider. Separate time to first useful shortlist from total batch duration. The current batch pre-generation count follows remaining daily application slots, which does not establish which documents the user will use.

### I06 Learn validated form mappings

Extract a compact field inventory including stable local IDs, labels, types, required state, options, and autocomplete hints. Resolve obvious mappings in code, ask semantic questions about ambiguous fields, and let code supply selectors and applicant values. Include a no-match outcome.

Save validated mappings by site and form structure. Verify the current structure and values before reuse, send changed fields for reanalysis, and retain review and submission verification. The current Markdown cleaning path can discard input elements. Do not replay old approval or blindly reuse selectors after a page changes.

### I07 Cache according to actual dependencies

| Artifact | Required dependencies |
| --- | --- |
| Parsed CV facts | CV content and parser version |
| Embeddings | Exact embedded text, endpoint identity, model/version, normalization and dimension configuration |
| Candidate-specific job analysis | Job content, candidate facts, analysis prompt/schema and model version |
| Tailored document | Job identity where relevant, selected evidence, approved edits, template and policy versions |
| Compiled PDF | Source and supporting files, compiler/toolchain and compilation settings |
| Form mapping | Site and structural fingerprint, relevant form semantics, mapping version |

The current CV parser produces a hash but rebuilds profiles with empty embeddings. The context cache uses only job ID and a one-hour TTL; batch-created `JobDetails` do not carry the database ID, bypassing that cache. Existing IDs also do not invalidate candidate-specific analysis when the CV changes. The base-CV path recompiles unchanged output for each job.

Reuse exact validated results when dependencies are unchanged. Similar jobs can share reusable features and approved prose, but not automatically share job-specific conclusions or submission state. Equal embedding dimensions do not establish the same embedding space.

Code: [CV parser](../../backend/matching/cv_parser.py), [embedder](../../backend/matching/embedder.py), [document pipeline](../../backend/latex/pipeline.py), [batch conversion](../../backend/scheduler/batch_runner.py).

### I08 Use provider capabilities to reduce format retries

The OpenAI-compatible adapter requests JSON mode and retries the full prompt after validation failure. Use strict schemas where the configured endpoint supports them; retain capability-aware fallbacks, bounded repair, and factual validation. JSON validity alone does not establish schema adherence. [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Maintain stable reusable prompt prefixes and measure actual cache hits. Prompt caching reuses eligible prefix computation while still generating an answer; application caching can eliminate the request. Model-specific retention and billing rules require checking for the chosen provider. Do not pad prompts solely to chase caching. [Prompt caching documentation](https://developers.openai.com/api/docs/guides/prompt-caching).

### I09 Measure inference and workflow outcomes

Preserve provider usage metadata; the current adapters discard it when returning text. Record operation, model, prompt version, input/output and cached tokens where available, latency, retries, browser steps, and result quality. Keep sensitive CV and mailbox content out of routine metrics.

Evaluate fixed representative jobs before and after optimization. Report token use and latency alongside shortlist relevance, missed eligible jobs, unsupported CV claims, and verified submissions. Compare warm and cold caches, local and hosted models, and p50/p95 latency. No percentage savings have been measured in this review.

### I10 Persist workflow checkpoints and reviewed state

Keep job identity, document versions, review decisions, pending steps, and externally verified outcomes durable. Resume safely after a restart and reconcile ambiguous submissions before retrying. A local SQLite-backed workflow record is sufficient to start; a distributed task platform is not a prerequisite for this single-user product.

## Suggested implementation order

1. Correct job/document identity and user document selection, regeneration, application status semantics, approval enforcement, quota policy, and privacy/deployment defaults.
2. Add targeted regressions and real local fixture forms for these failure modes; fix browser ownership, fail on migration errors, and reconcile interrupted operations safely.
3. Record usage and latency, then add persistent dependency-aware caches and compiled-document reuse.
4. Introduce verified candidate evidence, explicit eligibility constraints, and evaluated fit dimensions.
5. Add selection-driven tailoring and selective escalation, measuring quality and savings together.
6. Experiment with learned form mappings and feedback-driven ranking after the supported flows are dependable.

Initial design targets are zero repeated inference for unchanged evidence, instant reranking after a weight change, and generation concentrated on documents the user actually uses. Fresh source checks, changed evidence, model updates, and expired external state still require work.
