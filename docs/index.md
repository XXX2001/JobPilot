# JobPilot Documentation

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via Gemini, and applies at scale.

---

## Documentation Index

| Document | Audience | Description |
|---|---|---|
| [Architecture](architecture.md) | Developers | System overview, component diagram, request lifecycles, DB schema |
| [API Reference](api-reference.md) | Developers / Integrators | All REST endpoints + WebSocket protocol |
| [Code Review](code-review.md) | Project / QA | Production-readiness findings: security, reliability, quality (38 findings) |

## Module Reference

| Module | Description |
|---|---|
| [API](modules/api.md) | FastAPI routers, all endpoints, WebSocket protocol |
| [Applier](modules/applier.md) | Apply engine, two-tier strategy (Playwright Tier 1 + browser-use Tier 2) |
| [LaTeX](modules/latex.md) | CV tailoring pipeline: parse → safety-gated inject → Tectonic compile |
| [LLM](modules/llm.md) | Gemini 2.0 Flash client, CV editor/modifier, job analyzer, prompts |
| [Scraping](modules/scraping.md) | Orchestrator, two-tier fetcher (Scrapling + browser-use), session manager |
| [Models](modules/models.md) | SQLAlchemy models, full DB schema, Alembic migrations |
| [Scheduler](modules/scheduler.md) | APScheduler morning batch job |
| [Config & Database](modules/config-database.md) | Settings (all env vars), DB engine, FastAPI app entry point |
| [Frontend](modules/frontend.md) | SvelteKit routes, pages, API integration, WebSocket usage |

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

### "I want to install and run JobPilot"
→ [Architecture — Deployment section](architecture.md#deployment)
