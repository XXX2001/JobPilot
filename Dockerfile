# ── Stage 1: Build the SvelteKit frontend ────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./frontend/
RUN npm install --prefix frontend
COPY frontend/ ./frontend/
RUN npm run build --prefix frontend
# Output lands in frontend/build/ (SvelteKit default)

# ── Stage 2: Python runtime ───────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (same as the install script does)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# ── Cached layers: only re-run if these files change ──────────

# 1. Python deps
COPY pyproject.toml uv.lock* ./
RUN uv python install 3.12 && uv sync --frozen

# 2. Playwright + all its OS-level deps (--with-deps handles apt internally)
RUN uv run playwright install chromium --with-deps

# 3. Download Tectonic binary into ./bin/tectonic
COPY scripts/ ./scripts/
RUN uv run python scripts/download_tectonic.py

# ── App code (changes often, placed last) ─────────────────────
COPY --from=frontend-builder /app/frontend/build ./frontend/build
COPY . .

# Create all data subdirs + seed default CV template if bundled
RUN mkdir -p data/cvs data/letters data/templates data/sessions \
    data/browser_sessions data/browser_profiles data/pdfs data/logs && \
    if [ -d "scripts/defaults/templates" ]; then \
        cp scripts/defaults/templates/* data/templates/ 2>/dev/null || true; \
    fi

ENV JOBPILOT_HOST=0.0.0.0
ENV JOBPILOT_PORT=8000
ENV JOBPILOT_SCRAPER_HEADLESS=true
ENV JOBPILOT_DATA_DIR=/app/data

EXPOSE 8000
VOLUME ["/app/data"]

CMD ["uv", "run", "python", "start.py"]
