#Requires -Version 5.1
<#
.SYNOPSIS
    JobPilot installer for Windows.
.DESCRIPTION
    Idempotent installer: safe to run multiple times.
    Does NOT require Administrator privileges.
.EXAMPLE
    .\scripts\install.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Info    { param($Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Ok      { param($Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn    { param($Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Err     { param($Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }
function Write-Step    { param($Msg) Write-Host "`n──── $Msg ────" -ForegroundColor White }

# ── Repo root ─────────────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

Write-Host ""
Write-Host "╔═════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║         JobPilot Installer          ║" -ForegroundColor Cyan
Write-Host "╚═════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check Python ≥ 3.12 ───────────────────────────────────────────────
Write-Step "1/9  Checking Python version"
try {
    $PyVersion = & python --version 2>&1
    if ($PyVersion -match "Python (\d+)\.(\d+)") {
        $Major = [int]$Matches[1]
        $Minor = [int]$Matches[2]
        if ($Major -ge 3 -and $Minor -ge 12) {
            Write-Ok "Python $Major.$Minor found"
        } else {
            Write-Err "Python 3.12+ required, found $Major.$Minor"
            Write-Err "Install Python 3.12+ from https://python.org and re-run."
            exit 1
        }
    } else {
        throw "Could not parse version"
    }
} catch {
    Write-Err "python not found. Install Python 3.12+ from https://python.org"
    exit 1
}

# ── Step 2: Check Node.js ≥ 18 ────────────────────────────────────────────────
Write-Step "2/9  Checking Node.js version"
$NodeAvailable = $false
try {
    $NodeVersion = & node --version 2>&1
    if ($NodeVersion -match "v(\d+)") {
        $NodeMajor = [int]$Matches[1]
        if ($NodeMajor -ge 18) {
            Write-Ok "Node.js $NodeVersion found"
            $NodeAvailable = $true
        } else {
            Write-Warn "Node.js $NodeVersion found, but >=18 recommended."
            Write-Warn "Frontend build may fail."
            $NodeAvailable = $true
        }
    }
} catch {
    Write-Warn "node not found — frontend build will be skipped."
    Write-Warn "Install Node.js 18+ from https://nodejs.org if you want the web UI."
}

# ── Step 3: Install uv ────────────────────────────────────────────────────────
Write-Step "3/9  Checking uv"
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) {
    Write-Ok "uv already installed: $(& uv --version)"
} else {
    Write-Info "Installing uv (Python package manager)..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + `
                    [System.Environment]::GetEnvironmentVariable("Path", "Machine")
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($uvCmd) {
            Write-Ok "uv installed: $(& uv --version)"
        } else {
            Write-Err "uv installation failed. Install manually: https://docs.astral.sh/uv/"
            exit 1
        }
    } catch {
        Write-Err "Failed to install uv: $_"
        exit 1
    }
}

# ── Step 4: Python dependencies ───────────────────────────────────────────────
Write-Step "4/9  Installing Python dependencies"
& uv sync
Write-Ok "Python dependencies installed"

# ── Step 5: Playwright browsers ───────────────────────────────────────────────
Write-Step "5/9  Installing Playwright Chromium"
$PlaywrightOk = $false
try {
    $result = & uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); p.stop()" 2>&1
    $PlaywrightOk = $LASTEXITCODE -eq 0
} catch { $PlaywrightOk = $false }

if ($PlaywrightOk) {
    Write-Ok "Playwright Chromium already installed"
} else {
    & uv run playwright install chromium
    Write-Ok "Playwright Chromium installed"
}

# ── Step 6: Download Tectonic ─────────────────────────────────────────────────
Write-Step "6/9  Installing Tectonic (LaTeX engine)"
$TectonicBin = Join-Path $RepoRoot "bin\tectonic.exe"
$TectonicOk  = $false
if (Test-Path $TectonicBin) {
    try {
        $ver = & $TectonicBin --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Tectonic already installed: $($ver | Select-Object -First 1)"
            $TectonicOk = $true
        }
    } catch {}
}

if (-not $TectonicOk) {
    Write-Info "Downloading Tectonic binary..."
    try {
        & uv run python scripts\download_tectonic.py
        if (Test-Path $TectonicBin) {
            $ver = & $TectonicBin --version 2>&1
            Write-Ok "Tectonic installed: $($ver | Select-Object -First 1)"
        } else {
            Write-Warn "Tectonic download failed. PDF generation will be disabled."
            Write-Warn "Manual install: https://tectonic-typesetting.github.io"
        }
    } catch {
        Write-Warn "Tectonic download error: $_"
        Write-Warn "PDF generation will be disabled until Tectonic is installed."
    }
}

# ── Step 7: Frontend build ────────────────────────────────────────────────────
Write-Step "7/9  Building frontend"
$FrontendBuilt = Test-Path (Join-Path $RepoRoot "frontend\build\index.html")
if (-not $FrontendBuilt) {
    if ($NodeAvailable) {
        Write-Info "Installing frontend npm dependencies..."
        & npm install --prefix frontend
        Write-Info "Building SvelteKit app..."
        & npm run build --prefix frontend
        Write-Ok "Frontend built"
    } else {
        Write-Warn "node/npm not found — skipping frontend build."
        Write-Warn "Install Node.js 18+ then run: cd frontend; npm install; npm run build"
    }
} else {
    Write-Ok "Frontend already built (frontend\build\index.html exists)"
}

# ── Step 8: Data directories ──────────────────────────────────────────────────
Write-Step "8/9  Creating data directories"
$Dirs = @("data\cvs","data\letters","data\templates","data\sessions","data\browser_sessions","data\browser_profiles","data\pdfs","data\logs")
foreach ($Dir in $Dirs) {
    $Full = Join-Path $RepoRoot $Dir
    if (-not (Test-Path $Full)) {
        New-Item -ItemType Directory -Path $Full -Force | Out-Null
    }
}
Write-Ok "Data directories ready"

# ── Step 9: Environment file ──────────────────────────────────────────────────
Write-Step "9/9  Setting up .env"
$EnvFile    = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Ok ".env created from .env.example"
    Write-Host ""
    Write-Host "  ⚠  ACTION REQUIRED: Edit .env and fill in your API keys:" -ForegroundColor Yellow
    Write-Host "     GOOGLE_API_KEY  — Gemini API key (free at aistudio.google.com)" -ForegroundColor Yellow
    Write-Host "     ADZUNA_APP_ID   — Adzuna app ID  (free at developer.adzuna.com)" -ForegroundColor Yellow
    Write-Host "     ADZUNA_APP_KEY  — Adzuna API key (free at developer.adzuna.com)" -ForegroundColor Yellow
} else {
    Write-Ok ".env already exists"
}

Write-Step "10/10  Creating launcher shortcuts"

$LauncherPs1 = Join-Path $RepoRoot "start-jobpilot.ps1"
$LauncherBat = Join-Path $RepoRoot "Start JobPilot.bat"
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$DesktopLauncher = if ($DesktopDir) { Join-Path $DesktopDir "Start JobPilot.bat" } else { $null }

$Ps1Content = @"
Set-StrictMode -Version Latest
`$ErrorActionPreference = "Stop"
Set-Location "$RepoRoot"
uv run python start.py
"@
Set-Content -Path $LauncherPs1 -Value $Ps1Content -Encoding UTF8

$BatContent = @"
@echo off
cd /d "$RepoRoot"
uv run python start.py
"@
Set-Content -Path $LauncherBat -Value $BatContent -Encoding ASCII

Write-Ok "Created launcher: $LauncherPs1"
Write-Ok "Created launcher: $LauncherBat"

if ($DesktopLauncher) {
    Copy-Item -Path $LauncherBat -Destination $DesktopLauncher -Force
    Write-Ok "Created desktop shortcut: $DesktopLauncher"
} else {
    Write-Warn "Desktop path not found — launcher created in repo root only."
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔═════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   ✅  JobPilot installed successfully!       ║" -ForegroundColor Green
Write-Host "╚═════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  To start JobPilot:" -ForegroundColor White
Write-Host "    $LauncherBat" -ForegroundColor Cyan
Write-Host ""
if ($DesktopLauncher) {
    Write-Host "  Desktop shortcut: $DesktopLauncher" -ForegroundColor Cyan
    Write-Host ""
}
Write-Host "  Then open: http://localhost:8000" -ForegroundColor Cyan
Write-Host ""
