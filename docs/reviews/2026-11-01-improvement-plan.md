# JobPilot engineering improvement and cleanup plan

Date: 1 November 2026. Status: proposed implementation backlog.

Based on the [software review](2026-11-01-software-review.md) of revision `8c5123a09313fd9eedac60cca1c17c4a0c0a8c8d`. The findings remain the evidence record; this plan defines the work, dependencies, tests, and release decisions needed to address them. None of the proposed implementation is marked complete.

Keep the existing Python, FastAPI, SvelteKit, SQLite, and LaTeX stack. First make user choices and recorded outcomes reliable. Then make workflows recoverable, measure their costs, remove repeated work, and improve the quality of matching and document suggestions. Cleanup should support these changes through small, reviewable patches.

Navigation: [phases and dependencies](#delivery-phases-and-dependencies) · [implementation backlog](#phase-0-establish-the-baseline-and-contain-known-problems) · [migrations](#database-and-artifact-migration-plan) · [cleanup](#cleanup-execution-rules) · [validation](#validation-and-ci-plan) · [measurement](#measurement-and-optimization-acceptance) · [releases](#release-sequence-and-rollback-gates) · [effort](#estimated-effort-and-capacity) · [first PRs](#first-implementation-sequence) · [traceability](#traceability-to-the-review).

## Scope and delivery assumptions

The primary product remains a single-user application running locally, with an explicitly supported container mode. The first dependable journey is: discover a useful shortlist, understand fit and uncertainty, choose factual documents, review the actual application, and track a confirmed result.

This plan assumes one experienced engineer with periodic product decisions and code review. Ownership labels below describe responsibilities, not staffing commitments. The maintainer can fill several roles. Work on unrelated Gmail features, additional job boards, and cosmetic redesign should wait until the core journey passes its acceptance gates.

Remote multi-user hosting, a database replacement, microservices, a distributed workflow platform, a vector database, and a provider migration are outside the initial scope. Introduce infrastructure only when a measured requirement justifies it. New AI providers and learned browser mappings are experiments with their own acceptance criteria.

The plan covers all 22 findings and all 10 improvement proposals in the review. Work item identifiers W01–W29 are intended for issues, pull requests, and release tracking.

## Outcomes and engineering invariants

These rules define correctness across modules. Each must have an observable acceptance test.

| Invariant | Required behavior |
| --- | --- |
| Job identity survives every stage | Discovery, scoring, document generation, and application records refer to the same explicit job and match IDs. |
| The selected document is the uploaded document | Application attempts reference immutable artifact versions and content hashes; fallback never overrides a user's rejection. |
| Approval authorizes one reviewed state | Approval names an attempt and review version, including destination, documents, and field values. Changes invalidate it. |
| Preparation is distinct from submission | Opening a link, filling a form, HTTP success, and agent completion cannot establish submission. |
| Submission has an evidence source | Destination confirmation, explicit user attestation, and unknown historical status remain distinguishable. |
| Ambiguity does not cause a duplicate submission | A timeout or crash after a possible submit enters reconciliation; it is not automatically retried. |
| Documents preserve candidate facts | Generated edits cannot silently add or alter qualifications, dates, employers, or numerical achievements. |
| Explicit exclusions cannot be outweighed | A known failed mandatory constraint excludes the job; missing evidence remains unknown. |
| Quota has one definition | Reservation, enforcement, UI counts, and reconciliation use one policy and time boundary. |
| Resources have one owner | Each attempt owns its browser, temporary files, task scope, and cleanup. |
| Reported work is durable | A queued response identifies persisted work; ready means an artifact exists and has passed validation. |
| Reuse follows dependency identity | Reuse is allowed only when relevant inputs, policies, model configuration, and artifact versions still match. |

## Architecture direction

Retain a modular monolith. Extract narrow domain services inside the existing subsystem directories as each behavior is fixed. Routes validate requests and translate results; domain services own policy; orchestration coordinates actions; adapters own external I/O. Avoid creating generic repository or service abstractions around every function.

~~~mermaid
flowchart TD
    UI[Svelte interface] --> API[Validated HTTP commands]
    API --> Policy[Document, eligibility, and quota policies]
    Policy --> Workflow[Durable operations and application attempts]
    Workflow --> DB[(SQLite state and artifact metadata)]
    Workflow --> Docs[Evidence and document pipeline]
    Workflow --> Browser[Per-attempt browser adapters]
    Docs --> AI[Provider adapters and measured caches]
    Workflow --> Events[Typed progress events and snapshots]
    Events --> UI
    Browser --> Verify[Submission evidence verification]
    Verify --> DB
~~~

Use HTTP for acknowledged mutations such as starting work, selecting documents, approving a review, and confirming manual completion. Keep WebSockets for progress notifications, with HTTP snapshots as the recovery source. During migration, existing WebSocket commands must pass the same authenticated command handler and validation; remove the duplicate mutation path after the bundled frontend switches.

### Separate records with different lifetimes

The following are proposed domain concepts, not a requirement for an immediate wholesale schema replacement.

| Concept | Responsibility and minimum information |
| --- | --- |
| Job and match | Job identity/content version; candidate-specific assessment version; eligibility and ranking dimensions. Preserve existing entities. |
| Document artifact | Immutable version of a base CV, tailored CV, or letter; source hash, PDF hash, type, provenance, ready/failed state, managed path. |
| Operation | Durable discovery or document-generation work: kind, input versions, state, idempotency key, attempts, error, timestamps, worker lease when needed. |
| Application attempt | One execution: match ID, method, phase, selected artifacts, destination, version counter, quota reservation, submission evidence. |
| Review snapshot | Attempt ID and revision, document hashes, field inventory and values, destination identity, decision, expiry and invalidation reason. |
| Application | Long-lived tracker record and recruiting lifecycle: applied, interview, offer, rejected. Link attempts without overloading recruiting status with browser phases. |
| Candidate evidence | User-confirmed facts linked to source spans and candidate version, with explicit corrections and provenance. |
| Usage and cached results | Operation-scoped measurements and reusable outputs with dependency keys; separate retention from authoritative user records. |

Start with additive columns/tables. Use the existing application event table where its ownership and semantics fit; do not turn a diagnostic log into the sole authoritative state store.

### Application state and side-effect contract

| Phase or outcome | Entry requirement | Allowed action and exit |
| --- | --- | --- |
| Preparing | Valid request and selected artifact versions | Resolve PDFs, destination, eligibility, and applicable quota policy. |
| Filling | Attempt owns its browser session | Navigate and fill allowed fields; stop for unsupported interaction. |
| Awaiting review | A current snapshot is persisted | Present exact values and attachments. Patches produce a validated new snapshot. |
| Ready to submit | Current review approved; values read back; reservation valid | Atomically consume the approval and persist submission intent before external submission. |
| Submitting | Intent persisted and exclusively claimed | Perform the final submission action once within this attempt. |
| Verifying | A submission was attempted | Inspect destination evidence. Do not use agent self-report as the sole proof. |
| Confirmed | Destination evidence or explicit user attestation exists | Update tracker and quota once; preserve confirmation source. |
| Needs verification | Submission may have happened | Preserve uncertainty, block automatic replay, offer reconciliation. |
| Awaiting user submission | Manual/assisted work prepared the application | Show client-facing next action; require explicit completion evidence. |
| Failed or cancelled before submission | No external submission could have occurred | Release reservation, preserve reason, close owned resources. |

A database transaction cannot make an arbitrary website submission exactly once. Persist intent before the action, prevent concurrent claims, and reconcile uncertain outcomes. A database write failure after the click is an uncertain local record, not evidence that the remote application failed.

## Delivery phases and dependencies

| Phase | Work | Release gate |
| --- | --- | --- |
| 0 Establish the baseline and contain known problems | W01–W02 | Deterministic checks, truthful capability limits, safe startup and privacy defaults. |
| 1 Repair user intent and correctness | W03–W13 | Core defects have behavior regressions; unsafe automation remains unavailable where verification is missing. |
| 2 Make workflows durable | W14–W16 | Restart, cancellation, duplicate commands, and reconnect preserve correct state. |
| 3 Finish operational support and cleanup | W17–W19 | Advertised desktop/container paths work; CI and documentation agree. |
| 4 Measure and reduce repeated work | W20–W23 | Measured cost reductions without regression in the accepted quality baseline. |
| 5 Improve intelligence | W24–W27 | Evidence-backed edits and evaluated matching outperform the baseline on held-out examples. |
| 6 Evaluate optional learning features | W28–W29 | Each experiment earns adoption through measured benefit and bounded maintenance cost. |

The main dependency chain is W01 → identity/artifacts/approval/outcomes → W14–W16 → supported release → optimization. W20 instrumentation can start after W01 and should precede performance-changing experiments. W24 evidence modeling can be designed earlier, but W10 provides the immediate factual-edit boundary.

Within a phase, work with disjoint files can proceed independently. Serialize schema migrations and shared changes to the application engine. Do not assign concurrent broad refactors of the same module.

Phase 1 introduces the minimum artifact, attempt, review, and operation records needed by its fixes. Phase 2 completes lifecycle ownership and recovery on those same records. Do not build an interim persistence model that must immediately be replaced.

## Phase 0 Establish the baseline and contain known problems

### W01 Define acceptance fixtures and decision records

**Owner:** maintainer and backend engineer. **Depends on:** nothing. **Covers:** cross-cutting review methodology.

1. Record current supported modes, deployment assumptions, quota semantics, unknown-eligibility behavior, and the confirmation evidence policy in short architecture decision records.
2. Promote the review's isolated failure cases into repository tests with temporary databases and fake external adapters. Put each regression alongside its corresponding fix so the main branch remains usable.
3. Build a small local fixture application site with successful submission, validation errors, multi-step forms, upload inspection, changed fields, and ambiguous responses. It must record actual requests and uploaded bytes.
4. Prepare representative old-schema databases and synthetic CV/job fixtures. Use no real credentials or production submissions.
5. Capture the existing test/lint/type/build baseline, test environment, revision, and commands. Separate offline tests, local browser/container tests, and optional provider-network tests.

**Acceptance:** a fresh checkout can run deterministic offline checks; fixture submissions can distinguish attempted from accepted requests; an engineer can reproduce each priority bug without a live job board.

**Relevant files:** [test fixtures](../../tests/fixtures/), [test configuration](../../tests/conftest.py), [CI](../../.github/workflows/ci.yml), [contribution guide](../../CONTRIBUTING.md).

### W02 Correct startup, deployment, and privacy defaults

**Owner:** backend/operations engineer. **Depends on:** W01 baseline. **Covers:** F04, F20, F21; temporary containment for F05.

1. Publish the container port on loopback by default. Keep the container's internal bind address compatible with Docker networking.
2. Disable browser dependency telemetry before any relevant import in all supported entry points, including direct ASGI startup, local launcher, tests, and containers. A privacy mode must enforce this consistently.
3. Abort startup on migration/schema failure; keep optional integration degradation separate and visible.
4. Stop advertising application-browser interaction in deployment modes that cannot support it. Default to reviewed assistance; keep final automatic submission disabled until the relevant site/mode passes W06–W08 and W17.
5. Document external data destinations accurately, including configured model providers and explicitly enabled integrations.

**Acceptance:** migration failure produces failed startup/readiness; subprocess tests intercept attempted telemetry without sending data; rendered Compose configuration publishes only loopback; the UI does not offer an unsupported browser capability.

**Relevant files:** [startup](../../backend/main.py), [configuration](../../backend/config.py), [launcher](../../start.py), [Compose](../../docker-compose.yml), [Dockerfile](../../Dockerfile), [deployment guide](../deployment.md).

## Phase 1 Repair user intent and correctness

### W03 Preserve job identity through batch processing

**Owner:** backend engineer. **Depends on:** W01. **Covers:** F01.

1. Return explicit stored-match records containing match ID, job ID, and the corresponding job details from persistence.
2. Carry these records through scoring, assessment, document generation, and status events. Remove positional pairing between filtered and unfiltered collections.
3. Set persisted IDs on converted job details where needed; distinguish job ID from match ID in names and type signatures.
4. Count actual completed/failed artifacts separately from selected matches.

**Acceptance:** a batch containing skipped, already-applied, duplicate, and eligible jobs persists the correct company, job content, assessment, and document for every match. A generation failure cannot increase the ready-document count. Include a generated/permuted-input test if it exercises identity preservation better than many hand-written examples.

**Relevant files:** [batch runner](../../backend/scheduler/batch_runner.py), [batch tests](../../tests/test_batch_runner.py).

### W04 Introduce explicit artifact selection and one path resolver

**Owner:** backend and frontend engineer. **Depends on:** W03 for batch associations. **Covers:** F10, F16.

1. Introduce immutable artifact identity for base and tailored documents, initially through additive metadata and a small artifact service. A base CV must not need a fake job association.
2. Resolve sources relative to the configured data directory; distinguish source files from compiled PDFs. Import allowed legacy external sources into managed storage before use.
3. Validate the artifact's PDF content, existence, match compatibility where applicable, and content hash. Reject missing/mismatched selections with an actionable response.
4. Send explicit CV and optional letter artifact IDs from the review UI. Resolve once, bind to the attempt, and pass the same files to the browser adapter.
5. Remove latest-document and first-PDF fallbacks when they would replace an explicit decision. If a selected source changes, require regeneration and review.

**Acceptance:** Use base CV uploads the base PDF bytes even when a newer tailored CV exists; a rejected variant never reappears; a .tex path cannot reach an upload call; relative and absolute legacy source paths resolve consistently; a removed or modified artifact fails before submission.

**Relevant files:** [apply API](../../backend/api/applications.py), [document API](../../backend/api/documents.py), [document model](../../backend/models/document.py), [pipeline](../../backend/latex/pipeline.py), [CV review panel](../../frontend/src/lib/components/CVReviewPanel.svelte).

### W05 Make regeneration real and preserve the last usable document

**Owner:** backend engineer. **Depends on:** W04. **Covers:** F09 and the first slice of I10.

1. Add a minimal durable document operation with queued/running/succeeded/failed states and input versions. Persist it before returning an accepted response and operation ID.
2. Let an application-managed worker claim committed work using its own database session. Do not carry a request-scoped session into a background task.
3. Generate and compile into a temporary/versioned location; validate the output before publishing artifact metadata.
4. Atomically select the successful new version. Preserve the prior working version if generation, compilation, or persistence fails.
5. Define force as bypassing reusable output, not deleting history. Make duplicate requests for the same operation key return the existing operation.

**Acceptance:** the response leads to a retrievable replacement PDF; a compile failure leaves the prior PDF retrievable; duplicate requests do not generate two replacements; a restart does not silently lose accepted work. W15 extends recovery and lease behavior.

**Relevant files:** [regeneration routes](../../backend/api/documents.py), [document pipeline](../../backend/latex/pipeline.py), [letter regeneration tests](../../tests/test_documents_letter_regenerate.py).

### W06 Separate preparation from confirmed submission

**Owner:** backend and frontend engineer. **Depends on:** W01; integrates W04. **Covers:** F02, F12.

1. Add minimal durable attempt identity and confirmation metadata, and define typed strategy outcomes for opened, prefilled, awaiting user submission, submission attempted, confirmed, failed, cancelled, and needs verification.
2. Stop normalizing manual/assisted preparation into applied. Check browser-open failure and offer a client-facing URL.
3. Implement destination verifiers for supported fixture/site flows using explicit confirmation signals and validation-error detection. Unsupported or ambiguous pages return needs verification.
4. Inspect browser-agent results, while retaining destination evidence as the deciding condition. Record evidence type, time, and bounded relevant details.
5. Add an explicit user completion action for manual work, labeled user-confirmed. Update tracker, analytics, exports, and actionable-job filtering consistently.

**Acceptance:** clicking a submit button on an invalid form does not mark applied; agent completion without confirmation does not mark applied; opening and prefilling remain incomplete; a known confirmation updates the tracker once; explicit user attestation has a distinct evidence source.

**Relevant files:** [status vocabulary](../../backend/applier/__init__.py), [recorder](../../backend/applier/recorder.py), [form filler](../../backend/applier/form_filler.py), [auto strategy](../../backend/applier/auto_apply.py), [manual strategy](../../backend/applier/manual_apply.py), [analytics](../../backend/api/analytics.py).

### W07 Authenticate commands and acknowledge review decisions

**Owner:** backend and frontend engineer. **Depends on:** W01, W04, W06 attempt identity. **Covers:** F11.

1. Define a local access boundary: validate Host and allowed Origin, protect state-changing HTTP requests against cross-site use, and authenticate HTTP and WebSocket sessions consistently.
2. Use a per-installation secret or pairing flow to establish a session; keep secrets out of URLs and routine logs. Document development-origin handling. Remote access stays unavailable until its authenticated deployment design exists.
3. Implement a typed review-decision command carrying attempt ID, review ID/revision, decision, and command idempotency key.
4. Accept approval only for the current awaiting-review snapshot. Return the saved acknowledgment for an identical prior command; otherwise reject unknown, premature, expired, already-consumed, or mismatched review decisions.
5. Return the persisted decision/updated attempt in an HTTP acknowledgment. Keep the modal pending until acknowledgment; retry a lost response with the same key.
6. Fetch pending review snapshots after reconnect. Route legacy WebSocket mutations through the same handler during the compatibility window.

**Acceptance:** an untrusted Origin and unauthenticated command cannot approve; premature/stale approval fails; duplicate accepted commands have one effect; disconnecting before acknowledgment does not dismiss the review; two tabs cannot approve different revisions silently.

**Relevant files:** [WebSocket route](../../backend/api/ws.py), [startup wiring](../../backend/main.py), [engine](../../backend/applier/engine.py), [socket store](../../frontend/src/lib/stores/websocket.ts), [queue UI](../../frontend/src/routes/queue/+page.svelte).

### W08 Make corrected values and approved state gate submission

**Owner:** browser/backend engineer. **Depends on:** W04, W06, W07. **Covers:** F03; execution boundary concern.

1. Replace raw client-supplied selectors with validated field IDs bound to the server's current inventory.
2. Apply corrections in both browser tiers, read back values, validate required fields, and build a new snapshot if anything changes.
3. Require approval of the resulting current snapshot. Persist an atomic approval-consumption check immediately before final submission.
4. Recheck destination, document hashes, and relevant field values at submission time. Any change returns to review.
5. Restrict the preparation agent's available actions so it cannot trigger final submission. Website instructions cannot change upload, destination, or submit permissions.

**Acceptance:** an unfillable correction, changed upload, destination redirect, stale review, or page mutation prevents submission; Tier 2 consumes corrections; adversarial fixture text cannot invoke the final-submit capability or upload unrelated files.

**Relevant files:** [form filler](../../backend/applier/form_filler.py), [auto strategy](../../backend/applier/auto_apply.py), [assisted strategy](../../backend/applier/assisted_apply.py), [patch tests](../../tests/test_patch_fields.py).

### W09 Enforce eligibility before scoring

**Owner:** matching/backend engineer. **Depends on:** W03. **Covers:** F07, F22.

1. Create one settings-to-policy mapping for remote requirements, location, experience, job type, language, and supported knockout conditions.
2. Represent each mandatory rule as pass, fail, or unknown with evidence and an explanation. Treat user-selected soft preferences separately.
3. Exclude known failures before weighted ranking. Put unknown mandatory conditions into a visible review group and block automatic application until resolved.
4. Wire all supported settings through real batch processing. Disable or label controls whose semantics are not implemented.
5. Version policy inputs so a settings change recomputes eligibility without repeating unrelated extraction.

**Acceptance:** a known onsite job fails remote-only regardless of skill score; unknown remote policy remains unknown; settings changes alter real batch results; language and experience tests cover absent data, ranges, and explicit conflicts.

**Relevant files:** [search settings](../../backend/api/settings.py), [filters](../../backend/matching/filters.py), [matcher](../../backend/matching/matcher.py), [fit engine](../../backend/matching/fit_engine.py), [batch runner](../../backend/scheduler/batch_runner.py).

### W10 Restrict factual changes before expanding AI tailoring

**Owner:** document/backend engineer. **Depends on:** W04. **Covers:** F06; immediate prerequisite to I01/I04.

1. Make verbatim selection, reordering, and formatting the safe initial editing operations.
2. Protect dates, employers, degrees, certifications, experience duration, metrics, and identity fields. Treat new or ambiguous substantive claims as requiring user fact confirmation.
3. Remove model-supplied confidence as sufficient approval for an edit. Keep source-span and LaTeX validation as separate structural checks.
4. Show unsupported or changed claims explicitly in the review UI; never silently downgrade the validator to preserve a generated output.
5. Let W24/W26 introduce richer evidence-backed rewriting later. Deterministic checks on numbers alone must not be described as general factual verification.

**Acceptance:** the two-years-to-ten-years-and-PhD probe is rejected; invented achievements without new numbers are also not automatically accepted; unrelated source spans cannot validate a claim; supported formatting changes still compile correctly.

**Relevant files:** [CV applicator](../../backend/latex/applicator.py), [replacement validation](../../backend/llm/validators.py), [CV modifier](../../backend/llm/cv_modifier.py), [diff UI](../../frontend/src/lib/components/DiffBlock.svelte).

### W11 Centralize quota and reservation policy

**Owner:** backend engineer. **Depends on:** W06 outcome semantics. **Covers:** F13 and part of F15.

1. Resolve the configured limit at reservation time through one policy service; use it for UI status and batch planning.
2. Define the quota as an automated-submission guard. Manual opening and assistance preparation do not consume a completed submission. Preserve visibility of manually confirmed applications separately.
3. Count active reservations, confirmed automated submissions, and unresolved possible submissions against the guard. Release only when non-submission is established.
4. Choose one daily boundary, initially UTC with a visible reset time. Store quota day/reservation time separately from applied time. Keep the day assignment stable across restarts.
5. Revalidate an expired/day-crossing reservation before submission. Reducing the limit blocks new reservations; it does not erase existing facts.
6. Preserve atomic count-and-reserve behavior and test it using independent SQLite connections.

**Acceptance:** UI and engine agree after changing settings; two requests competing for the last slot cannot both reserve; midnight and cancellation do not leak or double-release slots; unresolved attempts cannot evade the guard by repeated retries.

**Relevant files:** [daily-limit guard](../../backend/applier/daily_limit.py), [limit API](../../backend/api/applications.py), [time helpers](../../backend/utils/time.py), [daily-limit tests](../../tests/test_daily_limit.py).

### W12 Give each attempt exclusive resource ownership

**Owner:** browser/backend engineer. **Depends on:** W06, W11. **Covers:** F14, F15.

1. Construct a per-attempt strategy/context; remove strategy-singleton mutable browser handles.
2. Own browser/context creation and cleanup in one scope. Track subprocesses and temporary files in the same execution context.
3. Serialize access to a persistent browser profile if it cannot safely be shared. Keep this lock separate from global provider/browser concurrency limits.
4. Handle task cancellation explicitly, perform bounded cleanup/finalization, and re-raise cancellation. Do not swallow all BaseException failures.
5. Persist whether submission could have happened. Release reservations for known pre-submit cancellation and preserve uncertainty after intent/action.

**Acceptance:** two deliberately interleaved applications close their own browser exactly once; cancelling one does not close the other; exceptions during launch, filling, waiting, or cleanup leave no unowned resources; post-submit uncertainty remains visible.

**Relevant files:** [engine](../../backend/applier/engine.py), [statechart](../../backend/applier/state.py), [strategy helpers](../../backend/applier/_strategy_common.py), [session manager](../../backend/scraping/session_manager.py).

### W13 Fix query semantics and truthful UI results

**Owner:** backend and frontend engineer. **Depends on:** W03, W06. **Covers:** F17 and cross-layer outcome reporting.

1. Select the latest match deterministically, apply score/eligibility predicates in SQL, and use the same relation for total count and pagination.
2. Preserve stable ordering with an ID tie-breaker. Define unscored/unknown behavior explicitly.
3. Render domain outcomes from the apply response; HTTP success alone must not generate an application-success toast.
4. Report ready, failed, skipped, and pending counts from actual persisted state. Offer retry only for a retryable operation.
5. Add component tests where user intent crosses the API boundary rather than testing only store helpers.

**Acceptance:** the low-score newest/high-score older example returns the eligible item and correct total; navigation has no filter-created empty pages; a 200 response carrying failed/cancelled is displayed accurately; partial batch failure does not show all documents ready.

**Relevant files:** [jobs API](../../backend/api/jobs.py), [CV review panel](../../frontend/src/lib/components/CVReviewPanel.svelte), [batch tracker](../../frontend/src/lib/components/BatchPipelineTracker.svelte), [jobs tests](../../tests/test_api_jobs.py).

## Phase 2 Make workflows durable

### W14 Make the application state machine own real actions

**Owner:** backend engineer. **Depends on:** W04, W06–W08, W11–W12. **Covers:** F18, I10.

1. Refactor the existing engine incrementally around the attempt record. Move prepare, fill, review, submit, verify, and record into the phases that actually perform them.
2. Reduce strategies to browser interaction primitives with typed inputs/results. Remove their competing internal approval/submission lifecycle as each phase moves.
3. Keep phase transitions short and transactional. Use a version counter/conditional update to reject concurrent transitions.
4. Persist submit intent and consumed approval before the external action. Persist verified evidence and tracker updates together where possible.
5. Define terminal and waiting states, transition reasons, and retry eligibility in one domain module. Keep recruiting outcomes separate.
6. Remove obsolete pass-through states only after equivalent behavior tests pass through the real phase implementation.

**Acceptance:** event history corresponds to actual actions; no submit action can run from filling or awaiting review; concurrent claims permit only one execution; a persistence failure after remote submission is recoverable uncertainty; a restart never synthesizes a new approval.

**Relevant files:** [engine](../../backend/applier/engine.py), [statechart](../../backend/applier/state.py), [recorder](../../backend/applier/recorder.py), [application model](../../backend/models/application.py).

### W15 Reconcile interrupted work and control retries

**Owner:** backend/operations engineer. **Depends on:** W05, W14. **Covers:** F15, I10.

1. Extend the operation worker with atomic claims, bounded concurrency, explicit deadlines, retry budgets, and stale-claim recovery. Start with one process and a small worker count.
2. Give each task its own session and cancellation scope. Do not hold SQLite write transactions while awaiting browser/model/network work.
3. On startup, classify unfinished work by its last durable phase. Requeue idempotent document work; restart preparation with a fresh review where valid; reconcile possible submissions.
4. Persist command idempotency keys with a request fingerprint. Repeated identical commands return the existing result; a reused key with different inputs is rejected.
5. Deduplicate active application attempts per target using database-enforced policy. A user-initiated new attempt after a known failure remains possible.
6. Expose queued, running, waiting, failed, and interrupted operations with clear next actions. Give ephemeral screenshots and temporary artifacts retention limits.

**Acceptance:** terminate/restart the process before claim, during compilation, before submit, after click, and before result persistence. No accepted operation disappears; no old approval is reused; no possible submission is automatically repeated; exhausted retries produce a visible actionable failure.

**Relevant files:** [startup lifecycle](../../backend/main.py), [database](../../backend/database.py), [scheduler](../../backend/scheduler/), [apply tests](../../tests/test_apply_engine.py), [migration tests](../../tests/test_migrations.py).

### W16 Establish one HTTP and event contract

**Owner:** backend and frontend engineer. **Depends on:** W06, W07, W14. **Covers:** boundary validation and UI state cleanup.

1. Define typed request, response, command-error, and event models in one backend-owned schema source. Generate matching TypeScript types; use runtime validation for received payloads.
2. Standardize identifiers: job_id, match_id, attempt_id, operation_id, artifact_id, review_revision. Do not silently reinterpret a field's meaning.
3. Include operation/attempt identity, entity revision, event type, and protocol version in progress events. Define rejection of unsupported protocol versions.
4. Replace any[] stores and discriminator-only casts. Remove branches for nonexistent progress messages and derive UI state from documented events/snapshots.
5. Treat events as notification hints. Ignore stale revisions and recover with an authoritative snapshot after reconnect or a detected gap.
6. Align asynchronous endpoints on accepted responses with operation identity; distinguish validation/conflict/authorization failures from domain failures recorded on an operation.

**Acceptance:** malformed payloads are rejected safely; generated types are checked for drift in CI; duplicate/out-of-order events cannot regress a confirmed state; reconnect restores pending review and operation progress; frontend tests exercise failure and conflict responses.

**Relevant files:** [event models](../../backend/api/ws_models.py), [frontend API types](../../frontend/src/lib/types/api.ts), [event types](../../frontend/src/lib/types/ws.ts), [socket store](../../frontend/src/lib/stores/websocket.ts), [status bar](../../frontend/src/lib/components/StatusBar.svelte), [API reference](../api-reference.md).

## Phase 3 Finish operational support and cleanup

### W17 Deliver explicit desktop and container capabilities

**Owner:** browser/operations engineer. **Depends on:** W02, W08, W12, W15. **Covers:** F05 and operational release verification.

1. Define a capability matrix for desktop-visible browser, local headless container, and unsupported remote hosting.
2. Honor headless configuration in both browser tiers. Fail installation/build clearly if a required browser is unavailable; remove success-masking installation fallbacks.
3. In desktop mode, support visible user intervention with a documented session/profile lifecycle.
4. In container mode, support API-based discovery, document workflows, and only the browser actions covered by fixtures. If login/CAPTCHA requires an unavailable visible browser, stop and provide a client-facing handoff; do not imply that the server's browser is visible to the user.
5. Keep remote desktop/browser streaming as a separate future feature if it proves necessary. Do not add it merely to preserve the current Docker claim.
6. Exercise the built image as its actual non-root runtime user, with a persistent data volume and the supported browser/compiler configuration.

**Acceptance:** the image boots, migrates, compiles a PDF, launches the supported browser, and completes a local fixture with a verified result; unsupported interaction produces a useful blocked state; restart preserves artifacts and pending work. A health endpoint alone does not pass this gate.

**Relevant files:** [Dockerfile](../../Dockerfile), [Compose](../../docker-compose.yml), [browser factory](../../backend/llm/factory.py), [browser path utility](../../backend/utils/browser_path.py), [deployment](../deployment.md), [applying guide](../applying.md).

### W18 Clean lint, types, dependencies, and failure handling

**Owner:** maintainer/backend engineer. **Depends on:** W01; work incrementally alongside earlier phases. **Covers:** code-quality gate and provider-capability concerns.

1. Classify the reviewed 217 Ruff violations into safe mechanical fixes, behavior-sensitive cleanup, and justified exceptions. Run safe fixes on a small scope; inspect import side effects and keep formatting-only patches separate.
2. Lower the Pyright error ceiling from 19 to the verified zero baseline in its own CI/documentation change. Resolve or explicitly document the eight warnings; prevent new diagnostics from hiding behind an aggregate allowance.
3. Add Ruff to CI after cleaning the baseline, or temporarily track exact existing diagnostics with an owner and removal task. Avoid permanent broad ignores.
4. Audit broad exception catches: propagate required startup failures; translate external failures once into typed domain errors; log once with operation identity; keep optional degradation explicit.
5. Audit dependencies by runtime imports, packaging, CLI entry points, and optional integration use. Declare directly imported runtime dependencies directly; remove unused packages only after checking dynamic/registered use.
6. Test generation, embeddings, structured output, and browser-provider capabilities independently against pinned adapters. Correct the browser Anthropic capability documentation based on tested behavior.
7. Separate package upgrades from architecture changes. Verify runtime support and upstream compatibility when scheduling an upgrade; refresh lockfiles intentionally.

**Acceptance:** Ruff is clean and gated; Pyright has zero errors with a reviewed warning policy; frontend remains zero errors/warnings; mandatory imports no longer fall back to silent no-ops; provider capability tests match the documented matrix.

**Relevant files:** [project configuration](../../pyproject.toml), [provider factory](../../backend/llm/factory.py), [CI](../../.github/workflows/ci.yml), [contribution guide](../../CONTRIBUTING.md), [review guide](../code-review.md), [provider guide](../llm-providers.md).

### W19 Consolidate domain logic and current documentation

**Owner:** maintainer and full-stack engineer. **Depends on:** relevant behavior fixes and W16. **Covers:** duplicated policies, confusing names, stale documentation.

1. Move duplicated document resolution, quota policy, eligibility mapping, and outcome classification behind the services established by their fixes. Delete obsolete paths after callers migrate.
2. Rename misleading ID variables and replace free-form domain result dictionaries with narrow types. Keep compatibility aliases only at documented boundaries with removal criteria.
3. Split large routes/components by cohesive responsibility after behavior is stable. Keep UI rendering, API commands, and operation state separate; avoid a generic abstraction layer without multiple real consumers.
4. Remove obsolete ticket narratives, missing-report references, dead flags, unused fallback paths, and accidental document markup. Preserve comments that explain invariants and non-obvious constraints.
5. Update architecture, data model, API, provider, deployment, and user docs in the same change as behavior. Consolidate repeated facts into a canonical section linked elsewhere.
6. Write a short operator runbook: backup/restore, failed migration, pending submission, stalled regeneration, browser cleanup, provider outage, and artifact/cache deletion.

**Acceptance:** a search finds one policy implementation per domain rule; schema/status tables and documentation match; all relative links resolve; a new contributor can trace review → chosen PDF → attempt → outcome without guessing an identifier's meaning.

**Relevant files:** [API modules](../../backend/api/), [frontend components](../../frontend/src/lib/components/), [architecture](../architecture.md), [data model](../data-model.md), [file maps](../file-map.md), [documentation index](../index.md).

## Phase 4 Measure and reduce repeated work

### W20 Add cost and workflow measurements before optimization

**Owner:** backend/AI engineer. **Depends on:** W01; start early, align outcomes with W06. **Covers:** I09.

1. Preserve provider usage metadata through a typed internal response envelope or instrumentation hook while keeping call-site behavior compatible.
2. Record operation, stage, provider/model/config revision, prompt/schema version, input/output/cached tokens when available, retries, queue time, service time, and outcome.
3. Instrument embedding requests, browser-agent calls/steps, document compilation, and abandoned preparation as well as text-generation requests.
4. Represent missing token counts as unknown; label estimates. Calculate currency cost only with a versioned price source for the configured provider. Local-model token use is not a hosted API bill.
5. Build a repeatable benchmark with a fixed workload, warm/cold cache modes, stage timings, and the same relevant quality checks.
6. Keep prompts, CV text, credentials, screenshots, and mailbox bodies out of routine metrics. Bound local retention and allow deletion.

**Acceptance:** known test usage survives adapter → operation aggregation; retries are included once; parallel timing is measured as actual wall-clock latency; every benchmark reports workload, versions, cache state, token availability, quality, and latency.

**Relevant files:** [LLM protocol](../../backend/llm/base.py), [OpenAI-compatible adapter](../../backend/llm/providers/openai_compat.py), [Anthropic adapter](../../backend/llm/providers/anthropic_client.py), [logging](../../backend/logging_config.py).

### W21 Cache validated artifacts by their dependencies

**Owner:** backend/AI engineer. **Depends on:** W03–W05, W20. **Covers:** I07.

1. Add a small persistent cache abstraction with versioned keys, validated payloads, bounded size, and observable hit/miss reasons. Keep cache deletion independent of authoritative artifact deletion.
2. Implement parsed CV, embedding, and compiled base-PDF reuse first. Then add normalized job analysis and candidate-specific fit/tailoring results.
3. Include exact input content, endpoint identity without secrets, model identity/revision, schema/prompt version, normalization, embedding dimensions, toolchain, and relevant policy in keys.
4. Separate job-only extraction from candidate-dependent assessment. A changed CV should not repeat unchanged job extraction.
5. Coalesce concurrent identical work so two requests do not compute the same key. Validate stored content/hash before reuse and publish entries atomically.
6. For mutable model aliases without a trustworthy revision, support an explicit cache-generation setting and invalidate after configuration changes.

**Acceptance:** unchanged warm input creates zero generation/embedding calls and no recompilation for the cached stages; a changed CV invalidates candidate-dependent results; a model/endpoint change invalidates embeddings even at identical dimension; a corrupted cache entry becomes a controlled miss; deleting caches does not delete chosen documents.

**Relevant files:** [CV parser](../../backend/matching/cv_parser.py), [embedder](../../backend/matching/embedder.py), [document pipeline](../../backend/latex/pipeline.py), [compiler](../../backend/latex/compiler.py), [job analyzer](../../backend/llm/job_analyzer.py).

### W22 Enforce inference budgets and provider capabilities

**Owner:** AI/backend engineer. **Depends on:** W20; builds on W18 capability tests. **Covers:** F19, I08.

1. Replace the ambiguous dry-run boolean with an explicit operation policy: cached/API-only preview, budgeted live discovery, or full preparation.
2. Thread inference permission, maximum calls/tokens where enforceable, time deadline, and retry budget through all scraper and model adapters, including browser agents.
3. Make no-inference preview fail closed at the inference boundary. Explain unavailable live sources in its result.
4. Use strict structured schemas where the configured endpoint supports them. Maintain a tested capability fallback with bounded repair, clear validation failure, and no unbounded full-prompt replay.
5. Separate rate-limit backoff from format repair and semantic failure. Include all retries in the operation budget.
6. Use stable reusable prompt prefixes and measure actual provider cache hits. Prefer application cache hits that avoid a request entirely.

**Acceptance:** real-orchestrator preview makes zero model calls with an inference client that raises on use; budget exhaustion produces partial results and a reason; unsupported schema features fall back predictably; malformed responses cannot trigger unlimited retries.

**Relevant files:** [queue API](../../backend/api/queue.py), [batch runner](../../backend/scheduler/batch_runner.py), [scraping orchestrator](../../backend/scraping/orchestrator.py), [Tier 1 extraction](../../backend/scraping/scrapling_fetcher.py), [provider adapters](../../backend/llm/providers/).

### W23 Prepare documents according to user demand

**Owner:** backend and frontend engineer. **Depends on:** W05, W09, W15, W20–W22. **Covers:** I05.

1. Persist and display eligible preliminary results as sources finish; label incomplete assessments and source coverage.
2. Separate discovery, assessment, and document operations. Do not derive generation volume from remaining application quota.
3. Start with preparation on selection. Evaluate a bounded prefetch of one or two likely selections only if measured use justifies the extra work.
4. Prioritize explicit user requests over speculative work; cancel queued obsolete tasks and avoid cancelling an almost-complete shared computation needed elsewhere.
5. Bound model, browser, and compiler concurrency separately. Use short DB transactions and report queue time separately from processing time.

**Acceptance:** useful results appear before the whole batch finishes; selecting a job promotes its preparation; unselected jobs do not generate documents by default; cancelling or changing selection cannot attach a late artifact to another job; benchmark reports reduced wasted work and time to first useful result.

**Relevant files:** [batch runner](../../backend/scheduler/batch_runner.py), [queue UI](../../frontend/src/routes/queue/+page.svelte), [batch tracker](../../frontend/src/lib/components/BatchPipelineTracker.svelte).

## Phase 5 Improve intelligence

### W24 Build a versioned candidate evidence bank

**Owner:** AI/backend engineer with maintainer review. **Depends on:** W10, W20–W21. **Covers:** I01.

1. Extract candidate facts with stable source references: skills in context, roles, projects, dates, qualifications, achievements, and user-supplied clarifications.
2. Distinguish extracted from user-confirmed facts. Surface ambiguous parsing for correction; keep the original source and correction history.
3. Retrieve evidence for each job requirement and classify supported, related, missing, or unknown. Related experience does not assert possession of the exact requirement.
4. Version the evidence bank by source and corrections; invalidate only dependent assessments/artifacts.
5. Supply compact relevant evidence to downstream generation, with a controlled full-source fallback for uncertain retrieval.

**Acceptance:** every usable fact resolves to a source span or explicit user entry; edits invalidate affected outputs; retrieval recall is measured on labeled requirements; missing retrieval is not presented as proof of missing experience; contradictory facts trigger review.

**Relevant files:** [CV parser](../../backend/matching/cv_parser.py), [job skill extractor](../../backend/matching/job_skill_extractor.py), [CV editor](../../backend/llm/cv_editor.py), [profile UI](../../frontend/src/lib/components/settings/ProfileTab.svelte).

### W25 Evaluate fit dimensions and correct score presentation

**Owner:** AI/matching engineer and product owner. **Depends on:** W09, W20, W24. **Covers:** F08, I03.

1. Rename simulated ATS output to an evidence-based skill coverage estimate; do not present it as hiring probability or an actual ATS test. Add insufficient-evidence behavior.
2. Persist technical fit, seniority fit, work preferences, location compatibility, and evidence strength separately, with input/model versions.
3. Compose soft preferences in code. Mandatory eligibility remains a separate gate, not another compensating weight.
4. Label 50–100 representative postings with a documented rubric, including French/English where supported and difficult unknown/incompatible cases. Split tuning and held-out evaluation by role/company families where feasible.
5. Calibrate thresholds per embedding/model setup and compare to the deterministic baseline. Record disagreement and uncertain labels.
6. Recompute ordering from stored dimensions when weights change, without model calls.

**Acceptance:** known excluded jobs cannot enter the eligible shortlist; absence of extracted skills cannot become a perfect score; weight-only changes cause zero inference; held-out ranking metrics and error examples support any default-threshold change.

**Relevant files:** [fit engine](../../backend/matching/fit_engine.py), [matcher](../../backend/matching/matcher.py), [score indicator](../../frontend/src/lib/components/ScoreIndicator.svelte), [matching guide](../matching.md).

### W26 Generate only supported and useful document changes

**Owner:** document/AI engineer. **Depends on:** W04, W10, W21, W24–W25. **Covers:** I04.

1. Choose the least expensive useful operation: reuse an approved version, reorder supported content, select relevant bullets, edit a narrow span, or ask for missing evidence.
2. Represent a suggested edit as source span, operation, supporting evidence IDs, intended requirement, and proposed text.
3. Validate evidence linkage and factual boundaries independently of formatting. Unknown or newly introduced claims require user confirmation before becoming reusable evidence.
4. Store reusable approved content by role family and evidence version. Keep employer names, job identity, and destination-specific requirements outside generic reuse.
5. Measure accepted useful edits and rejected unsupported claims. Do not optimize only for textual difference or the model's score.

**Acceptance:** a changed candidate fact invalidates affected prose; reused content does not carry another company's name; qualification gaps do not trigger invented qualifications; the same approved inputs avoid generation; compiled PDFs retain expected layout and content.

**Relevant files:** [CV modifier](../../backend/llm/cv_modifier.py), [prompts](../../backend/llm/prompts.py), [applicator](../../backend/latex/applicator.py), [pipeline](../../backend/latex/pipeline.py), [CV review](../../frontend/src/lib/components/CVReviewPanel.svelte).

### W27 Evaluate selective reasoning and model routing

**Owner:** AI engineer. **Depends on:** W20–W22, W24–W26. **Covers:** I02.

1. Start with deterministic constraints and cached evidence; use a narrow inexpensive judgment for unresolved dimensions.
2. Escalate only when uncertainty could change shortlist membership, a factual edit, or a meaningful user choice.
3. Calibrate escalation using observed errors on labeled examples. Model self-confidence alone is not a correctness or submission gate.
4. Batch independent judgments sharing evidence; measure the cost and latency of validators and escalations, not just the first cheap call.
5. Compare the existing configured provider with any optional narrow-judgment provider, including TypeSafe/Jev if useful, behind a small adapter. Do not add a mandatory vendor dependency without evidence.
6. Keep a feature flag and the prior deterministic/validated path available for rollback.

**Acceptance:** paired evaluation meets the quality limits defined below and reduces measured cost or latency; all calls fit the shared budget; unsupported provider capabilities fail visibly; ambiguous cases still reach deeper review when needed.

**Relevant files:** [provider boundary](../../backend/llm/base.py), [factory](../../backend/llm/factory.py), [fit engine](../../backend/matching/fit_engine.py), [batch assessment](../../backend/scheduler/batch_runner.py).

## Phase 6 Evaluate optional learning features

### W28 Reuse validated form mappings

**Owner:** browser/AI engineer. **Depends on:** W08, W12, W17, W20–W22. **Covers:** I06.

1. Extract a compact inventory of field IDs, labels, types, options, required state, autocomplete hints, and relevant surrounding text.
2. Fill obvious mappings deterministically and use semantic judgment only for ambiguous fields, with an explicit no-match result.
3. Store reviewed mappings by site, form structure, and semantic version; do not store an approval as a reusable mapping.
4. Compare a current inventory with the saved version. Reuse only compatible mappings and reanalyze changed fields.
5. Keep execution selectors server-owned and validate uploads, destination, and current field values through the existing review boundary.

**Acceptance:** a changed required field invalidates the relevant mapping; two lookalike forms do not share unsafe selectors; protected answers are never invented; fewer browser/model steps are measured on supported fixtures without reducing correct-fill or confirmation accuracy.

**Adoption rule:** ship only for a small tested form family first. If ongoing breakage exceeds measured savings, keep deterministic filling and remove the experimental learner.

**Relevant files:** [form filler](../../backend/applier/form_filler.py), [auto strategy](../../backend/applier/auto_apply.py), [form tests](../../tests/test_form_filler.py).

### W29 Learn explicit preferences and ask useful clarifications

**Owner:** AI/frontend engineer and product owner. **Depends on:** W09, W24–W25. **Covers:** I03.

1. Capture explicit reasons such as wrong seniority, location, industry, or role type. Keep an editable preference history.
2. Treat unopened, delayed, and abandoned items as weak signals; do not infer rejection automatically.
3. Propose a clarification when one answer resolves uncertainty across many jobs. Show how it changes eligibility or ranking.
4. Recompute affected dimensions in code where possible and explain why ordering changed.
5. Evaluate relevance using a fixed holdout and user feedback; treat interviews/callbacks as delayed, confounded signals.

**Acceptance:** preference changes are inspectable and reversible; no new hard constraint appears without user intent; one clarification updates only dependent assessments; explicit feedback improves the relevant slice without suppressing unrelated eligible roles.

**Adoption rule:** keep feedback collection useful even if automatic learning does not outperform editable explicit preferences.

**Relevant files:** [search preferences](../../frontend/src/lib/components/settings/SearchTab.svelte), [queue UI](../../frontend/src/routes/queue/+page.svelte), [user settings model](../../backend/models/user.py), [matching](../../backend/matching/matcher.py).

## Database and artifact migration plan

Treat migration work as part of each feature's completion, not a final cleanup activity.

### Expand the schema without losing history

1. Inventory current constraints, indexes, status aliases, document paths, and recruiting-lifecycle consumers before adding new values. The current application status CHECK constraint cannot accept arbitrary new workflow phases.
2. Add artifact/attempt/review/operation tables and necessary links with tested Alembic migrations. Keep transient attempt phases separate from application recruiting status.
3. Backfill resolvable document records with managed paths and hashes. Mark missing or invalid PDFs unavailable and offer regeneration; never silently select a different document.
4. Preserve historical applied records and their original timestamps. Mark their confirmation provenance as legacy unknown where no evidence exists. Do not fabricate receipts or rewrite all history as failed.
5. Classify old pending work as interrupted. Require reconciliation where past submission cannot be ruled out; never auto-submit it during an upgrade.
6. Introduce compatibility reads and one authoritative writer, then migrate the bundled frontend. Remove legacy aliases and unused columns only after supported upgrade paths and all callers are checked.

Use short, restartable backfill batches if a dataset is large. Record migration progress where a backfill cannot fit in one transaction. Validate foreign keys, indexes, row counts, and representative reads after each migration. Exercise SQLite table rebuilds and failure rollback on an old-schema database, not only on metadata-created empty tables.

### Publish files and database metadata safely

The filesystem and SQLite do not share a transaction. Generate into a staging location, validate the PDF, publish an immutable file with an atomic filesystem move where supported, then commit its metadata/current-selection change. A crash can leave an orphan file; it must not destroy the old selected artifact or expose a ready record pointing to an unfinished file.

Garbage collection should remove only unreferenced, expired staging/orphan/cache data. Artifacts referenced by an application attempt or user selection remain protected until an explicit retention/deletion policy allows removal. Store content hashes for integrity; hashes do not replace factual validation.

### Backup and rollback

Before a schema-changing release, create a consistent SQLite backup through the database backup mechanism or after stopping writers; copying only the main file during WAL activity is insufficient. Back up referenced documents and the credential-encryption key securely as part of the same recovery procedure.

Test restoration into a separate data directory. For additive compatible changes, rollback may use the previous application version with the expanded schema. After incompatible changes, stop workers and follow a tested restore/forward-fix procedure; do not assume an Alembic downgrade is lossless.

Database restoration cannot undo external submissions. Preserve submission-intent/evidence records and reconcile activity since the backup before allowing retries. If a release fails after real submissions, a forward fix may be safer than restoring old application state.

## Cleanup execution rules

Use cleanup to remove duplication and make behavior easier to verify. Avoid a repository-wide rewrite or file shuffle before the behavior fixes land.

| Area | Concrete cleanup | Completion evidence |
| --- | --- | --- |
| Formatting and imports | Small safe lint fixes; no unsafe bulk autofix; separate semantic changes. | Ruff clean and focused tests still pass. |
| Domain types | Narrow outcomes and identifiers; remove generic dictionaries at critical boundaries. | Invalid states fail validation; call sites use consistent IDs. |
| Large modules | Extract artifact resolution, eligibility, quota, review commands, and workflow actions by responsibility. | One authoritative implementation and regression coverage for each rule. |
| Exceptions | Distinguish mandatory failure, retryable external failure, unsupported capability, cancellation, and unknown submission. | Each category reaches a documented UI/operation state. |
| Async tasks | Explicit ownership, deadlines, bounded queues, per-task sessions, cancellation-safe cleanup. | Interleaving and restart tests pass without leaks or stuck reservations. |
| Frontend | Typed API state, acknowledged mutations, accurate result rendering, stale-event handling. | Component/browser tests validate actual user choices and outcomes. |
| Dependencies | Remove unused dependencies only after runtime/registration checks; test optional capability combinations. | Clean locked installs and capability smoke tests. |
| Documentation | One source for status vocabulary, settings, deployment modes, and API contracts. | Docs updated with the feature and relative links checked. |
| Stored diagnostic data | Separate metrics from content; bound raw prompts/screenshots/log retention and make deletion explicit. | Metrics contain no fixture secrets or CV/mail bodies; cleanup preserves referenced artifacts. |

Each cleanup PR should identify the responsibility it clarifies or duplication it removes. A file-length target, higher coverage percentage, or changed naming convention alone is not evidence of better behavior.

## Validation and CI plan

### Validation layers

| Layer | What to exercise | When it runs |
| --- | --- | --- |
| Unit/domain | Eligibility truth tables, result types, cache keys, fact protection, transition rules. | Every relevant PR. |
| API and persistence | Actual request → service → SQLite → response; separate sessions for concurrency; artifact retrieval. | Every relevant PR and required CI suite. |
| Frontend components | Selected document IDs, domain failure rendering, disconnected approval, stale review conflicts. | Every frontend/contract PR. |
| Local browser fixtures | Actual uploaded bytes, patch readback, validation failure, confirmation page, malicious page instructions. | Browser/approval/adapter PRs and releases. |
| Migration and recovery | Upgrade from supported previous schema; interrupted upgrade; crash at each side-effect boundary; backup restore. | Schema/workflow PRs and releases. |
| Built-container smoke | Runtime browser, compiler, persistent volume, restart, declared capability behavior. | Packaging/browser changes and release gate. |
| AI evaluation | Labeled frozen examples, held-out ranking, unsupported claims, tokens and latency, warm/cold runs. | Prompt/model/ranking changes; recorded benchmark before enabling a new default. |
| Optional external integration | Small controlled provider/source checks with explicit credentials and budgets. | Manual/release workflow; no real application submission in routine CI. |

Reuse existing tests and add tests that prove changed behavior. Do not multiply mock-only tests that assert a successful return from another mock. For small reversible documentation changes, link/content validation is sufficient.

### Required checks

The reviewed baseline is 635 backend tests passed with 5 skipped, 50 frontend tests passed, a successful frontend build, zero frontend errors/warnings, zero Pyright errors with eight warnings, and 217 Ruff violations. These are historical review results, not a result from implementing this plan.

The target PR gates are:

- Deterministic backend tests pass without provider credentials or external network dependence.
- Pyright has zero errors and no unreviewed new warnings.
- Ruff passes after W18; any temporary debt baseline is explicit and decreases.
- Frontend checking reports zero errors and warnings, frontend tests pass, and production build passes.
- Contract generation is up to date; migration tests run for schema changes.
- Relevant fixture/browser tests run when application behavior changes.

The prior host-isolated backend command is retained in the review for reproducibility. CI should define its own clean test environment and explicit markers instead of depending on unrelated host plugins or silently skipping required checks.

### Product acceptance scenarios

A supported release must pass an end-to-end local journey that:

1. Discovers a mixed eligible/ineligible/unknown job set and preserves job identity.
2. Shows reasons and uncertainty instead of unsupported ATS certainty.
3. Generates or reuses a valid artifact, then honors Use base CV and explicit letter omission.
4. Applies corrections, displays current values, and accepts only the current review.
5. Detects rejected submission and confirmed submission as different outcomes.
6. Leaves manual/assisted preparation awaiting user action.
7. Survives a disconnected frontend, duplicate command, cancellation, and process restart.
8. Keeps quota, tracker, analytics, and operation state consistent.
9. Runs in every deployment mode advertised as supported.

## Measurement and optimization acceptance

Establish the baseline in W20 before committing to percentage savings. Use the same workload, model configuration, hardware, source fixtures, and evaluation rubric for paired comparisons.

| Metric | Definition | Initial acceptance or investigation target |
| --- | --- | --- |
| Time to first useful result | Refresh command accepted → first eligible, explained shortlist item displayed. | Improve against baseline; report incomplete source coverage. |
| Time to usable document | Explicit selection → validated, retrievable PDF. | Report cache-hit/miss and queue time separately. |
| Tokens per selected job | All generation, embedding, repair, and attributed browser inference for the workload divided by selected jobs. | Measure total workload as well, so speculative work is not hidden. |
| Tokens per confirmed application | Relevant workflow inference divided by confirmed applications, with evidence source reported. | If there are no confirmations, report total cost and no ratio. |
| Repeated-work elimination | Unchanged validated inputs served from application cache. | Zero model/embedding calls for cached stages; zero unnecessary base-PDF compilation. |
| Preference reranking | Weight change using already stored fit dimensions. | Zero inference; measure local recompute/display latency. |
| Preparation waste | Completed generated documents never opened/selected within the chosen observation period. | Compare on-demand and optional prefetch policies. |
| Ranking quality | Precision at the chosen shortlist size and recall of labeled eligible/relevant jobs. | No accepted regression hidden by a lower token bill. |
| Eligibility error | Known mandatory violations admitted as eligible; unknowns incorrectly made definite. | Zero such errors in deterministic acceptance fixtures. |
| Factual edit error | Unsupported/altered claims in reviewed generated outputs. | Zero in the release regression set; report observed rate and sample size in broader evaluation. |
| Submission correctness | False local confirmation, duplicated remote attempt, and unresolved-result frequency. | Zero false confirmations/duplicates in controlled fixtures; unresolved cases remain visible. |
| Resource reliability | Browser/process leaks, stuck reservations, lost accepted operations. | Zero in concurrency and restart acceptance tests. |

For the first optimization experiment, investigate whether at least a 25% reduction in total inference tokens or a 30% reduction in time to first useful result is achievable on the fixed workload. These are provisional experiment targets, not measured benefits or release promises. Revisit them after identifying which stages dominate.

Compare cold and warm caches separately. Report p50 and p95 only with enough repeated measurements to make them useful, alongside sample counts and ranges. A 50–100-example labeled set is a starting point for error analysis, not proof of general reliability. Inspect regressions by language, role family, and missing-information cases.

A cheaper model or new cascade may become default only if factual/eligibility/submission invariants hold and the held-out quality limits set before the experiment are met. If evidence is inconclusive, keep the experiment optional.

## Release sequence and rollback gates

| Release | Included behavior | Conditions to enable it | Rollback strategy |
| --- | --- | --- | --- |
| R0 Containment | W02 defaults, fail-fast startup, supported-mode limits. | Startup/privacy checks pass; changed configuration documented. | Restore prior compatible code only if it preserves the corrected access/privacy boundary. |
| R1 Correct reviewed workflow | W03–W13, minimum durable records, accurate UI outcomes. | Identity, selection, fact protection, approval, quota, and ownership regressions pass. | Disable affected action, retain prepared artifacts and honest outcomes; never restore silent-submit behavior. |
| R2 Recoverable supported product | W14–W19 and the supported container/desktop matrix. | Restart, migration, backup/restore, browser fixtures, and cleanup gates pass. | Pause claims, retain operations/evidence, use compatible prior version or forward fix. |
| R3 Measured efficiency | W20–W23. | Baseline and paired benchmarks show benefit without accepted quality regression. | Disable prefetch/routing/cache namespace; recompute from authoritative inputs. |
| R4 Evidence-based intelligence | W24–W27. | Held-out evaluation, provenance, factual review, and cache invalidation pass. | Use prior validated assessment/edit policy; invalidate affected derived outputs. |
| Experimental releases | W28–W29 individually. | Specific experiment acceptance criteria pass on supported scope. | Disable the experiment without losing explicit user preferences or authoritative artifacts. |

An unresolved P1 finding blocks the affected advertised feature. A smaller release may ship a truthful supported subset while the capability remains unavailable. Passing unit tests does not waive a missing browser, migration, or user-intent acceptance gate.

Before enabling broader automatic submission, require a small maintainer-supervised evaluation on supported flows with explicit permission for any real submissions. That evaluation is a future release task; this planning work does not authorize sending applications.

## Estimated effort and capacity

These are planning ranges in focused engineering days, including implementation, meaningful tests, review fixes, migrations, and relevant documentation. They assume familiarity with the repository. They are not elapsed-time commitments.

| Workstream | Focused days |
| --- | --- |
| Phase 0 baseline and containment | 3–5 |
| Phase 1 correctness fixes | 12–18 |
| Phase 2 durable workflows and contracts | 12–20 |
| Phase 3 deployment and operational support, excluding cleanup below | 7–12 |
| W18–W19 cleanup across phases | 6–10 |
| Phase 4 instrumentation, reuse, and progressive preparation | 10–16 |
| Phase 5 evidence and evaluated intelligence | 12–20 |
| Phase 6 optional experiments | 6–12 |
| Core scope excluding optional experiments | **62–101** |
| Full scope including optional experiments | **68–113** |

Reserve roughly 20% for browser variability, migration surprises, and review feedback: approximately 75–122 focused days for the core scope, or about 15–25 weeks at five focused days per week. Maintenance, support, and part-time availability extend calendar time.

The first containment release should take days. R1 is approximately 15–23 focused days from the start, before contingency, if the scope stays narrow. R2 adds durable recovery and deployment acceptance; it should not be promised on the R1 timeline.

A second engineer can work on frontend contracts/tests, fixtures, or isolated cleanup while the primary engineer owns schema and workflow changes. Do not assume linear speedup: migrations, domain decisions, and integrated browser behavior remain shared constraints.

Re-estimate after W01, after the first artifact/attempt migration, and after W17's container fixture test. If capacity is limited, defer W27–W29 and richer rewriting before cutting correctness or recovery.

## First implementation sequence

Start with these reviewable PRs. Each should include its regression and relevant documentation; split further when a schema/API change makes review difficult.

1. **Baseline and fixtures:** W01 environment separation, regression fixture skeletons, decision records.
2. **Startup/privacy containment:** W02 loopback publication, telemetry enforcement, migration failure propagation.
3. **Batch identity:** W03 explicit match/job records and truthful readiness counts.
4. **Artifact foundations:** W04 version metadata, source/PDF distinction, managed resolver and migration.
5. **Explicit document choice:** W04 frontend request contract and final-upload verification.
6. **Real regeneration:** W05 durable operation and replace-on-success artifact publication.
7. **Outcome and attempt semantics:** W06 minimal attempt record, preparation states, confirmation provenance and UI.
8. **Acknowledged review commands:** W07 access/session checks, review identity, idempotency and reconnect behavior.
9. **Submission boundary:** W08 readback, patch failures, approval consumption and restricted browser actions.
10. **Quota and ownership:** W11 and W12 as separate PRs with concurrency/cancellation tests.
11. **Eligibility and factual edits:** W09 and W10 as separate PRs; these can start earlier against independent modules.
12. **Listing and UI consistency:** W13 filtered pagination and domain-result component tests.

W18's zero-error type gate and small mechanical cleanup PRs can land independently after the baseline. W20 instrumentation can also proceed early. Keep broad file moves and dependency upgrades out of the correctness PRs.

## Risks and decision checkpoints

| Risk or unresolved choice | Planned treatment | Decision owner and timing |
| --- | --- | --- |
| Unsupported sites lack reliable submission signals | Return needs verification; enable verified automation only per tested flow. | Maintainer/browser engineer before W06 release. |
| Local authentication harms first-run usability | Test pairing/session setup and localhost development separately; preserve the command boundary. | Maintainer/full-stack engineer in W07 design. |
| Quota meaning differs from user expectations | Document the proposed automated-submission guard, separate manual counts, and show reset time. | Product owner in W01, before W11. |
| Browser profiles cannot be shared safely | Start with serialized profile access and per-attempt ownership; measure concurrency before raising it. | Browser engineer in W12. |
| Old applied records cannot prove submission | Preserve history with legacy-unknown provenance; avoid retrospective false certainty. | Maintainer before W06 migration. |
| Schema design expands into a workflow framework | Implement only concrete operation/attempt needs; keep one process and SQLite. | Maintainer at W05 and W14 review. |
| Cache keys or mutable models produce stale outputs | Version dependencies and cache generation; invalidate conservatively; keep authoritative source artifacts. | AI/backend engineer in W21. |
| Fact extraction misses valid evidence | Preserve full-source fallback, expose unknowns, measure retrieval recall. | AI engineer before W24 becomes default. |
| AI evaluation is too small or biased | Use held-out families and error slices; expand examples based on observed failures. | Product owner/AI engineer before W25–W27 defaults. |
| Container interaction scope grows unexpectedly | Ship a smaller truthful capability set; estimate remote viewing separately. | Maintainer at W17 checkpoint. |

Recommended choices in this plan are working defaults. Record changes to those choices before implementing dependent work; avoid reopening settled details in every PR.

## Traceability to the review

| Finding | Work items |
| --- | --- |
| F01 Job and match association | W03 |
| F02 Unverified submission success | W06, W08, W14 |
| F03 Corrections do not gate submission | W07–W08 |
| F04 Unauthenticated network exposure | W02, W07 |
| F05 Container browser mismatch | W02, W17 |
| F06 Unsupported CV claims | W10, W24, W26 |
| F07 Mandatory preferences treated as weights | W09 |
| F08 Unsupported ATS interpretation | W25 |
| F09 Regeneration never scheduled | W05, W15 |
| F10 Base-CV choice ignored | W04 |
| F11 WebSocket and review boundary | W07–W08, W16 |
| F12 Preparation recorded as applied | W06 |
| F13 Divergent quotas | W11 |
| F14 Shared browser ownership | W12 |
| F15 Cancellation reservation leak | W11–W12, W15 |
| F16 Source file used as PDF | W04 |
| F17 Filtering after pagination | W13 |
| F18 Pass-through lifecycle states | W14 |
| F19 Preview can invoke models | W22 |
| F20 Migration failure swallowed | W02 |
| F21 Default dependency telemetry | W02, W18 |
| F22 Unused search filters | W09 |

| Improvement proposal | Work items |
| --- | --- |
| I01 Candidate evidence | W24 |
| I02 Selective reasoning | W27 |
| I03 Fit dimensions and preference learning | W25, W29 |
| I04 Approved content reuse | W10, W26 |
| I05 Progressive preparation | W23 |
| I06 Validated form mappings | W28 |
| I07 Dependency-aware caching | W21 |
| I08 Structured output and prompt reuse | W22 |
| I09 Measurement | W20 |
| I10 Durable workflows | W05, W14–W16 |

## Definition of done for each work item

- The user-visible behavior and domain invariant are implemented through the actual API/UI path.
- Acceptance tests prove the resulting state, selected artifact, or external-action arguments; relevant failure paths are included.
- Required lint, type, build, contract, migration, and integration checks pass for the scope.
- Stored data has a documented migration/backfill and rollback/recovery path.
- Errors, retries, cancellation, and unknown outcomes have accurate observable states.
- Documentation describes shipped capability; new configuration has sensible defaults and validation.
- Performance-changing work includes comparable measurements and quality results.
- Dead compatibility code and temporary flags are either removed or have a specific removal condition.
- The PR states what changed, why, how it was validated, and any remaining supported-scope limitation.

Track each item as not started, in progress, in review, accepted, or deferred. A passing test count or merged refactor alone does not mark a product behavior accepted; the release owner checks the associated acceptance gate.
