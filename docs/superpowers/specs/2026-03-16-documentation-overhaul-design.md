# Design: JobPilot Documentation Overhaul

**Date:** 2026-03-16
**Audience:** Developer
**Goal:** Delete all existing docs, rewrite from scratch, and add full Doxygen-style inline docstrings to every Python file across the codebase.

---

## 1. Scope

### What changes
- **All `docs/` content except `docs/superpowers/`** — deleted and rewritten from the actual current code (`docs/superpowers/` is preserved — it contains this spec)
- **All `backend/**/*.py` files** — full Doxygen-compatible docstrings added at module, class, method, and attribute level

### What does not change
- No logic, no imports, no signatures — code behaviour is untouched
- Frontend (`.svelte`, `.ts`) — out of scope for this pass

---

## 2. Inline Docstring Standard

Every Python file gets Doxygen-compatible docstrings using this format:

### Module level (top of every `.py` file)
```python
"""
@file       scraping/orchestrator.py
@brief      One-line description of what this file is.
@details    Longer explanation of responsibilities, design decisions,
            and how this file fits in the system.
            Depends on: <list of internal modules/classes it imports>
            Called by:  <list of modules/classes that import this>
"""
```

### Class level
```python
class Foo:
    """
    @brief   One-line summary.
    @details Multi-line explanation of what this class owns,
             its lifecycle, and any important invariants.
    """
```

### Method level
- **Public methods** (names not starting with `_`): full tags
- **Private/protected methods** (names starting with `_` or `__`): `@brief` only

```python
def bar(self, x: int, y: str) -> bool:
    """
    @brief   One-line summary.
    @param   x    Description of x.
    @param   y    Description of y.
    @return  True if ..., False otherwise.
    @raises  ValueError  If x is negative.  ← omit this tag entirely if the method does not raise
    @note    Any important side-effects or caveats.  ← omit if not needed
    """
```

### Attribute level
```python
class Foo:
    max_retries: int  #: @brief Maximum number of retry attempts before giving up.
```

### `__init__.py` files (re-export stubs)
Even files that only contain `from . import ...` or are empty get a module docstring:
```python
"""
@file       scraping/__init__.py
@brief      Public re-exports for the scraping package.
@details    Exports: ScrapingOrchestrator, AdzunaClient, AdaptiveScraper,
            ScraplingFetcher, BrowserSessionManager, JobDeduplicator.
"""
```
If a `__init__.py` is completely empty (no exports), write:
```python
"""
@file       security/__init__.py
@brief      Package marker for backend.security. No public re-exports.
"""
```

---

## 3. New `docs/` Structure

All existing files in `docs/` are deleted. The new structure is:

```
docs/
├── index.md                 ← Developer nav hub + module dependency table
├── architecture.md          ← System overview, Mermaid component diagram, 3 request
│                               lifecycles, DB schema, key design decisions, deployment
├── api-reference.md         ← All REST endpoints with current accurate signatures + WS protocol
├── code-review.md           ← Fresh audit of current code: security, reliability, quality
├── file-map.md              ← Flat table: every .py file → responsibility + depends-on + called-by
└── modules/
    ├── api.md               ← Routers, dependency injection, WS ConnectionManager
    ├── applier.md           ← ApplicationEngine, two-tier strategy, DailyLimitGuard
    ├── latex.md             ← CVPipeline, LetterPipeline, CVApplicator safety gate, Tectonic
    ├── llm.md               ← GeminiClient rate limiter, JobAnalyzer, CVModifier, CVEditor, prompts
    ├── scraping.md          ← Orchestrator, ScraplingFetcher (T1), AdaptiveScraper (T2), sessions
    ├── matching.md          ← JobMatcher, embedder, fit engine, filters, skill patterns
    ├── models.md            ← All ORM models, DTOs (RawJob/JobDetails), logical FK relationships
    ├── scheduler.md         ← MorningBatchRunner, semaphore concurrency, WS progress events
    ├── security.md          ← InputSanitizer, Fernet credential encryption
    └── config-database.md  ← Settings (all env vars), async DB engine, lifespan startup sequence
```

### `file-map.md` format
A flat table covering every `.py` file in `backend/`:

| File | Responsibility | Depends on (internal) | Called by |
|------|---------------|----------------------|-----------|
| `backend/main.py` | FastAPI app factory, lifespan, singleton wiring, SPA mount | config, database, all api/*, all services | entry point (uvicorn) |
| `backend/config.py` | Pydantic-settings singleton, all env var definitions | — | everything |
| … | … | … | … |

---

## 4. Agent Assignment

11 agents run in parallel. Each agent is given its file list and the docstring standard above. Agents do not communicate.

| Agent | Owns | Files (including `__init__.py` for each package) |
|-------|------|-------|
| `docs-agent` | Rebuild all of `docs/` (except `docs/superpowers/`) | Reads entire `backend/` codebase to write accurate docs |
| `scraping-agent` | `backend/scraping/` | `__init__.py`, orchestrator, adaptive_scraper, scrapling_fetcher, adzuna_client, session_manager, deduplicator, site_prompts, json_utils |
| `llm-agent` | `backend/llm/` | `__init__.py`, gemini_client, job_analyzer, cv_modifier, cv_editor, job_context, validators, prompts |
| `latex-agent` | `backend/latex/` | `__init__.py`, pipeline, compiler, applicator, injector, parser, validator |
| `applier-agent` | `backend/applier/` | `__init__.py`, engine, auto_apply, assisted_apply, manual_apply, form_filler, captcha_handler, daily_limit |
| `matching-agent` | `backend/matching/` | `__init__.py`, matcher, embedder, fit_engine, filters, cv_parser, job_skill_extractor, skill_patterns |
| `models-agent` | `backend/models/` | `__init__.py`, base, application, document, job, session, user, schemas |
| `api-agent` | `backend/api/` | `__init__.py`, jobs, queue, applications, documents, settings, analytics, ws, ws_models, deps |
| `scheduler-agent` | `backend/scheduler/` | `__init__.py`, morning_batch |
| `infra-agent` | `backend/` root + utils + security | `backend/__init__.py`, `utils/__init__.py`, `security/__init__.py`, main, config, database, defaults, utils/retry, utils/source_health, utils/browser_path, security/sanitizer |

---

## 5. Rules for All Agents

1. **Read before writing** — every agent reads all files in its scope before touching any
2. **No logic changes** — only docstrings and comments are added/modified
3. **No import changes** — existing imports stay exactly as-is
4. **Preserve existing docstrings** — if a docstring already exists and is accurate, update it; if it is wrong or missing, replace/add
5. **Cross-references use file paths** — `@see backend/scraping/orchestrator.py` not just class names
6. **`@details Depends on / Called by`** — every module docstring must include both sections

---

## 6. Success Criteria

- Every `.py` file in `backend/` has a `@file`/`@brief`/`@details`/`Depends on`/`Called by` module docstring
- Every public class has `@brief` + `@details`
- Every public method has `@brief`, `@param` per arg, `@return`, `@raises` (if applicable)
- Every public attribute has an `#:` inline comment
- `docs/file-map.md` covers all ~71 Python files (including all `__init__.py` stubs) with accurate dependency columns
- All module docs cross-link to each other and to `file-map.md`
- `docs/code-review.md` reflects current code state (not the old 38-finding audit)
