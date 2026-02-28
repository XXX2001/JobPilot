# JobPilot — Project Overview

## What is JobPilot?

JobPilot is a **fully local, AI-powered job application assistant**. It automates the most repetitive parts of job searching — scraping listings, tailoring your CV, and filling application forms — while keeping you in control of every submit.

Everything runs on your own machine. No cloud account, no subscription, no data leaving your computer except the API calls to Gemini and Adzuna.

---

## Key Goals

| Goal | How it's achieved |
|---|---|
| **Free to run** | Gemini 2.0 Flash free tier (15 RPM) + Adzuna free API (250 req/day) |
| **Local and private** | SQLite DB, Tectonic LaTeX engine, Playwright browser — all local |
| **Surgical CV edits** | Gemini edits only the relevant bullet points — no full rewrites |
| **You stay in control** | ApplicationEngine always pauses and asks you to confirm before any form submit |
| **Scale without spam** | Configurable daily apply limit (default: 10) |

---

## How It Works — End-to-End Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     Morning Batch (08:00)                    │
│                                                             │
│  Adzuna REST API ──┐                                        │
│                    ├──▶ ScrapingOrchestrator                 │
│  browser-use       │        │                               │
│  (LinkedIn, etc) ──┘        │ deduplicate                   │
│                             ▼                               │
│                       JobDeduplicator                        │
│                             │ store new jobs                 │
│                             ▼                               │
│                         jobs table                           │
│                             │                               │
│                             ▼                               │
│                        JobMatcher                            │
│                    (keyword scoring)                         │
│                             │ score ≥ min_score              │
│                             ▼                               │
│                      job_matches table                       │
│                     (today's queue)                          │
└─────────────────────────────────────────────────────────────┘

                User opens http://localhost:8000
                         │
                         ▼
                  Morning Queue page
                  (ranked by score)
                         │
                  User clicks "Apply"
                         │
                         ▼
                    CVPipeline
              ┌──────────┴──────────┐
              │                     │
         GeminiClient          LaTeX engine
         (cv_editor.py)        (Tectonic)
              │                     │
         JSON diff            compile PDF
              └──────────┬──────────┘
                         │
                  tailored_documents table
                         │
                         ▼
                 ApplicationEngine
              ┌──────────┴──────────┐
              │         │           │
           auto      assisted    manual
           apply      apply       apply
              │
         PAUSE — WebSocket sends "confirm_apply" event
              │
         User clicks Confirm in browser
              │
         Form submitted
              │
         application recorded in DB
```

---

## Design Decisions

### Why LaTeX only?
Surgical CV editing requires knowing the exact structure of the document. LaTeX provides semantic markers (`%==SECTION_NAME==`) that Gemini can target precisely. PDF-only CVs cannot be edited this way.

### Why Tectonic instead of chktex?
The plan originally listed `chktex` for validation. In practice, Tectonic compilation provides stronger validation (a LaTeX file that compiles is by definition valid) and is cross-platform. chktex is Linux-centric and was dropped.

### Why not shadcn-svelte?
At implementation time, shadcn-svelte had Svelte 5 runes mode incompatibilities. Plain TailwindCSS + lucide-svelte icons achieve the same Notion/Linear aesthetic without the friction.

### Why SQLite?
JobPilot is a single-user local tool. SQLite over async SQLAlchemy (`aiosqlite`) is fast enough, requires zero setup, and the DB file lives at `data/jobpilot.db`.

### Why always pause before submit?
ApplicationEngine is designed so `auto_apply` triggers a WebSocket event and **waits** for a `confirm_submit` message from the frontend before calling any form submit action. This is a hard safety constraint — the system never submits anything autonomously.

---

## Component Summary

| Layer | Technology | Purpose |
|---|---|---|
| API server | FastAPI (async) | REST + WebSocket |
| Database | SQLite + SQLAlchemy async | Persistent state |
| Migrations | Alembic | Schema versioning |
| Scraping | Adzuna REST + browser-use | Job discovery |
| Matching | Custom keyword scorer | Relevance ranking |
| CV editing | Gemini 2.0 Flash + LaTeX parser | Surgical tailoring |
| PDF compilation | Tectonic | Cross-platform LaTeX → PDF |
| Application | browser-use + Playwright | Form filling |
| Scheduler | APScheduler | Morning batch at 08:00 |
| Frontend | SvelteKit + TailwindCSS | Dashboard UI |
| Config | pydantic-settings + .env | Environment-based |
