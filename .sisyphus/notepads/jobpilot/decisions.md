# JobPilot — Architectural Decisions

## [2026-02-28] Initial Decisions

### Database
- SQLite (local-first, zero config)
- SQLAlchemy 2.0 async (aiosqlite driver)
- Alembic for migrations from Day 1
- WAL mode for concurrent reads during batch
- All file paths stored relative to project root

### Frontend
- SvelteKit + adapter-static (outputs static files served by FastAPI)
- shadcn-svelte + TailwindCSS for Notion/Linear aesthetic
- Dark mode default (ModeWatcher, class-based)
- WebSocket for live updates (ws://localhost:8000/ws)
- pdf.js CDN for CV preview

### LLM
- Gemini 2.0 Flash (free tier: 15 RPM, 1500 RPD)
- Rate limiter: sliding window counter, 15 RPM hard cap
- All responses validated against Pydantic schemas before use
- JSON diff only — never raw LaTeX rewriting

### Scraping
- Adzuna REST API (250 free calls/day) as primary source
- browser-use + Playwright for other sites (LLM-driven, no hardcoded selectors)
- Deduplication: MD5 hash of normalized company|title|location

### Apply Engine
- Three modes: Auto (Easy Apply), Assisted (pre-fill + hand off), Manual (open URL)
- Daily limit: 10 applications enforced in DB
- max_steps=15 for scraping agents, max_steps=25 for apply agents
