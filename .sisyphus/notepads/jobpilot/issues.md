# JobPilot — Issues & Gotchas

## [2026-02-28] Known Risks (from Metis review)

### TexSoup Reliability
- TexSoup is unreliable for marker detection
- SOLUTION: Use regex for `% --- JOBPILOT:*:START/END ---` markers, TexSoup only for `\section{}` fallback

### APScheduler + asyncio conflict
- APScheduler 3.x has issues with asyncio event loops
- SOLUTION: Use `AsyncIOScheduler` (not `BlockingScheduler`) and initialize INSIDE the FastAPI lifespan

### Gemini 15 RPM vs browser-use chattiness
- browser-use can make many LLM calls per page action
- SOLUTION: Pass `max_steps=15` to every scraping Agent call, `max_steps=25` for apply

### Tectonic cross-platform
- Tectonic binary differs per OS/arch
- SOLUTION: install script detects OS/arch and downloads correct binary to `bin/`

### chktex not cross-platform
- chktex not available on Windows
- SOLUTION: Dropped entirely — Tectonic compilation itself is the validator

### SvelteKit SPA routing with FastAPI
- FastAPI needs to serve `index.html` for all non-API paths
- SOLUTION: `app.mount('/', StaticFiles(directory='frontend/build', html=True))` + explicit catch-all before mount

### pydantic-settings REQUIRED keys
- Missing required env vars should fail loudly at startup (not silently return None)
- SOLUTION: Mark GOOGLE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY as required (no default) in Settings class
