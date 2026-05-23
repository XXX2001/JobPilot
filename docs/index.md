# JobPilot Documentation

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via Gemini, and applies at scale.

---

## Documentation Index

| Document | Audience | Description |
|---|---|---|
| [Changelog](../CHANGELOG.md) | Everyone | Per-PR changelog. Start here to see what shipped most recently. |
| [Architecture](architecture.md) | Developers | System overview, component diagram, request lifecycles, DB schema |
| [API Reference](api-reference.md) | Developers / Integrators | All REST endpoints + WebSocket protocol |
| [File Map](file-map.md) | Developers | Every backend file, its responsibility, internal deps, callers |
| [File Map (frontend)](frontend-file-map.md) | Developers | Every SvelteKit file, its responsibility, internal deps, backend endpoints used |
| [Code Review](code-review.md) | Project / QA | Production-readiness findings: security, reliability, quality (38 findings) |

## Reports & Audits

| Report | Date | What's inside |
|---|---|---|
| [Pre-ship hardening audit](reports/2026-05-22-audit/INDEX.md) | 2026-05-22 | 8 parallel deep-dives (LLM / DB / Gmail / FE / API / perf / tests / ops) + the cross-cutting top-12 attack list |
| [Post-sprint verification](reports/2026-05-22-audit/POST-SPRINT-VERIFICATION.md) | 2026-05-22 | What was actually fixed in the 12-PR sprint, per-PR verdicts, residual baseline |
| [Forward-looking improvements](reports/2026-05-23-improvements/INDEX.md) | 2026-05-23 | UX friction + backend refactors + product gaps. The "what's the next sprint?" report. |
| [Standards backlog](reports/2026-05-22-standards/INDEX.md) | 2026-05-22 | Naming / error-handling / lint standards spec |

## Module Reference

| Module | Description |
|---|---|
| [API](modules/api.md) | FastAPI routers, all endpoints, WebSocket protocol |
| [Applier](modules/applier.md) | Apply engine, two-tier strategy (Playwright Tier 1 + browser-use Tier 2) |
| [LaTeX](modules/latex.md) | CV tailoring pipeline: parse → safety-gated inject → Tectonic compile |
| [LLM](modules/llm.md) | Gemini 2.0 Flash client, CV editor/modifier, job analyzer, prompts |
| [Scraping](modules/scraping.md) | Orchestrator, two-tier fetcher (Scrapling + browser-use), session manager |
| [Models](modules/models.md) | SQLAlchemy models, full DB schema, Alembic migrations |
| [Scheduler](modules/scheduler.md) | On-demand batch runner (CV tailoring pipeline trigger) |
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
→ [Code Review](code-review.md) and [Pre-ship audit reports](reports/2026-05-22-audit/INDEX.md)

### "I want to see what shipped recently"
→ [Changelog](../CHANGELOG.md)

### "I want to know what to build next"
→ [Forward-looking improvements report](reports/2026-05-23-improvements/INDEX.md)

### "I want to install and run JobPilot"
→ [Architecture — Deployment section](architecture.md#deployment)
