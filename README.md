# JobPilot

**Your personal job-hunting assistant that runs on your own computer.**

JobPilot finds job listings that match your profile, scores how well you fit each one, customises your CV for every application, and helps you apply -- all from a simple web interface in your browser.

Everything stays on your machine. No data is sent to third-party servers except the AI and job-search APIs you configure yourself.

---

## What can JobPilot do?

- **Search for jobs** on LinkedIn, Indeed, Glassdoor, Welcome to the Jungle, and more
- **Score each listing** against your skills and experience so you focus on the best matches
- **Tailor your CV** automatically for every job, highlighting the most relevant skills
- **Generate polished PDF CVs** from your template
- **Help you apply** with manual, guided, or fully automated workflows
- **Schedule daily searches** so new opportunities are waiting for you each morning

---

## Getting started

### Before you begin

You only need two things to get started:

| What to install | Where to get it | Why it is needed |
| --- | --- | --- |
| **Git** | [git-scm.com/downloads](https://git-scm.com/downloads/) | Downloads the project |
| **Node.js 18 or newer** | [nodejs.org](https://nodejs.org/) (pick the LTS version) | Builds the web interface |

> **You do NOT need to install Python yourself.** The installer automatically sets up `uv` (a fast Python manager) which downloads and manages the correct Python version for you.

### Step 1 -- Download the project

Open a terminal (or PowerShell on Windows) and run:

```bash
git clone <your-repository-url>
cd Web-automation
```

### Step 2 -- Run the installer

The installer takes care of everything automatically. It is safe to run more than once.

<details>
<summary><strong>Linux</strong></summary>

```bash
bash scripts/install.sh
```

</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
bash scripts/install.sh
```

If macOS shows a security warning about Tectonic after the install, run this once to allow it:

```bash
xattr -d com.apple.quarantine ./bin/tectonic
```

</details>

<details>
<summary><strong>Windows</strong></summary>

Open **PowerShell** in the project folder and run:

```powershell
.\scripts\install.ps1
```

If PowerShell says "scripts are disabled on this system", run this first:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

</details>

The installer will:
1. Install `uv` and Python 3.12 automatically (no manual Python setup needed)
2. Check that Node.js is available for the web interface
3. Download all the libraries JobPilot needs
4. Set up a web browser for job-site scraping
5. Download the PDF engine for CV generation
6. Build the web interface
7. Create a `.env` settings file for your API keys
8. Place a **shortcut on your Desktop** so you can start JobPilot with one click

### Step 3 -- Get your free API keys

JobPilot uses two free services. You only need to do this once.

#### Gemini (Google AI) -- powers the smart features

1. Go to [aistudio.google.com](https://aistudio.google.com/)
2. Sign in with your Google account
3. Click **"Get API key"** and copy the key

#### Adzuna -- powers the job search

1. Go to [developer.adzuna.com](https://developer.adzuna.com/)
2. Create a free account
3. Copy your **App ID** and **App Key** from the dashboard

### Step 4 -- Enter your API keys

Open the file called `.env` in the project folder (any text editor works) and paste your keys:

```
GOOGLE_API_KEY=paste_your_gemini_key_here
ADZUNA_APP_ID=paste_your_adzuna_app_id_here
ADZUNA_APP_KEY=paste_your_adzuna_key_here
```

Save the file.

### Step 5 -- Start JobPilot

**Option A -- Use the Desktop shortcut** (created by the installer)

- Linux: double-click `JobPilot` on your desktop
- macOS: double-click `JobPilot.command` on your desktop
- Windows: double-click `Start JobPilot.bat` on your desktop

**Option B -- Start from the terminal**

```bash
# Linux / macOS
./start-jobpilot.sh

# Windows (PowerShell)
.\start-jobpilot.ps1
```

Once started, open your browser and go to: **http://localhost:8000**

---

## First-time setup (inside the app)

1. The setup wizard will guide you through the basics
2. Upload your CV template (a `.tex` file)
3. Set your job search preferences (location, keywords, etc.)
4. Run your first search or wait for the automatic daily batch

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

# AI model settings
GOOGLE_MODEL=gemini-2.0-flash
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
  scheduler/   Scheduled daily searches
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
