#!/usr/bin/env bash
# JobPilot installer for Linux / macOS
# Idempotent: safe to run multiple times.
# Does NOT require sudo/root.
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()    { echo -e "\n${BOLD}──── $* ────${NC}"; }

# ── Repo root ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo -e "${BOLD}${CYAN}╔═════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         JobPilot Installer          ║${NC}"
echo -e "${BOLD}${CYAN}╚═════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check Python ≥ 3.12 ───────────────────────────────────────────────
step "1/9  Checking Python version"
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        success "Python $PY_VERSION found"
    else
        error "Python 3.12+ is required, but found $PY_VERSION"
        error "Please install Python 3.12 or newer and re-run this script."
        exit 1
    fi
else
    error "python3 not found. Please install Python 3.12+ and re-run."
    exit 1
fi

# ── Step 2: Check Node.js ≥ 18 ────────────────────────────────────────────────
step "2/9  Checking Node.js version"
if command -v node &>/dev/null; then
    NODE_VERSION=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 18 ]; then
        success "Node.js $NODE_VERSION found"
    else
        warn "Node.js $NODE_VERSION found, but ≥18 is recommended."
        warn "Frontend build may fail. Install Node.js 18+ for best results."
    fi
else
    warn "node not found — frontend build will be skipped."
    warn "Install Node.js 18+ if you want the web UI."
fi

# ── Step 3: Install uv ────────────────────────────────────────────────────────
step "3/9  Checking uv"
if command -v uv &>/dev/null; then
    success "uv already installed: $(uv --version)"
else
    info "Installing uv (Python package manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this script
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    if command -v uv &>/dev/null; then
        success "uv installed: $(uv --version)"
    else
        error "uv installation failed. Please install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
fi

# ── Step 4: Python dependencies ───────────────────────────────────────────────
step "4/9  Installing Python dependencies"
uv sync
success "Python dependencies installed"

# ── Step 5: Playwright browsers ───────────────────────────────────────────────
step "5/9  Installing Playwright Chromium"
if uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); p.stop()" &>/dev/null 2>&1; then
    success "Playwright Chromium already installed"
else
    uv run playwright install chromium --with-deps
    success "Playwright Chromium installed"
fi

# ── Step 6: Download Tectonic ─────────────────────────────────────────────────
step "6/9  Installing Tectonic (LaTeX engine)"
TECTONIC_BIN="$REPO_ROOT/bin/tectonic"
if [ -f "$TECTONIC_BIN" ] && "$TECTONIC_BIN" --version &>/dev/null 2>&1; then
    success "Tectonic already installed: $("$TECTONIC_BIN" --version 2>&1 | head -1)"
else
    info "Downloading Tectonic binary…"
    uv run python scripts/download_tectonic.py
    if [ -f "$TECTONIC_BIN" ] && "$TECTONIC_BIN" --version &>/dev/null 2>&1; then
        success "Tectonic installed: $("$TECTONIC_BIN" --version 2>&1 | head -1)"
    else
        warn "Tectonic download failed. PDF generation will be disabled."
        warn "You can manually install from: https://tectonic-typesetting.github.io"
    fi
fi

# ── Step 7: Frontend build ────────────────────────────────────────────────────
step "7/9  Building frontend"
if [ ! -f "frontend/build/index.html" ]; then
    if command -v node &>/dev/null && command -v npm &>/dev/null; then
        info "Installing frontend npm dependencies…"
        npm install --prefix frontend
        info "Building SvelteKit app…"
        npm run build --prefix frontend
        success "Frontend built"
    else
        warn "node/npm not found — skipping frontend build."
        warn "Install Node.js 18+ then run: cd frontend && npm install && npm run build"
    fi
else
    success "Frontend already built (frontend/build/index.html exists)"
fi

# ── Step 8: Data directories ──────────────────────────────────────────────────
step "8/9  Creating data directories"
mkdir -p data/cvs data/letters data/templates data/sessions data/pdfs data/logs
success "Data directories ready"

# ── Step 9: Environment file ──────────────────────────────────────────────────
step "9/9  Setting up .env"
if [ ! -f ".env" ]; then
    cp .env.example .env
    success ".env created from .env.example"
    echo ""
    echo -e "${YELLOW}  ⚠  ACTION REQUIRED: Edit .env and fill in your API keys:${NC}"
    echo -e "     ${BOLD}GOOGLE_API_KEY${NC}  — Gemini API key (free at aistudio.google.com)"
    echo -e "     ${BOLD}ADZUNA_APP_ID${NC}   — Adzuna app ID  (free at developer.adzuna.com)"
    echo -e "     ${BOLD}ADZUNA_APP_KEY${NC}  — Adzuna API key (free at developer.adzuna.com)"
else
    success ".env already exists"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔═════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   ✅  JobPilot installed successfully!       ║${NC}"
echo -e "${GREEN}${BOLD}╚═════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}To start JobPilot:${NC}"
echo -e "    ${CYAN}uv run python start.py${NC}"
echo ""
echo -e "  Then open: ${CYAN}http://localhost:8000${NC}"
echo ""
