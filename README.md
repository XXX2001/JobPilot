# JobPilot

JobPilot is a local-first job application assistant with a FastAPI backend and a Svelte frontend. It collects jobs, scores them against your preferences, tailors LaTeX CV content, and helps you apply with manual, assisted, or automated flows.

## What this repository includes

- FastAPI backend for scraping, matching, CV generation, and application workflows
- Svelte frontend for queue review, settings, analytics, and document inspection
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

### 2. Install dependencies

#### Linux / macOS

```bash
bash scripts/install.sh
```

#### Windows (PowerShell)

```powershell
.\scripts\install.ps1
```

The installer is safe to re-run. It checks Python and Node.js, installs Python dependencies with `uv`, installs Playwright Chromium, downloads Tectonic into `bin/`, builds the frontend, creates runtime directories, and initializes `.env` from `.env.example` when needed.

### 3. Configure environment variables

Copy `.env.example` to `.env` if the installer did not already create it, then fill in your API keys.

```dotenv
GOOGLE_API_KEY=your_gemini_api_key
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key
```

Required services:

- `GOOGLE_API_KEY`: Gemini access for tailoring, extraction, and browser-agent prompts
- `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`: Adzuna search integration

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
backend/     FastAPI application code
frontend/    Svelte frontend
scripts/     Setup and utility scripts
tests/       Automated tests
data/        Runtime data (gitignored)
bin/         Downloaded binaries (gitignored)
start.py     Local launcher
```

## License

MIT — see [LICENSE](LICENSE)
