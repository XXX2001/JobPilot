# JobPilot

**A single-user, self-hosted AI assistant for the full job-application cycle.**

JobPilot is a local web app that discovers job listings from the sources you enable, scores how well each one fits your profile, tailors your LaTeX CV and cover letter for every match with Google Gemini, and walks you through applying — manually, semi-automatically, or fully automatically. It runs as one process on your own machine: a FastAPI backend serves both the REST API and the compiled SvelteKit frontend, backed by a single SQLite database and the Tectonic LaTeX compiler. No authentication layer, no cloud services beyond the Gemini and job-search APIs you configure yourself.

## Features

- **First-run onboarding wizard** (`/onboarding`) — guides you through API keys, CV upload, keywords, and your first batch.
- **Job discovery** from API sources (Adzuna) and browser-scraped boards (LinkedIn, Indeed, Glassdoor, Welcome to the Jungle, Google Jobs) plus custom "lab" URLs.
- **Relevance scoring** with a weighted keyword + recency model, and a **"Why this score"** breakdown on each job's detail page.
- **AI CV tailoring** — surgical, safety-gated LaTeX replacements compiled to PDF via Tectonic.
- **AI cover letters** — marker-delimited paragraph editing, viewable and regenerable.
- **On-demand batch runs** with a **dry-run preview** (scrape + match only, nothing written to the database).
- **Queue review** with three apply modes — **auto**, **assisted**, **manual** — and pre-submit editing of mis-filled form fields.
- **CV editor** (`/cv`) and **Letters view** (`/letters`) with one-click regeneration, plus a **template compile-test** button in Settings → Profile.
- **Application tracker** (`/tracker`) for following each application's status.
- **Gmail integration** (optional) — connect a mailbox to surface application-related correspondence.

Everything stays on your machine. The only data leaving your computer goes to the AI and job-search APIs you configure.

---

## Quickstart

You need API keys before either path will work — see [Getting the API keys](#getting-the-api-keys) below. Once running, open **http://localhost:8000** and complete the in-app onboarding wizard.

### Path A — Docker Compose (recommended for self-hosting)

```bash
cp .env.example .env
# Edit .env and fill in GOOGLE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY
docker compose up -d --build
```

Then open **http://localhost:8000**. Your data persists in `./data` across container restarts.

### Path B — Local dev (uv)

**Prerequisites:** Python 3.12, Node.js 20.

```bash
uv sync                                         # install Python dependencies
uv run python scripts/download_tectonic.py      # download the Tectonic LaTeX compiler
cd frontend && npm ci && npm run build && cd ..  # build the web interface
cp .env.example .env                            # then fill in your API keys
uv run python start.py                          # launch (opens http://localhost:8000)
```

`start.py` checks that the data directory, frontend build, and Tectonic binary are present, frees the port if needed, then starts the backend on `JOBPILOT_HOST:JOBPILOT_PORT` (default `127.0.0.1:8000`) and opens your browser.

### Getting the API keys

JobPilot needs three required keys, all available on free tiers:

| Key | Where to get it | What it powers |
| --- | --- | --- |
| `GOOGLE_API_KEY` | [Google AI Studio](https://aistudio.google.com/) → **Get API key** | All Gemini features (scoring extraction, CV/letter tailoring, scraping, form-fill) |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | [Adzuna developer portal](https://developer.adzuna.com/) → create a free account | The Adzuna job-search API source |

Optional integrations (SerpAPI fallback, Gmail) have their own keys — see `.env.example` for the full list and inline notes.

---

## Documentation

- **[User guide](docs/user-guide.md)** — an end-to-end walkthrough of every feature, from onboarding to Gmail.
- **[Architecture](docs/architecture.md)** — system overview, component diagram, request lifecycles, database schema, and the [credentials & encryption](docs/architecture.md#credentials--encryption) reference.
- **[Custom CV templates](docs/custom-templates.md)** — how to bring your own LaTeX CV template.
- **[Contributing](CONTRIBUTING.md)** — dev setup, quality gates, and the spec/plan workflow.

---

## Alternative install (guided installer scripts)

The repository also ships convenience installer scripts that bundle the local-dev steps above (install `uv` + Python, build the frontend, download Tectonic, create `.env`, and add a desktop shortcut). They are optional; the [Quickstart](#quickstart) paths above are the canonical setup.

<details>
<summary><strong>Linux / macOS</strong></summary>

```bash
bash scripts/install.sh
```

On macOS, if a security warning about Tectonic appears, run once: `xattr -d com.apple.quarantine ./bin/tectonic`.

</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
.\scripts\install.ps1
```

If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process Bypass` first.

</details>

---

## First-time setup (inside the app)

1. The onboarding wizard (`/onboarding`) will guide you through the basics
2. Upload your CV template (a `.tex` file)
3. Set your job search preferences (location, keywords, etc.)
4. Click **Refresh queue** to run your first job-discovery batch

For a full walkthrough of every feature, see the [user guide](docs/user-guide.md).

---

## Troubleshooting

### "Node not found"

Make sure Node.js 18+ is installed and that its installer added it to your system PATH. Close and reopen your terminal after installing, then run the installer again. (Python is installed automatically by the installer -- you do not need to install it yourself.)

### The installer finished but something seems wrong

The installer is safe to run again -- it will skip anything already set up and only fix what is missing.

### macOS blocks Tectonic (security warning)

Run this command once from the project folder:

```bash
xattr -d com.apple.quarantine ./bin/tectonic
```

### Windows blocks the PowerShell script

Run this before the installer:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### PDF generation is not working

The PDF engine (Tectonic) may not have downloaded correctly. Re-run the installer, or visit [tectonic-typesetting.github.io](https://tectonic-typesetting.github.io/) to install it manually.

### The web interface does not load

Make sure the frontend was built. Run these two commands from the project folder:

```bash
npm install --prefix frontend
npm run build --prefix frontend
```

Then restart JobPilot.

### The app starts but nothing works properly

- Check that your `.env` file has valid API keys (no extra spaces around the `=` sign)
- Check that your CV template file exists and is accessible
- Try stopping and restarting the app

---

## Advanced configuration

<details>
<summary><strong>All .env settings</strong></summary>

```dotenv
# Required API keys
GOOGLE_API_KEY=your_gemini_api_key
ADZUNA_APP_ID=your_adzuna_app_id
ADZUNA_APP_KEY=your_adzuna_app_key

# Credential encryption (the installer generates this for you)
CREDENTIAL_KEY=

# App settings (defaults work for most users)
JOBPILOT_HOST=127.0.0.1
JOBPILOT_PORT=8000
JOBPILOT_LOG_LEVEL=info
JOBPILOT_DATA_DIR=./data
JOBPILOT_SCRAPER_HEADLESS=true
# Comma-separated CORS allow-list. Set this if you serve the UI from a host
# other than the local dev defaults (e.g. behind a reverse proxy).
JOBPILOT_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000

# AI model settings
GOOGLE_MODEL=gemini-3-flash-preview
GOOGLE_MODEL_FALLBACKS=

# Feature flags
SCRAPLING_ENABLED=true
APPLY_TIER1_ENABLED=true
```

</details>

<details>
<summary><strong>Platform support</strong></summary>

| Platform | Status | Notes |
| --- | --- | --- |
| Linux | Fully supported | Primary development platform |
| macOS | Supported | Tectonic may need a one-time security approval |
| Windows | Supported | PowerShell execution policy may need adjustment |

</details>

<details>
<summary><strong>Development commands</strong></summary>

```bash
uv run pytest tests/ -q            # Run tests
uv run pyright backend/            # Type-check the backend
uv run ruff check backend/ tests/  # Lint the backend
npm run check --prefix frontend    # Check the frontend
npm run build --prefix frontend    # Rebuild the frontend
```

</details>

<details>
<summary><strong>Backup & restore</strong></summary>

JobPilot keeps everything in a single SQLite database at
`data/jobpilot.db`. To take a hot, online snapshot while the app is
running:

```bash
uv run python scripts/backup_db.py
# → /…/data/backups/jobpilot-20260524T143000Z.db
```

The script uses SQLite's `VACUUM INTO` so it is safe to run while the
server is live (no shutdown required). By default the snapshot lands in
`data/backups/`; override with `--out /elsewhere` if you prefer.

To restore: stop the app, replace `data/jobpilot.db` with the snapshot
(and delete any `jobpilot.db-wal` / `jobpilot.db-shm` siblings), then
restart.

Your encryption key (`CREDENTIAL_KEY` in `.env`) is **not** part of the
DB backup — see [`docs/architecture.md` → "Credentials & encryption"](docs/architecture.md#credentials--encryption)
for how to back it up separately.

</details>

<details>
<summary><strong>Project layout</strong></summary>

```text
backend/
  api/         Web server routes
  applier/     Application engine (auto, assisted, manual)
  latex/       CV generation (LaTeX to PDF)
  llm/         AI integration (Gemini)
  matching/    Job-CV scoring and skill analysis
  models/      Data models
  scraping/    Job site scrapers
  scheduler/   On-demand batch pipeline (BatchRunner)
frontend/      Web interface
scripts/       Installer and utility scripts
tests/         Automated tests
data/          Your data (ignored by Git)
bin/           Downloaded tools (ignored by Git)
start.py       App launcher
```

</details>

---

## License

MIT -- see [LICENSE](LICENSE)
