# JobPilot — Operations Guide

## Prerequisites

| Tool | Version | Where to get |
|---|---|---|
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) |
| Git | any | [git-scm.com](https://git-scm.com/) |

---

## Installation

### Linux / macOS (one-command)
```bash
git clone https://github.com/yourusername/jobpilot.git
cd jobpilot
bash scripts/install.sh
```

### Windows (PowerShell one-command)
```powershell
git clone https://github.com/yourusername/jobpilot.git
cd jobpilot
.\scripts\install.ps1
```

### What the installer does
1. Checks Python ≥ 3.12 and Node.js ≥ 18
2. Installs [uv](https://docs.astral.sh/uv/) (fast Python package manager)
3. `uv sync` — installs all Python dependencies into `.venv/`
4. `playwright install chromium` — downloads Chromium for browser automation
5. `download_tectonic.py` — downloads the Tectonic LaTeX engine to `bin/`
6. `npm install && npm run build` — builds the SvelteKit frontend
7. Creates `data/cvs`, `data/letters`, `data/templates`, `data/browser_sessions`, `data/logs`
8. Copies `.env.example` → `.env` (first run only; won't overwrite existing)

The installer is **idempotent** — safe to run multiple times.

---

## Configuration

Edit `.env` at the project root. Required fields:

```dotenv
# Gemini API key — free at https://aistudio.google.com/
GOOGLE_API_KEY=your_gemini_api_key_here

# Adzuna job search API — free at https://developer.adzuna.com/
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key
```

Optional fields:
```dotenv
# Optional: SerpAPI for additional search (not required)
SERPAPI_KEY=

# Server bind address (default: 127.0.0.1 — localhost only)
JOBPILOT_HOST=127.0.0.1

# Server port (default: 8000)
JOBPILOT_PORT=8000

# Log level: debug, info, warning, error (default: info)
JOBPILOT_LOG_LEVEL=info

# Data directory (default: ./data)
JOBPILOT_DATA_DIR=./data
```

> **Security note**: Never share your `.env` file or commit it to git. It's in `.gitignore` by default.

---

## Starting the Application

```bash
uv run python start.py
```

Then open **http://localhost:8000** in your browser.

> **Note**: `start.py` checks for Tectonic before starting. If Tectonic is not installed, it exits with an error. To bypass this check during development: `uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000`

---

## First-Time Setup

When you open the app for the first time, the **Setup Wizard** will guide you through:

### Step 1: API Keys
Verify your Gemini and Adzuna keys are set. The wizard reads from your `.env` file — if they're set correctly, you'll see green checkmarks.

### Step 2: Upload Your Base CV
- Your CV must be a **LaTeX `.tex` file**
- It must use JobPilot's section markers for surgical editing:

```latex
\section{Experience}
%==EXPERIENCE==
\begin{itemize}
  \item Developed Python microservices using FastAPI and SQLAlchemy
  \item Reduced API latency by 40\% through async refactoring
\end{itemize}
%==END_EXPERIENCE==

\section{Skills}
%==SKILLS==
Python, FastAPI, PostgreSQL, Docker, AWS
%==END_SKILLS==
```

Supported section names: `EXPERIENCE`, `SKILLS`, `EDUCATION`, `PROJECTS`, `SUMMARY`

### Step 3: Search Settings
Configure your job search:
- **Keywords**: terms that must/should appear in job listings
- **Location**: target cities or "Remote"
- **Salary minimum**: filter out low-paying roles
- **Daily apply limit**: how many applications to submit per day (default: 10)
- **Batch time**: when morning batch runs (default: 08:00)
- **Minimum match score**: threshold to add a job to the queue (default: 30/100)

---

## Daily Workflow

### Morning (automatic)
At 08:00, JobPilot automatically:
1. Scrapes Adzuna (and configured browser-use sites)
2. Deduplicates against previously seen jobs
3. Scores all new jobs against your settings
4. Adds qualifying jobs (score ≥ min_match_score) to today's queue
5. Pre-tailors CVs for top matches

### Using the Dashboard
Open **http://localhost:8000** and check the **Morning Queue** (home page).

For each queued job:
- Review the match score and job details
- Click **Job** to open the full listing
- Click **Apply** to start the application process
- Click **Skip** to remove from today's queue

### Application Flow
When you click **Apply**:
1. JobPilot tailors your CV for that specific job (Gemini + Tectonic)
2. The browser opens the application URL
3. browser-use fills in the form fields
4. JobPilot **pauses and shows a confirmation dialog** — review the filled form
5. Click **Confirm** to submit, or **Cancel** to abort
6. The application is recorded in the tracker

### Tracking Applications
The **Tracker** page shows a Kanban board with columns:
- **Applied** — submitted applications
- **Interview** — interview scheduled
- **Offer** — received an offer
- **Rejected** — no longer active

Drag cards between columns, or update status via the job detail page.

---

## Managing Your CV

The **CV** page lets you:
- View all tailored documents generated
- Download any tailored PDF
- Delete old documents
- Re-tailor a CV for a different job

---

## Viewing Analytics

The **Analytics** page shows:
- Total applications, applications this week
- Response rate (interviews + offers + rejections / total)
- Average match score
- Applications per day chart (30-day default, adjustable)

---

## Manual Batch Trigger

If you miss the morning batch or want to re-run:
```bash
# Via API
curl -X POST http://localhost:8000/api/queue/refresh
```
Or click **Re-run Batch** in the Settings page.

---

## Upgrading

```bash
git pull origin main
uv sync
cd frontend && npm install && npm run build && cd ..
uv run alembic upgrade head
uv run python start.py
```

---

## Backup

The entire application state is in `data/`:
```
data/
├── jobpilot.db          SQLite database (all jobs, applications, settings)
├── cvs/                 Your tailored .tex and .pdf CVs
│   ├── base_cv.tex      Your uploaded base CV
│   └── job_1_cv.tex     Tailored for job #1
├── letters/             Cover letters
├── templates/           CV templates
├── browser_sessions/    Saved browser cookies (for assisted apply)
└── logs/                Application logs
```

To back up: copy the entire `data/` directory.

To restore: copy it back and run `uv run alembic upgrade head`.

---

## Stopping the Application

Press `Ctrl+C` in the terminal running `start.py`. The scheduler will gracefully shut down.

---

## Uninstallation

```bash
# Remove virtualenv and dependencies
rm -rf .venv uv.lock

# Remove downloaded binaries
rm -rf bin/

# Remove all runtime data (CAUTION: deletes all your CVs and application history)
rm -rf data/

# Remove frontend build
rm -rf frontend/build frontend/node_modules
```
