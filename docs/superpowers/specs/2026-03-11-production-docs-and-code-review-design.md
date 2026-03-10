# Design: Production Documentation & Code Review

**Date**: 2026-03-11
**Status**: Approved
**Project**: JobPilot

---

## Goal

Prepare the JobPilot codebase for production by:
1. Archiving stale docs
2. Generating fresh, accurate module-level reference docs (one per module)
3. Synthesizing system-wide architecture docs
4. Performing a structured code correctness investigation (security, quality, architecture, testing)

---

## Approach: Parallel Wave Subagents

Three sequential waves, with Wave 1 fully parallelized.

---

## Wave 0 — Archive (main thread)

Move all existing `docs/*.md` files to `docs/plans/archive/` to preserve them without cluttering the new docs root.

Files to archive:
- `docs/architecture.md`
- `docs/developer-guide.md`
- `docs/api-overview.md`
- `docs/operations.md`
- `docs/overview.md`
- `docs/troubleshooting.md`
- `docs/verification-gap-analysis.md`
- `docs/index.md`

---

## Wave 1 — Module Documentation (parallel subagents)

Each subagent reads its assigned files and writes one markdown doc to `docs/modules/`.

### Subagent Assignments

| Subagent | Output | Source files |
|---|---|---|
| `api-docs` | `docs/modules/api.md` | `backend/api/` (all files) |
| `applier-docs` | `docs/modules/applier.md` | `backend/applier/` (all files) |
| `latex-docs` | `docs/modules/latex.md` | `backend/latex/` (all files) |
| `llm-docs` | `docs/modules/llm.md` | `backend/llm/` (all files) |
| `scraping-docs` | `docs/modules/scraping.md` | `backend/scraping/` (all files) |
| `models-docs` | `docs/modules/models.md` | `backend/models/` + `alembic/` |
| `scheduler-docs` | `docs/modules/scheduler.md` | `backend/scheduler/` |
| `config-docs` | `docs/modules/config-database.md` | `backend/config.py`, `backend/database.py`, `backend/main.py` |
| `frontend-docs` | `docs/modules/frontend.md` | `frontend/src/routes/` (all pages) |

### Module Doc Format

Each `docs/modules/<name>.md` must contain:

```markdown
# Module: <Name>

## Purpose
One paragraph: what this module does and why it exists.

## Key Components
### <filename.py>
What this file does, its role in the module.

## Public Interface
Key classes and functions with signatures and descriptions.

## Data Flow
How data enters and exits this module (inputs, outputs, side effects).

## Configuration
Any env vars, settings, or config keys this module reads.

## Known Limitations / TODOs
Identified gaps, hardcoded values, missing features.
```

---

## Wave 2 — Architecture Synthesis (single subagent)

**Inputs**: All 9 module docs + `backend/main.py`, `backend/database.py`, `backend/models/`

**Outputs**:

### `docs/architecture.md`
- System overview (1 paragraph)
- Component diagram (ASCII or Mermaid)
- Request lifecycle (scraping → matching → tailoring → applying)
- Data flows between modules
- DB schema overview
- Key design decisions and trade-offs

### `docs/api-reference.md`
- Every REST endpoint: method, path, params, request body, response shape
- WebSocket protocol: events, message formats
- Auth/deps model

---

## Wave 3 — Code Review (single subagent)

**Inputs**: All `.py` files in `backend/` + all `.svelte` files in `frontend/`

**Output**: `docs/code-review.md`

### Structure

```markdown
# Code Review: Correctness & Production Readiness

## Critical (security vulnerabilities, data loss risks)
- `file:line` — description — recommended fix

## High (architecture violations, missing error handling)
- ...

## Medium (code quality, type hints, dead code)
- ...

## Low (naming, style, minor TODOs)
- ...

## Summary
Count of findings per category, overall assessment.
```

### Review Domains
1. **Security**: input validation, SQL injection via raw queries, secret/API key handling, CORS config, auth bypass risks, prompt injection in LLM calls
2. **Code quality**: missing type hints, bare `except`, unused imports/variables, hardcoded values that should be config, missing error handling at boundaries
3. **Architecture**: module coupling, circular imports, single responsibility violations, files that are too large/doing too much
4. **Testing gaps**: untested code paths, missing edge cases, integration vs unit test balance

---

## Final State: `docs/` Layout

```
docs/
├── plans/
│   ├── archive/                    ← moved from docs/ root
│   └── (existing plan files)
├── modules/
│   ├── api.md
│   ├── applier.md
│   ├── latex.md
│   ├── llm.md
│   ├── models.md
│   ├── scheduler.md
│   ├── scraping.md
│   ├── config-database.md
│   └── frontend.md
├── architecture.md
├── api-reference.md
├── code-review.md
└── index.md
```

---

## Success Criteria

- All existing docs archived to `docs/plans/archive/`
- 9 module docs written, each following the template
- `docs/architecture.md` covers full system with component diagram
- `docs/api-reference.md` covers every endpoint and WebSocket event
- `docs/code-review.md` has findings with file:line references across all 4 domains
- `docs/index.md` updated to reflect new structure
