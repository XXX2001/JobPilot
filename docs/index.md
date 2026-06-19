# JobPilot Documentation

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via your configured LLM, and applies at scale.

---

## Documentation Index

| Document | Audience | Description |
|---|---|---|
| [Changelog](../CHANGELOG.md) | Everyone | Per-PR changelog. Start here to see what shipped most recently. |
| [Architecture](architecture.md) | Developers | System overview, component diagram, request lifecycles, DB schema |
| [API Reference](api-reference.md) | Developers / Integrators | All REST endpoints + WebSocket protocol |
| [File Map](file-map.md) | Developers | Every backend file, its responsibility, internal deps, callers |
| [File Map (frontend)](frontend-file-map.md) | Developers | Every SvelteKit file, its responsibility, internal deps, backend endpoints used |
| [Code Review](code-review.md) | Developers | How to run a code review: quality gates and what reviewers check |

## Module Reference

| Module | Description |
|---|---|
| [API](modules/api.md) | FastAPI routers, all endpoints, WebSocket protocol |
| [Applier](modules/applier.md) | Apply engine, two-tier strategy (Playwright Tier 1 + browser-use Tier 2) |
| [LaTeX](modules/latex.md) | CV tailoring pipeline: parse → safety-gated inject → Tectonic compile |
| [LLM](modules/llm.md) | Provider-agnostic LLM clients + factory, CV editor/modifier, job analyzer, prompts |
| [Scraping](modules/scraping.md) | Orchestrator, two-tier fetcher (Scrapling + browser-use), session manager |
| [Models](modules/models.md) | SQLAlchemy models, full DB schema, Alembic migrations |
| [Scheduler](modules/scheduler.md) | On-demand batch runner (CV tailoring pipeline trigger) |
| [Config & Database](modules/config-database.md) | Settings (all env vars), DB engine, FastAPI app entry point |
| [Frontend](modules/frontend.md) | SvelteKit routes, pages, API integration, WebSocket usage |

---

## Quick Navigation

### "I want to understand how it works"
→ [Architecture](architecture.md)

### "I want to call the API"
→ [API Reference](api-reference.md)

### "I want to understand a specific module"
→ [Module Reference](#module-reference) above

### "I want to run a code review"
→ [Code Review](code-review.md)

### "I want to see what shipped recently"
→ [Changelog](../CHANGELOG.md)

### "I want to install and run JobPilot"
→ [Architecture — Deployment section](architecture.md#deployment)
