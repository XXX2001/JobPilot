# Documentation Overhaul Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete all existing `docs/` content (except `docs/superpowers/`) and rebuild it from scratch; add full Doxygen-compatible docstrings to every Python file in `backend/`.

**Architecture:** 11 agents run in parallel — 10 module agents each own one `backend/` subdirectory and add inline docstrings, 1 docs agent reads all source and rebuilds the entire `docs/` tree. The two workstreams are independent and can run simultaneously. No agent changes any logic, imports, or function signatures.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Gemini LLM, Playwright, SvelteKit, SQLite, Tectonic (LaTeX compiler), Scrapling, browser-use.

**Spec:** `docs/superpowers/specs/2026-03-16-documentation-overhaul-design.md`

---

## Docstring Standard (all agents must follow this exactly)

### Module docstring — top of every `.py` file
```python
"""
@file       <module_dir>/<filename>.py
@brief      One-line description of what this file is and owns.
@details    Multi-line explanation of this file's responsibilities, design
            decisions, and how it fits in the system.
            Depends on: <internal modules/classes this file imports from>
            Called by:  <modules/classes that import from this file>
"""
```
For `__init__.py` with re-exports:
```python
"""
@file       scraping/__init__.py
@brief      Public re-exports for the scraping package.
@details    Exports: ScrapingOrchestrator, AdzunaClient, ...
"""
```
For empty `__init__.py`:
```python
"""
@file       security/__init__.py
@brief      Package marker for backend.security. No public re-exports.
"""
```

### Class docstring
```python
class Foo:
    """
    @brief   One-line summary of what this class owns.
    @details Multi-line explanation of lifecycle, invariants, design decisions.
    """
```

### Public method docstring (names NOT starting with `_`)
```python
def bar(self, x: int, y: str) -> bool:
    """
    @brief   One-line summary.
    @param   x    Description of x.
    @param   y    Description of y.
    @return  True if ..., False otherwise.
    @raises  ValueError  If x is negative.   ← omit entirely if method does not raise
    @note    Side-effects or caveats.         ← omit if not needed
    """
```

### Private method docstring (names starting with `_` or `__`)
```python
def _helper(self, x: int) -> None:
    """@brief One-line summary."""
```

### Attribute inline comment
```python
max_retries: int  #: @brief Maximum retry attempts before giving up.
```

### Hard rules for all agents
1. Read every file in scope before writing any changes
2. Only add or update docstrings/comments — zero changes to logic, imports, signatures, or indentation of code
3. If a correct docstring already exists, update it to match the standard above; if wrong or missing, replace/add
4. Cross-references use full file paths: `@see backend/scraping/orchestrator.py`
5. Every module docstring must include both `Depends on:` and `Called by:` lines

---

## Chunk 1: Inline Docstrings — 10 parallel agents

> Dispatch all 10 tasks in this chunk simultaneously.

---

### Task 1: scraping module docstrings

**Files to modify:**
- `backend/scraping/__init__.py`
- `backend/scraping/orchestrator.py`
- `backend/scraping/adaptive_scraper.py`
- `backend/scraping/scrapling_fetcher.py`
- `backend/scraping/adzuna_client.py`
- `backend/scraping/session_manager.py`
- `backend/scraping/deduplicator.py`
- `backend/scraping/site_prompts.py`
- `backend/scraping/json_utils.py`

**Context for this module:**
The scraping module is the job-discovery engine. It has a three-phase pipeline: Phase 1 scrapes the Adzuna REST API, Phase 2 scrapes known job boards (LinkedIn, Indeed, Google Jobs, Welcome to the Jungle, Glassdoor) using a Tier 1 HTTP fetcher (ScraplingFetcher) that falls back to a Tier 2 browser-use LLM agent (AdaptiveScraper), Phase 3 scrapes user-supplied lab/company URLs via Tier 2 only. All phases are coordinated by ScrapingOrchestrator. BrowserSessionManager maintains persistent Playwright sessions per job board. JobDeduplicator deduplicates by MD5 hash of company|title|location.

- [ ] Read all 9 files in `backend/scraping/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 2: llm module docstrings

**Files to modify:**
- `backend/llm/__init__.py`
- `backend/llm/gemini_client.py`
- `backend/llm/job_analyzer.py`
- `backend/llm/cv_modifier.py`
- `backend/llm/cv_editor.py`
- `backend/llm/job_context.py`
- `backend/llm/validators.py`
- `backend/llm/prompts.py`

**Context for this module:**
The LLM module is JobPilot's interface to Google Gemini. GeminiClient wraps the google-genai SDK with a 15 RPM sliding-window rate limiter, primary-plus-fallback model chain, exponential back-off on 429 responses, and self-healing JSON retry. JobAnalyzer extracts structured skill/keyword data from job descriptions (cached per job.id, 1h TTL, 100-entry cap). CVModifier produces surgical LaTeX text replacements guided by the JobContext. CVEditor customizes the marker-delimited motivation-letter paragraph. All prompts in prompts.py wrap untrusted external data in `<untrusted_data>` XML tags.

- [ ] Read all 8 files in `backend/llm/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 3: latex module docstrings

**Files to modify:**
- `backend/latex/__init__.py`
- `backend/latex/pipeline.py`
- `backend/latex/compiler.py`
- `backend/latex/applicator.py`
- `backend/latex/injector.py`
- `backend/latex/parser.py`
- `backend/latex/validator.py`

**Context for this module:**
The LaTeX module is the CV tailoring pipeline. CVPipeline orchestrates JobAnalyzer → CVModifier → CVApplicator → Tectonic for CV documents. LetterPipeline uses LaTeXParser → CVEditor → LaTeXInjector → Tectonic for cover letters. CVApplicator is the safety gate: it only applies replacements with confidence ≥ 0.7 that are verbatim substrings of the current LaTeX and introduce no new LaTeX commands; caps at 3 replacements. LaTeXCompiler wraps Tectonic as an async subprocess. LaTeXParser extracts JOBPILOT marker-delimited sections (`% --- JOBPILOT:<MARKER>:START/END ---`).

- [ ] Read all 7 files in `backend/latex/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 4: applier module docstrings

**Files to modify:**
- `backend/applier/__init__.py`
- `backend/applier/engine.py`
- `backend/applier/auto_apply.py`
- `backend/applier/assisted_apply.py`
- `backend/applier/manual_apply.py`
- `backend/applier/form_filler.py`
- `backend/applier/captcha_handler.py`
- `backend/applier/daily_limit.py`

**Context for this module:**
The applier module executes job applications. ApplicationEngine enforces the daily limit (default 10), prevents concurrent in-flight applies to the same job, and dispatches to the correct strategy. AutoApplyStrategy uses Tier 1 (PlaywrightFormFiller: launches Chromium, gets Gemini to map applicant data to CSS selectors, fills fields, pauses for user WS confirmation before submitting) falling back to Tier 2 (browser-use agent). AssistedApplyStrategy fills but leaves the browser open for manual submission. ManualApplyStrategy opens the URL in the system browser. CaptchaHandler detects CAPTCHA challenges during apply flows.

- [ ] Read all 8 files in `backend/applier/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 5: matching module docstrings

**Files to modify:**
- `backend/matching/__init__.py`
- `backend/matching/matcher.py`
- `backend/matching/embedder.py`
- `backend/matching/fit_engine.py`
- `backend/matching/filters.py`
- `backend/matching/cv_parser.py`
- `backend/matching/job_skill_extractor.py`
- `backend/matching/skill_patterns.py`

**Context for this module:**
The matching module scores job listings against the user's profile. JobMatcher.score() takes a RawJob and JobFilters and returns a weighted float score. Jobs below `min_match_score` (default 30.0) are discarded by the batch runner. The embedder provides semantic similarity; fit_engine combines keyword hits, recency, and skill overlap into the final score. cv_parser extracts structured info from the user's CV LaTeX source. job_skill_extractor + skill_patterns extract required skills from job descriptions.

- [ ] Read all 8 files in `backend/matching/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 6: models module docstrings

**Files to modify:**
- `backend/models/__init__.py`
- `backend/models/base.py`
- `backend/models/application.py`
- `backend/models/document.py`
- `backend/models/job.py`
- `backend/models/session.py`
- `backend/models/user.py`
- `backend/models/schemas.py`

**Context for this module:**
The models module defines all SQLAlchemy ORM models and Pydantic DTOs. ORM models: UserProfile, SearchSettings, SiteCredential, JobSource, Job, JobMatch, TailoredDocument, Application, ApplicationEvent, BrowserSession. All FK relationships are application-enforced only (no DB-level FK constraints). Pydantic DTOs: RawJob (from scraper output), JobDetails (in-memory between scraping, matching, LLM layers). schemas.py contains Pydantic request/response models for the API layer.

- [ ] Read all 8 files in `backend/models/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 7: api module docstrings

**Files to modify:**
- `backend/api/__init__.py`
- `backend/api/jobs.py`
- `backend/api/queue.py`
- `backend/api/applications.py`
- `backend/api/documents.py`
- `backend/api/settings.py`
- `backend/api/analytics.py`
- `backend/api/ws.py`
- `backend/api/ws_models.py`
- `backend/api/deps.py`

**Context for this module:**
The api module contains thin FastAPI routers organised by domain vertical. No business logic lives in the API layer. deps.py provides FastAPI Depends helpers for DB session injection and singleton access from app.state. ws.py manages all real-time connections via ConnectionManager with asyncio.Lock-protected connection dict; exposes broadcast_status used by scraping and scheduling. ws_models.py defines the WebSocket message Pydantic models. All state (GeminiClient, CVPipeline, ApplicationEngine, etc.) lives on app.state, set during lifespan startup in main.py.

- [ ] Read all 10 files in `backend/api/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 8: scheduler module docstrings

**Files to modify:**
- `backend/scheduler/__init__.py`
- `backend/scheduler/morning_batch.py`

**Context for this module:**
The scheduler module orchestrates the full five-step batch pipeline: scrape → match → store → pre-generate CVs → notify. MorningBatchRunner is a singleton stored on app.state. It is triggered on-demand via POST /api/queue/refresh (APScheduler auto-start is intentionally disabled). CV pre-generation is parallelised with asyncio.Semaphore(3). Progress is broadcast via WebSocket at each stage (5%, 35%, 60%, 75%, 95%, 100%). morning_batch.py is the largest file at 23KB — document each private helper method with at least @brief.

- [ ] Read both files in `backend/scheduler/`
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

### Task 9: infra module docstrings

**Files to modify:**
- `backend/__init__.py`
- `backend/main.py`
- `backend/config.py`
- `backend/database.py`
- `backend/defaults.py`
- `backend/utils/__init__.py`
- `backend/utils/retry.py`
- `backend/utils/source_health.py`
- `backend/utils/browser_path.py`
- `backend/security/__init__.py`
- `backend/security/sanitizer.py`

**Context for this module:**
config.py defines the pydantic-settings Settings class and exposes a `settings` singleton; three fields (GOOGLE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY) have no defaults and cause fatal ValidationError at startup if absent. database.py builds the async SQLAlchemy infrastructure: async engine (sqlite+aiosqlite, WAL mode), AsyncSessionLocal factory, db_session() for service code (auto-commit/rollback), get_db() for FastAPI Depends. main.py constructs the FastAPI app, registers middleware and all API routers, implements the lifespan context manager (data dirs → DB init → singleton construction → WS handler wiring), and mounts the SvelteKit SPA. defaults.py defines DAILY_LIMIT and other runtime defaults. utils/retry.py wraps async callables with exponential back-off. utils/source_health.py tracks per-source scraping success/failure rates. utils/browser_path.py resolves the Playwright/Patchright browser binary path. security/sanitizer.py sanitizes LLM prompt inputs to prevent prompt injection.

- [ ] Read all 11 files
- [ ] Add Doxygen docstrings to every module, class, public method, private method (brief only), and public attribute following the standard above
- [ ] Verify: no imports changed, no logic changed, no signatures changed
- [ ] Verify: every module docstring has `Depends on:` and `Called by:` sections

---

## Chunk 2: Docs Rebuild — 1 agent

> This chunk can run in parallel with Chunk 1 since it reads source code directly.

---

### Task 10: Rebuild all docs

**Delete first** (do NOT delete `docs/superpowers/`):
```
docs/index.md
docs/architecture.md
docs/api-reference.md
docs/code-review.md
docs/custom-templates.md
docs/modules/api.md
docs/modules/applier.md
docs/modules/latex.md
docs/modules/llm.md
docs/modules/scraping.md
docs/modules/models.md
docs/modules/scheduler.md
docs/modules/config-database.md
docs/modules/frontend.md
```

**Then write the following files. Read ALL source files first, then write.**

---

#### Step 1: Read all source files

- [ ] Read `backend/main.py`, `backend/config.py`, `backend/database.py`, `backend/defaults.py`
- [ ] Read all files in `backend/api/`, `backend/applier/`, `backend/latex/`, `backend/llm/`
- [ ] Read all files in `backend/matching/`, `backend/models/`, `backend/scheduler/`, `backend/scraping/`
- [ ] Read all files in `backend/utils/`, `backend/security/`
- [ ] Read existing `docs/superpowers/specs/2026-03-16-documentation-overhaul-design.md` for reference

---

#### Step 2: Write `docs/file-map.md`

A flat table of every Python file in `backend/`. Format:

```markdown
# File Map

Every Python file in `backend/`, its responsibility, what it imports internally, and what imports it.

| File | Responsibility | Depends on (internal) | Called by |
|------|---------------|----------------------|-----------|
| `backend/main.py` | FastAPI app factory, lifespan startup, singleton wiring, SPA static mount | config, database, all api/*, all service singletons | uvicorn entry point |
| `backend/config.py` | Pydantic-settings singleton; all env var definitions and defaults | — | everything |
| ... (all ~71 files) | ... | ... | ... |
```

- [ ] Write `docs/file-map.md` covering all ~71 Python files

---

#### Step 3: Write `docs/index.md`

Developer navigation hub. Must include:
- One-paragraph system summary
- Table linking to every doc file with a description of its audience/purpose
- Module dependency diagram (text-based or Mermaid) showing which modules call which
- Quick navigation section ("I want to understand X → go here")

- [ ] Write `docs/index.md`

---

#### Step 4: Write `docs/architecture.md`

Rewrite from scratch based on current code. Must include:
- System overview (2-3 paragraphs)
- Mermaid component diagram (frontend → API → services → external)
- Three request lifecycle walkthroughs: Morning Batch, Apply Flow, CV Tailoring Flow
- Full database schema (all 10 tables, all columns, types, constraints)
- Key design decisions section (two-tier scraping, two-tier apply, no-auth, marker-based LaTeX editing, APScheduler disabled, Fernet encryption, async SQLite WAL)
- Deployment section (dependencies, env vars, start commands, data directory layout)

- [ ] Write `docs/architecture.md`

---

#### Step 5: Write `docs/api-reference.md`

Rewrite from scratch based on current router code in `backend/api/`. Must include:
- Base URL, auth note (no auth, local only)
- Every REST endpoint: method + path, description, query params (table), request body schema, response schema with example JSON, error responses
- WebSocket protocol section: connection URL, all message types (inbound + outbound), field descriptions, example JSON for each message type

Routers to cover: `jobs.py`, `queue.py`, `applications.py`, `documents.py`, `settings.py`, `analytics.py`, `ws.py`

- [ ] Write `docs/api-reference.md`

---

#### Step 6: Write `docs/code-review.md`

Fresh audit of current code. Read all source files and produce a new findings list. Do NOT copy from the old `code-review.md` — audit the actual current code. Format:

```markdown
# Code Review: Production Readiness

## Summary
- Total findings: N (Critical: X, High: Y, Medium: Z, Low: W)
- Overall assessment: ...

## Critical
**[CRIT-1]** `file:line` — **Issue title**
Description. *Fix:* ...

## High
...
```

- [ ] Write `docs/code-review.md`

---

#### Step 7: Write `docs/modules/scraping.md`

Must include:
- Module purpose (1-2 paragraphs)
- Two-tier architecture explanation (Tier 1 ScraplingFetcher vs Tier 2 AdaptiveScraper)
- Component table: class name → file → one-line description
- Data flow: input → each component → output
- Key classes with their public interface (method signatures + brief descriptions)
- Cross-links to `file-map.md` and `docs/architecture.md#morning-batch-flow`

- [ ] Write `docs/modules/scraping.md`

---

#### Step 8: Write `docs/modules/llm.md`

Must include:
- Module purpose
- GeminiClient internals: rate limiter (15 RPM sliding window), fallback model chain, retry logic
- Each client class: JobAnalyzer (caching), CVModifier (LaTeX replacements), CVEditor (letter paragraph)
- Prompt injection safety pattern (`<untrusted_data>` tags in prompts.py)
- Cross-links to `file-map.md`

- [ ] Write `docs/modules/llm.md`

---

#### Step 9: Write `docs/modules/latex.md`

Must include:
- Module purpose
- CVPipeline vs LetterPipeline (parallel structure)
- CVApplicator safety gate (confidence ≥ 0.7, verbatim check, no-new-commands check, cap 3)
- Marker-based editing (`% --- JOBPILOT:<MARKER>:START/END ---`)
- LaTeXCompiler Tectonic subprocess wrapper
- Cross-links to `file-map.md` and `docs/architecture.md#cv-tailoring-flow`

- [ ] Write `docs/modules/latex.md`

---

#### Step 10: Write `docs/modules/applier.md`

Must include:
- Module purpose
- ApplicationEngine: daily limit guard, in-flight concurrency guard, strategy dispatch
- Three strategies: Auto (Tier 1 → Tier 2, WS review gate), Assisted (fill only, browser stays open), Manual (system browser)
- PlaywrightFormFiller flow: Chromium context → CAPTCHA check → HTML skeleton → Gemini field mapping → fill → WS confirm/cancel
- Cross-links to `file-map.md` and `docs/architecture.md#apply-flow`

- [ ] Write `docs/modules/applier.md`

---

#### Step 11: Write `docs/modules/matching.md`

Must include:
- Module purpose
- Scoring algorithm (weighted keyword hits, recency, skill overlap → float score)
- JobMatcher.score() inputs and output
- How min_match_score filters results
- Cross-links to `file-map.md`

- [ ] Write `docs/modules/matching.md`

---

#### Step 12: Write `docs/modules/models.md`

Must include:
- Module purpose (ORM models vs Pydantic DTOs)
- All 10 ORM tables with columns, types, and notes (same format as architecture.md DB schema but cross-linked)
- Pydantic DTOs: RawJob, JobDetails, and their usage (in-memory between scraping/matching/LLM)
- FK relationship table (application-enforced only, no DB-level constraints)
- Cross-links to `file-map.md` and `docs/architecture.md#database-schema`

- [ ] Write `docs/modules/models.md`

---

#### Step 13: Write `docs/modules/scheduler.md`

Must include:
- Module purpose
- Batch pipeline steps (5 steps with WS progress percentages)
- asyncio.Semaphore(3) for CV pre-generation concurrency
- APScheduler present but auto-start disabled (on-demand only via POST /api/queue/refresh)
- Cross-links to `file-map.md` and `docs/architecture.md#morning-batch-flow`

- [ ] Write `docs/modules/scheduler.md`

---

#### Step 14: Write `docs/modules/config-database.md`

Must include:
- config.py: all env vars with types, defaults, and descriptions (same completeness as README but more technical)
- database.py: async engine setup, WAL mode rationale, session patterns (db_session vs get_db), init_db seed behavior
- main.py: lifespan startup sequence (exact order: data dirs → DB init → singletons → WS handlers → SPA mount), SPAStaticFiles cache-control logic, global exception handlers
- Cross-links to `file-map.md`

- [ ] Write `docs/modules/config-database.md`

---

#### Step 15: Write `docs/modules/api.md`

Must include:
- Module purpose (thin routers, no business logic)
- deps.py patterns (get_db, singleton access from app.state)
- WebSocket ConnectionManager: asyncio.Lock, connection dict, registered handlers, broadcast_status
- All WS message types (same as api-reference.md WS section but with internal code context)
- Cross-links to `file-map.md` and `docs/api-reference.md`

- [ ] Write `docs/modules/api.md`

---

#### Step 16: Write `docs/modules/security.md`

Must include:
- Module purpose
- InputSanitizer: what it sanitizes, how, which callers use it
- Fernet credential encryption: key source (CREDENTIAL_KEY env var), what is encrypted (site email + password), where decryption happens
- Security boundary summary (local 127.0.0.1 only, no auth layer, open CORS)
- Cross-links to `file-map.md`

- [ ] Write `docs/modules/security.md`

---

#### Step 17: Verify completeness

- [ ] Confirm all files listed in the delete list above are gone
- [ ] Confirm `docs/superpowers/` is untouched
- [ ] Confirm every module in `docs/modules/` has a cross-link back to `docs/file-map.md`
- [ ] Confirm `docs/index.md` links to every file in `docs/`
