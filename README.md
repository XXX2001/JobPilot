# JobPilot

JobPilot is a local-first job application assistant with a FastAPI backend and a Svelte frontend. It collects jobs, scores them against your preferences, tailors LaTeX CV content, and helps you apply with manual, assisted, or automated flows.

## What this repository includes

- FastAPI backend for scraping, matching, CV generation, and application workflows
- Svelte frontend for queue review, settings, analytics, and document inspection
- Two-tier scraping: fast Scrapling HTTP fetch for known boards, full browser-agent fallback for others
- FitEngine: semantic gap-severity scoring combining skill extraction, embedding similarity, and ATS analysis
- Local SQLite-backed runtime data under `data/`
- Installer scripts for Linux/macOS (`scripts/install.sh`) and Windows (`scripts/install.ps1`)

## Prerequisites

| Tool | Required version | Notes |
| --- | --- | --- |
| Python | 3.12+ | Required for the backend and scripts |
| Node.js | 18+ | Required for the frontend build |
| Git | Current version | Needed to clone the repository |
| uv | Current version | Preferred Python package manager |

## Full onboarding

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd Web-automation
```

### 2. Install dependencies (by OS)

<details>
<summary><strong>Linux</strong></summary>

```bash
# from repository root
bash scripts/install.sh
```

What this does:

- checks Python and Node versions
- installs Python deps with `uv sync`
- installs Playwright Chromium with Linux deps
- downloads Tectonic into `bin/tectonic`
- builds frontend static assets
- creates runtime folders under `data/`
- initializes `.env` from `.env.example` if missing

</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
# from repository root
bash scripts/install.sh
```

After install, validate Tectonic:

```bash
./bin/tectonic --version
```

If macOS blocks execution (Gatekeeper/quarantine), allow or unquarantine the binary, then re-check:

```bash
xattr -d com.apple.quarantine ./bin/tectonic
./bin/tectonic --version
```

</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
# from repository root
.\scripts\install.ps1
```

After install, you can start JobPilot by double-clicking:

- Desktop: `Start JobPilot.bat`
- Or repo root: `Start JobPilot.bat` / `start-jobpilot.ps1`

If your execution policy blocks scripts:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

Validate Tectonic after install:

```powershell
.\bin\tectonic.exe --version
```

</details>

The installer is safe to re-run. It checks Python and Node.js, installs Python dependencies with `uv`, installs Playwright Chromium, downloads Tectonic into `bin/`, builds the frontend, creates runtime directories, and initializes `.env` from `.env.example` when needed.

It also creates launchers for non-technical users:

- Linux: `start-jobpilot.sh` and desktop `JobPilot.desktop` (when Desktop folder exists)
- macOS: `start-jobpilot.sh` and desktop `JobPilot.command`
- Windows: `Start JobPilot.bat`, `start-jobpilot.ps1`, and desktop `Start JobPilot.bat`

When users launch JobPilot from these shortcuts, they see a simple startup message in the terminal window and the app opens automatically in the browser at `http://localhost:8000`.

### 3. Configure environment variables

Copy `.env.example` to `.env` if the installer did not already create it, then fill in your API keys.

```dotenv
# Required API keys
GOOGLE_API_KEY=your_gemini_api_key
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key
# Credential encryption (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
CREDENTIAL_KEY=

# App settings
JOBPILOT_HOST=127.0.0.1
JOBPILOT_PORT=8000
JOBPILOT_LOG_LEVEL=info
JOBPILOT_DATA_DIR=./data
JOBPILOT_SCRAPER_HEADLESS=true

# Gemini model settings
GOOGLE_MODEL=gemini-2.0-flash
GOOGLE_MODEL_FALLBACKS=               # comma-separated fallback model IDs

# Feature flags
SCRAPLING_ENABLED=true                # fast HTTP-first scraping tier
APPLY_TIER1_ENABLED=true              # two-tier application engine
```

Required services:

- `GOOGLE_API_KEY`: Gemini access for tailoring, extraction, and browser-agent prompts
- `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`: Adzuna search integration
- `CREDENTIAL_KEY`: Fernet key for encrypting stored credentials (generate once per install)

### 4. Build the frontend manually if needed

```bash
npm install --prefix frontend
npm run build --prefix frontend
```

### 5. Start the app

```bash
uv run python start.py
```

Then open `http://localhost:8000`.

## First-time workflow

1. Open the app in your browser.
2. Complete the setup wizard.
3. Upload a LaTeX CV template (`.tex`).
4. Configure search preferences and API keys.
5. Run a batch or wait for the scheduled job search.

## Architecture notes

### Two-tier scraping

The scraper tries a fast Scrapling HTTP fetch first (Tier 1) for known boards (LinkedIn, Indeed, Google Jobs, Welcome to the Jungle, Glassdoor). A single Gemini call extracts jobs from the cleaned HTML — roughly 20× fewer LLM calls than a full browser-agent loop. Unknown or complex sites fall back to the full AdaptiveScraper (Tier 2). Toggle with `SCRAPLING_ENABLED`.

### FitEngine (ATS gap severity)

`backend/matching/fit_engine.py` scores a candidate CV against a job using:

1. Skill extraction from both documents (`JobSkillExtractor`, `CVParser`)
2. Cosine similarity between sentence embeddings (`Embedder`)
3. ATS gap severity: missing skills are rated critical / important / nice-to-have
4. A weighted composite score that drives CV tailoring priority

The morning batch generates a base CV once per day and then calls `modify_from_assessment()` to produce job-specific variants targeted at gap coverage. Sensitivity is configurable per user in Settings.

## Current limitations

- JobPilot is designed as a **local single-user application**.
- Default startup assumes the backend binds to `127.0.0.1:8000`.
- The frontend WebSocket logic falls back to a localhost backend if no explicit API base URL is configured.
- Some workflows assume a desktop environment, including opening browser windows and copying generated files to a local downloads folder.
- Runtime paths such as `./data`, `frontend/build`, and `bin/tectonic` are relative to the project/runtime context.

## Platform support

| Platform | Status | Notes |
| --- | --- | --- |
| Linux | Primary path | Best-covered by the current installer flow |
| macOS | Best effort | Tectonic downloads may require manual approval depending on system security settings |
| Windows | Best effort | PowerShell execution policy, Playwright setup, and local downloads-folder behavior may need manual attention |

## Troubleshooting

### Playwright install fails

- Re-run the installer.
- Try `uv run playwright install chromium` manually.
- On Linux, browser system dependencies may still need manual installation.

### Tectonic is missing or cannot run

- Re-run the installer or run `uv run python scripts/download_tectonic.py`.
- Confirm the binary exists in `bin/` or that `tectonic` is available on `PATH`.
- On macOS, a downloaded binary may require manual approval before first launch.
- On Windows, security software may quarantine `tectonic.exe`; restore/allow it, then rerun the installer.

### Frontend build is missing

```bash
npm install --prefix frontend
npm run build --prefix frontend
```

### The app starts but setup is incomplete

- Verify `.env` contains valid API keys.
- Verify your LaTeX CV exists and is accessible to the app.
- Verify the frontend build exists at `frontend/build`.

## Development commands

```bash
uv run pytest tests/ -q
uv run pyright backend/
uv run ruff check backend/ tests/
npm run check --prefix frontend
npm run build --prefix frontend
```

## Repository layout

```text
backend/
  api/         FastAPI route handlers
  applier/     Two-tier application engine (auto, assisted, manual)
  latex/        CV generation pipeline (LaTeX → PDF via Tectonic)
  llm/          Gemini client, CV modifier, job analyzer, prompts
  matching/     FitEngine — skill extraction, embedding, gap scoring, ATS analysis
  models/       SQLAlchemy models and Pydantic schemas
  scraping/     Orchestrator, AdaptiveScraper (Tier 2), Scrapling fetcher (Tier 1)
  scheduler/    Morning batch job
frontend/    Svelte frontend
scripts/     Setup and utility scripts
tests/       Automated tests
data/        Runtime data (gitignored)
bin/         Downloaded binaries (gitignored)
start.py     Local launcher
```

## License

MIT — see [LICENSE](LICENSE)
