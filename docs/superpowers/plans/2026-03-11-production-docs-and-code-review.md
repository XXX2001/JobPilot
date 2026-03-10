# Production Documentation & Code Review Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive stale docs, generate accurate module-level reference docs and system architecture docs via parallel subagents, then run a structured code correctness investigation across all four domains (security, quality, architecture, testing).

**Architecture:** Three sequential waves. Wave 0 archives existing docs. Wave 1 spawns 9 parallel subagents — one per module — each reading source files and writing a reference doc to `docs/modules/`. Wave 2 synthesizes a system architecture doc and full API reference from the module docs. Wave 3 performs a structured code review across the entire codebase and writes findings to `docs/code-review.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (async), SvelteKit, Gemini 2.0 Flash, Playwright, browser-use, APScheduler, Tectonic LaTeX, SQLite/aiosqlite

---

## Chunk 1: Wave 0 — Archive Existing Docs

### Task 1: Create archive directory and move stale docs

**Files:**
- Create dir: `docs/plans/archive/`
- Move: `docs/architecture.md` → `docs/plans/archive/architecture.md`
- Move: `docs/developer-guide.md` → `docs/plans/archive/developer-guide.md`
- Move: `docs/api-overview.md` → `docs/plans/archive/api-overview.md`
- Move: `docs/operations.md` → `docs/plans/archive/operations.md`
- Move: `docs/overview.md` → `docs/plans/archive/overview.md`
- Move: `docs/troubleshooting.md` → `docs/plans/archive/troubleshooting.md`
- Move: `docs/verification-gap-analysis.md` → `docs/plans/archive/verification-gap-analysis.md`
- Move: `docs/index.md` → `docs/plans/archive/index.md`

- [ ] **Step 1: Create archive directory**

```bash
mkdir -p docs/plans/archive
```

- [ ] **Step 2: Move all stale docs**

```bash
mv docs/architecture.md docs/developer-guide.md docs/api-overview.md \
   docs/operations.md docs/overview.md docs/troubleshooting.md \
   docs/verification-gap-analysis.md docs/index.md \
   docs/plans/archive/
```

- [ ] **Step 3: Create modules directory**

```bash
mkdir -p docs/modules
```

- [ ] **Step 4: Verify structure**

```bash
ls docs/plans/archive/
ls docs/modules/
ls docs/
```

Expected: 8 files in archive, empty modules dir, no `.md` files in docs root (only `plans/` and `modules/` and `superpowers/` dirs).

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: archive stale docs to docs/plans/archive/"
```

---

## Chunk 2: Wave 1 — Parallel Module Documentation

Spawn all 9 subagents simultaneously. Each subagent reads its assigned source files and writes one doc following the template below.

**Module doc template (every subagent must use this exact structure):**

```markdown
# Module: <Name>

## Purpose
One paragraph: what this module does, why it exists, and its role in the overall JobPilot system.

## Key Components

### `filename.py`
What this file does and its role within the module.

## Public Interface

### `ClassName` / `function_name(params) -> return_type`
Description of what it does, key parameters, return value, side effects.

## Data Flow
How data enters and exits this module: what it receives as input, what it produces as output, what external systems it reads from or writes to.

## Configuration
Environment variables, settings keys, or config values this module reads. Reference `backend/config.py` field names where applicable.

## Known Limitations / TODOs
Any hardcoded values that should be config, missing features, identified gaps, or TODO comments found in the source.
```

### Task 2: Spawn 9 parallel documentation subagents

- [ ] **Step 1: Dispatch all 9 subagents in parallel**

Each subagent is a **general-purpose** agent. Spawn all at once in a single message with 9 Agent tool calls.

**Subagent 1 — api-docs**
Reads: `backend/api/__init__.py`, `backend/api/deps.py`, `backend/api/analytics.py`, `backend/api/applications.py`, `backend/api/documents.py`, `backend/api/jobs.py`, `backend/api/queue.py`, `backend/api/settings.py`, `backend/api/ws.py`, `backend/api/ws_models.py`
Writes: `docs/modules/api.md`

Prompt:
```
Read all files in backend/api/ (deps.py, analytics.py, applications.py, documents.py, jobs.py, queue.py, settings.py, ws.py, ws_models.py, __init__.py) in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/api.md using this exact structure:

# Module: API

## Purpose
## Key Components
(one subsection per file)
## Public Interface
(document every FastAPI router, every endpoint with its HTTP method + path + request params + response model, and the WebSocket protocol in ws.py with all event types and message formats)
## Data Flow
## Configuration
## Known Limitations / TODOs

Be thorough — this is production documentation. Include every endpoint, its path, HTTP method, query/path params, request body schema, response schema, and any auth dependencies used. For WebSocket, document every message type and its fields from ws_models.py.
```

**Subagent 2 — applier-docs**
Reads: `backend/applier/` (all 8 files)
Writes: `docs/modules/applier.md`

Prompt:
```
Read all files in backend/applier/ (engine.py, auto_apply.py, assisted_apply.py, manual_apply.py, form_filler.py, captcha_handler.py, daily_limit.py, __init__.py) in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/applier.md using this exact structure:

# Module: Applier

## Purpose
## Key Components
(one subsection per file — explain the two-tier apply strategy: Tier 1 = PlaywrightFormFiller, Tier 2 = browser-use LLM agent)
## Public Interface
(document every class and public method with signatures, parameters, return types)
## Data Flow
(document the full apply pipeline: how a job application flows from engine.apply() through strategy selection to browser automation)
## Configuration
## Known Limitations / TODOs

Be thorough. Document the AutoApplyStrategy, AssistedApplyStrategy, PlaywrightFormFiller, CaptchaHandler, and DailyLimitTracker classes in detail.
```

**Subagent 3 — latex-docs**
Reads: `backend/latex/` (all files)
Writes: `docs/modules/latex.md`

Prompt:
```
Read all files in backend/latex/ (pipeline.py, applicator.py, compiler.py, injector.py, parser.py, validator.py, __init__.py) in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/latex.md using this exact structure:

# Module: LaTeX

## Purpose
## Key Components
(one subsection per file)
## Public Interface
(document every class and public function with signatures, parameters, return types)
## Data Flow
(document the CV tailoring pipeline: parse → inject edits → compile → validate)
## Configuration
## Known Limitations / TODOs

Be thorough. This module transforms LaTeX CV source files by surgically injecting LLM-generated edits and compiling them with Tectonic.
```

**Subagent 4 — llm-docs**
Reads: `backend/llm/` (all files)
Writes: `docs/modules/llm.md`

Prompt:
```
Read all files in backend/llm/ (gemini_client.py, cv_editor.py, cv_modifier.py, job_analyzer.py, job_context.py, prompts.py, validators.py, __init__.py) in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/llm.md using this exact structure:

# Module: LLM

## Purpose
## Key Components
(one subsection per file)
## Public Interface
(document every class and public function with signatures, parameters, return types — include prompt templates from prompts.py summarized but not fully reproduced)
## Data Flow
(document how job data flows into LLM calls and how CV edit instructions flow back out)
## Configuration
(document Gemini API key config, model selection, rate limits)
## Known Limitations / TODOs

Be thorough. This module wraps Gemini 2.0 Flash for CV tailoring, job analysis, and context extraction.
```

**Subagent 5 — scraping-docs**
Reads: `backend/scraping/` (all 9 files)
Writes: `docs/modules/scraping.md`

Prompt:
```
Read all files in backend/scraping/ (orchestrator.py, adaptive_scraper.py, scrapling_fetcher.py, session_manager.py, site_prompts.py, adzuna_client.py, deduplicator.py, json_utils.py, __init__.py) in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/scraping.md using this exact structure:

# Module: Scraping

## Purpose
## Key Components
(one subsection per file — explain the two-tier scraping system: Tier 1 = Scrapling HTTP fetcher, Tier 2 = browser-use LLM agent)
## Public Interface
(document every class and public function with signatures, parameters, return types)
## Data Flow
(document the full scraping pipeline: source discovery → fetch → LLM extraction → deduplication → storage)
## Configuration
(document all site configs, session management settings, Adzuna API keys)
## Known Limitations / TODOs

Be thorough. Document the ScraplingFetcher, AdaptiveScraper, SessionManager, Orchestrator, and site prompt system in detail.
```

**Subagent 6 — models-docs**
Reads: `backend/models/` (all files), `alembic/versions/` (all files), `alembic/env.py`
Writes: `docs/modules/models.md`

Prompt:
```
Read all files in backend/models/ (base.py, job.py, user.py, document.py, application.py, session.py, schemas.py, __init__.py) and alembic/versions/ (migration files) and alembic/env.py in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/models.md using this exact structure:

# Module: Models & Database

## Purpose
## Key Components
(one subsection per model file, one for schemas, one for alembic)
## Public Interface
(document every SQLAlchemy model: its table name, all columns with types and constraints, relationships)
## Data Flow
(document how models relate to each other — FK relationships, cascades)
## Configuration
## Known Limitations / TODOs

Include a full DB schema section with each table, its columns, types, and relationships. Document the Pydantic schemas in schemas.py.
```

**Subagent 7 — scheduler-docs**
Reads: `backend/scheduler/__init__.py`, `backend/scheduler/morning_batch.py`
Writes: `docs/modules/scheduler.md`

Prompt:
```
Read backend/scheduler/__init__.py and backend/scheduler/morning_batch.py in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/scheduler.md using this exact structure:

# Module: Scheduler

## Purpose
## Key Components
### `morning_batch.py`
## Public Interface
(document every function and class with signatures)
## Data Flow
(document what the morning batch job does step by step: what it reads, what it triggers, what it writes)
## Configuration
(document scheduling config: cron schedule, time windows, batch size limits)
## Known Limitations / TODOs

Be thorough. Document the full morning batch pipeline including which modules it calls and in what order.
```

**Subagent 8 — config-docs**
Reads: `backend/config.py`, `backend/database.py`, `backend/main.py`
Writes: `docs/modules/config-database.md`

Prompt:
```
Read backend/config.py, backend/database.py, and backend/main.py in the JobPilot project at /home/mouad/Web-automation.

Write a comprehensive reference doc to docs/modules/config-database.md using this exact structure:

# Module: Config, Database & Application Entry Point

## Purpose
## Key Components
### `config.py`
### `database.py`
### `main.py`
## Public Interface
(document every Settings field in config.py with its type, default, and env var name; document every database session/engine export; document FastAPI app setup and middleware)
## Data Flow
(document app startup sequence: config load → DB init → router registration → scheduler start)
## Configuration
(list every environment variable the app reads, with its type, default, and description)
## Known Limitations / TODOs
```

**Subagent 9 — frontend-docs**
Reads: `frontend/src/routes/+layout.svelte`, `frontend/src/routes/+page.svelte`, `frontend/src/routes/analytics/+page.svelte`, `frontend/src/routes/cv/+page.svelte`, `frontend/src/routes/jobs/[id]/+page.svelte`, `frontend/src/routes/settings/+page.svelte`, `frontend/src/routes/tracker/+page.svelte`
Writes: `docs/modules/frontend.md`

Prompt:
```
Read the following SvelteKit files in the JobPilot project at /home/mouad/Web-automation:
- frontend/src/routes/+layout.svelte
- frontend/src/routes/+page.svelte
- frontend/src/routes/analytics/+page.svelte
- frontend/src/routes/cv/+page.svelte
- frontend/src/routes/jobs/[id]/+page.svelte (look for [id] subdirectory)
- frontend/src/routes/settings/+page.svelte
- frontend/src/routes/tracker/+page.svelte

Write a comprehensive reference doc to docs/modules/frontend.md using this exact structure:

# Module: Frontend

## Purpose
## Key Components
(one subsection per route/page)
## Public Interface
(for each page: what API endpoints it calls, what data it displays, what user actions it supports)
## Data Flow
(document how data flows from backend API → frontend state → UI; document WebSocket usage)
## Configuration
(any env vars, API base URL config)
## Known Limitations / TODOs

Be thorough. Document what each page renders, what backend endpoints it calls, what reactive state it manages, and any notable UI patterns (diff viewer, PDF viewer, etc).
```

- [ ] **Step 2: Wait for all 9 subagents to complete**

Verify each file was created:

```bash
ls docs/modules/
```

Expected: 9 `.md` files: `api.md`, `applier.md`, `latex.md`, `llm.md`, `scraping.md`, `models.md`, `scheduler.md`, `config-database.md`, `frontend.md`

- [ ] **Step 3: Spot-check each doc has required sections**

```bash
for f in docs/modules/*.md; do
  echo "=== $f ===";
  grep "^## " "$f";
done
```

Expected: each doc has `## Purpose`, `## Key Components`, `## Public Interface`, `## Data Flow`, `## Configuration`, `## Known Limitations`

- [ ] **Step 4: Commit module docs**

```bash
git add docs/modules/
git commit -m "docs: add module reference docs for all 9 backend/frontend modules"
```

---

## Chunk 3: Wave 2 — Architecture Synthesis

### Task 3: Spawn architecture synthesis subagent

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/api-reference.md`

- [ ] **Step 1: Dispatch architecture subagent**

Agent type: **general-purpose**

Prompt:
```
You are writing production architecture documentation for JobPilot — an AI-powered local job application assistant built with FastAPI + SvelteKit + Gemini 2.0 Flash.

Read the following files (in this order) in /home/mouad/Web-automation:
1. docs/modules/config-database.md
2. docs/modules/models.md
3. docs/modules/api.md
4. docs/modules/scraping.md
5. docs/modules/llm.md
6. docs/modules/latex.md
7. docs/modules/applier.md
8. docs/modules/scheduler.md
9. docs/modules/frontend.md
10. backend/main.py
11. backend/config.py
12. backend/models/__init__.py

Then write TWO documents:

---

DOCUMENT 1: docs/architecture.md

Use this structure:
# JobPilot Architecture

## System Overview
2-3 paragraphs describing the system: what it does, how it's structured, key design philosophy.

## Component Diagram
ASCII or Mermaid diagram showing all major components and their relationships.

## Request Lifecycle
Step-by-step narrative of the two key flows:
1. Morning batch: scheduler → scraping → LLM extraction → DB storage → matching
2. Apply flow: user triggers apply → applier engine → strategy selection → Tier 1/2 execution → DB update

## Module Responsibilities
One paragraph per module summarizing its role and boundaries.

## Data Flow
How data moves between modules. Key: job data (scraped → analyzed → matched → stored → displayed → applied), CV data (uploaded → parsed → tailored → compiled → stored).

## Database Schema
All tables with columns, types, and relationships. Use a table format.

## Key Design Decisions
Bullet list of architectural decisions with rationale: async SQLAlchemy, SQLite, Gemini free tier, two-tier scraping, two-tier apply, browser-use, Tectonic LaTeX.

## Deployment
How to run the system locally. Reference pyproject.toml for dependencies.

---

DOCUMENT 2: docs/api-reference.md

Use this structure:
# API Reference

## Authentication
How auth works (deps.py model).

## REST Endpoints

For each endpoint document:
### `METHOD /path`
**Description:** what it does
**Auth required:** yes/no
**Path params:** ...
**Query params:** ...
**Request body:** JSON schema
**Response:** JSON schema
**Errors:** status codes and conditions

Group endpoints by router file (jobs, applications, documents, analytics, queue, settings).

## WebSocket Protocol

### `GET /ws`
Document the connection flow, all message types sent by server, all message types sent by client, message schemas.

Be exhaustive — document every endpoint found in backend/api/.
```

- [ ] **Step 2: Verify both files were created**

```bash
ls -la docs/architecture.md docs/api-reference.md
grep "^## " docs/architecture.md
grep "^### " docs/api-reference.md | head -20
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md docs/api-reference.md
git commit -m "docs: add architecture overview and full API reference"
```

---

## Chunk 4: Wave 3 — Code Review

### Task 4: Spawn code review subagent

**Files:**
- Create: `docs/code-review.md`

- [ ] **Step 1: Dispatch code review subagent**

Agent type: **general-purpose**

Prompt:
```
You are performing a production-readiness code review of JobPilot — an AI-powered job application assistant at /home/mouad/Web-automation.

Read ALL of the following source files:

Backend Python files:
- backend/config.py
- backend/database.py
- backend/main.py
- backend/models/base.py, job.py, user.py, document.py, application.py, session.py, schemas.py, __init__.py
- backend/api/deps.py, analytics.py, applications.py, documents.py, jobs.py, queue.py, settings.py, ws.py, ws_models.py
- backend/applier/engine.py, auto_apply.py, assisted_apply.py, manual_apply.py, form_filler.py, captcha_handler.py, daily_limit.py
- backend/latex/pipeline.py, applicator.py, compiler.py, injector.py, parser.py, validator.py
- backend/llm/gemini_client.py, cv_editor.py, cv_modifier.py, job_analyzer.py, job_context.py, prompts.py, validators.py
- backend/scraping/orchestrator.py, adaptive_scraper.py, scrapling_fetcher.py, session_manager.py, site_prompts.py, adzuna_client.py, deduplicator.py, json_utils.py
- backend/scheduler/morning_batch.py

Frontend Svelte files:
- frontend/src/routes/+layout.svelte
- frontend/src/routes/+page.svelte
- frontend/src/routes/analytics/+page.svelte
- frontend/src/routes/cv/+page.svelte
- frontend/src/routes/settings/+page.svelte
- frontend/src/routes/tracker/+page.svelte

After reading ALL files, write docs/code-review.md with this exact structure:

# Code Review: Production Readiness

## Summary
- Total findings: N (Critical: X, High: Y, Medium: Z, Low: W)
- Overall assessment: 1-2 sentences

## Critical — Security & Data Loss Risks
Issues that could cause security vulnerabilities, data loss, or production outages.

Format each finding as:
**[CRIT-N]** `file.py:line` — **Issue title**
Description of the problem and why it is dangerous.
*Fix:* Specific code change or approach to fix it.

Cover:
- Input validation gaps (unsanitized user input reaching shell commands, LLM prompts, or DB queries)
- Secret/API key exposure (keys in logs, error messages, or config without validation)
- CORS configuration correctness
- Auth bypass possibilities in API endpoints
- Prompt injection risks in LLM calls (user-supplied text injected into system prompts)
- SQL injection via raw queries (if any)
- Path traversal in file operations

## High — Architecture & Reliability
Issues that affect correctness, error handling, or architectural integrity.

Format: **[HIGH-N]** `file.py:line` — **Issue title** / Description / *Fix:*

Cover:
- Missing error handling at system boundaries (API calls, file I/O, DB)
- Bare `except:` or `except Exception:` that swallows errors silently
- Resource leaks (browser instances, DB connections, file handles not closed)
- Blocking calls in async context (sync I/O inside async functions)
- Module coupling violations (modules importing from modules they shouldn't)
- Circular import risks

## Medium — Code Quality
Issues that affect maintainability and correctness over time.

Format: **[MED-N]** `file.py:line` — **Issue title** / Description / *Fix:*

Cover:
- Missing type hints on public functions
- Hardcoded values that should be in config (URLs, timeouts, limits, model names)
- Dead code (unreachable branches, unused imports, unused variables)
- Functions/methods that are too long (>50 lines) and should be split
- Missing docstrings on public classes and functions
- TODOs and FIXMEs in production code

## Low — Style & Polish
Minor issues that don't affect correctness but affect code quality.

Format: **[LOW-N]** `file.py:line` — **Issue title** / Description / *Fix:*

Cover:
- Inconsistent naming conventions
- Redundant comments that restate the code
- Magic numbers without named constants
- Svelte reactivity issues or anti-patterns in frontend files

Be thorough and specific. Every finding MUST include a real file path and line number from the actual source code you read. Do not invent findings — only report real issues found in the files. If a category has no issues, write "None found."
```

- [ ] **Step 2: Verify file was created and has content**

```bash
wc -l docs/code-review.md
grep "^## " docs/code-review.md
```

Expected: file exists, has all 4 severity sections, has a Summary section.

- [ ] **Step 3: Spot-check finding format**

```bash
grep "\`.*:.*\`" docs/code-review.md | head -10
```

Expected: findings with `file.py:line` format.

- [ ] **Step 4: Commit**

```bash
git add docs/code-review.md
git commit -m "docs: add production-readiness code review with file:line findings"
```

---

## Chunk 5: Final Index

### Task 5: Write updated docs/index.md

**Files:**
- Create: `docs/index.md`

- [ ] **Step 1: Write the new index**

Create `docs/index.md` with this content:

```markdown
# JobPilot Documentation

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via Gemini, and applies at scale.

---

## Documentation Index

| Document | Audience | Description |
|---|---|---|
| [Architecture](architecture.md) | Developers | System overview, component diagram, data flows, DB schema |
| [API Reference](api-reference.md) | Developers / Integrators | All REST endpoints + WebSocket protocol |
| [Code Review](code-review.md) | Project / QA | Production-readiness findings: security, quality, architecture, testing |

## Module Reference

| Module | Description |
|---|---|
| [API](modules/api.md) | FastAPI routers, endpoints, WebSocket |
| [Applier](modules/applier.md) | Apply engine, two-tier strategy (Playwright + browser-use) |
| [LaTeX](modules/latex.md) | CV tailoring pipeline: parse → inject → compile |
| [LLM](modules/llm.md) | Gemini 2.0 Flash client, CV editor, job analyzer |
| [Scraping](modules/scraping.md) | Orchestrator, two-tier fetcher, session manager |
| [Models](modules/models.md) | SQLAlchemy models, DB schema, Alembic migrations |
| [Scheduler](modules/scheduler.md) | APScheduler morning batch job |
| [Config & Database](modules/config-database.md) | Settings, DB engine, FastAPI app entry point |
| [Frontend](modules/frontend.md) | SvelteKit routes, pages, API integration |

## Archive

Older documentation is preserved in [docs/plans/archive/](plans/archive/).

---

## Quick Navigation

### "I want to understand how it works"
→ [Architecture](architecture.md)

### "I want to call the API"
→ [API Reference](api-reference.md)

### "I want to understand a specific module"
→ [Module Reference](#module-reference) above

### "I want to see what needs to be fixed before production"
→ [Code Review](code-review.md)
```

- [ ] **Step 2: Verify**

```bash
cat docs/index.md
ls docs/
```

Expected: `index.md`, `architecture.md`, `api-reference.md`, `code-review.md`, `modules/`, `plans/`, `superpowers/`

- [ ] **Step 3: Final commit**

```bash
git add docs/index.md
git commit -m "docs: add updated index with module reference table"
```

- [ ] **Step 4: Final verification — all docs present**

```bash
echo "=== docs/ root ===" && ls docs/
echo "=== docs/modules/ ===" && ls docs/modules/
echo "=== docs/plans/archive/ ===" && ls docs/plans/archive/
echo "=== word counts ===" && wc -l docs/*.md docs/modules/*.md
```

Expected: 9 module docs, 4 root docs (index, architecture, api-reference, code-review), 8 archived docs.
