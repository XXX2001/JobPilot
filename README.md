# JobPilot 🚀

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via Gemini, and helps you apply at scale.

- **Free**: uses Gemini 2.0 Flash free tier + Adzuna free API
- **Local**: everything runs on your machine; no cloud account needed
- **Smart**: Gemini edits only the relevant bullet points in your CV — no rewrites
- **Polished**: Notion/Linear-aesthetic dashboard to track every application

---

## Prerequisites

| Tool | Version | Where to get |
|------|---------|-------------|
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) |
| Git | any | [git-scm.com](https://git-scm.com/) |

---

## Quick Start

### Linux / macOS

```bash
git clone https://github.com/yourusername/jobpilot.git
cd jobpilot
bash scripts/install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/yourusername/jobpilot.git
cd jobpilot
.\scripts\install.ps1
```

The installer is **idempotent** — safe to run multiple times. It will:

1. Check Python ≥ 3.12 and Node.js ≥ 18
2. Install [uv](https://docs.astral.sh/uv/) (Python package manager)
3. Install all Python dependencies via `uv sync`
4. Install Playwright Chromium for browser automation
5. Download the [Tectonic](https://tectonic-typesetting.github.io) LaTeX engine to `bin/`
6. Build the SvelteKit frontend
7. Create `data/` directories
8. Copy `.env.example` → `.env` (first run only)

---

## Configuration

Edit `.env` and fill in your API keys:

```dotenv
# Get yours free at https://aistudio.google.com/
GOOGLE_API_KEY=your_gemini_api_key

# Get yours free at https://developer.adzuna.com/
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key
```

You can also configure these via the **Settings** page in the web UI after starting the app.

---

## Launch

```bash
uv run python start.py
```

Then open **http://localhost:8000** in your browser.

---

## First-Time Setup

1. Open **http://localhost:8000** — the Setup Wizard will guide you
2. Upload your base CV as a `.tex` file (LaTeX required for surgical editing)
3. Set your job search keywords, target location, and daily apply limit
4. Click **Run Morning Batch** or wait for the 08:00 scheduled run

---

## How It Works

```
Adzuna API ──┐
             ├──▶ ScrapingOrchestrator ──▶ JobMatcher ──▶ Queue
LinkedIn     │        (browser-use)       (keyword fit)
Indeed ──────┘

Queue ──▶ CVPipeline ──▶ Gemini 2.0 Flash ──▶ LaTeX diff ──▶ Tectonic PDF
                                                    │
                                              ApplicationEngine
                                                    │
                                         (pause → confirm → apply)
```

- **Adzuna**: structured job data via REST API (no browser needed)
- **browser-use**: headless Chromium + Gemini for any other site
- **CVPipeline**: copies your base `.tex`, sends relevant sections to Gemini, applies JSON diff via marker injection, compiles with Tectonic
- **ApplicationEngine**: browser-use agent fills forms; always pauses for your `confirm_submit` before submitting

---

## Project Structure

```
jobpilot/
├── backend/
│   ├── api/          FastAPI route handlers
│   ├── applier/      Browser-use application engine
│   ├── latex/        LaTeX parser, injector, compiler
│   ├── llm/          Gemini client + CV editor
│   ├── matching/     Job scoring & filters
│   ├── models/       SQLAlchemy models + Pydantic schemas
│   ├── scraping/     Adzuna client + adaptive browser scraper
│   └── scheduler/    Morning batch (APScheduler)
├── frontend/         SvelteKit web UI
├── scripts/
│   ├── install.sh    Linux/macOS one-command installer
│   ├── install.ps1   Windows one-command installer
│   └── download_tectonic.py  Cross-platform Tectonic downloader
├── tests/            pytest test suite (110+ tests)
├── data/             Runtime data (CVs, PDFs, DB) — gitignored
├── bin/              Tectonic binary — gitignored
└── start.py          Application entry point
```

---

## Development

```bash
# Run tests
uv run pytest tests/ -q

# Type check
uv run pyright backend/

# Lint
uv run ruff check backend/ tests/

# Frontend dev server (hot reload)
cd frontend && npm run dev
```

---

## Limits & Constraints

- **Gemini free tier**: 15 requests/minute — the rate limiter handles this automatically
- **Adzuna free tier**: 250 requests/day — morning batch respects this
- **No parallel browsers**: browser-use agents run sequentially to stay within RPM limits
- **No auto-submit**: ApplicationEngine always pauses and waits for your explicit `confirm_submit` before any form submission
- **LaTeX only**: CV must be a `.tex` file for surgical editing; PDF-only CVs are not supported

---

## License

MIT — see [LICENSE](LICENSE)
