# syntax=docker/dockerfile:1.7
# JobPilot — production container.
# Multi-stage: (1) Python deps via uv  (2) Frontend build via Node  (3) Slim runtime.

# ─── Stage 1: Python dependencies ────────────────────────────────────────────
FROM python:3.12-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned via official installer)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ─── Stage 2: Frontend build ─────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ─── Stage 3: Tectonic LaTeX engine ──────────────────────────────────────────
FROM python:3.12-slim AS tectonic-fetcher
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/tectonic
# Pin a known-good release; static musl build, no extra runtime deps.
ARG TECTONIC_VERSION=0.15.0
RUN ARCH="$(uname -m)" && \
    case "$ARCH" in \
        x86_64)  ASSET="tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" ;; \
        aarch64) ASSET="tectonic-${TECTONIC_VERSION}-aarch64-unknown-linux-musl.tar.gz" ;; \
        *) echo "unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL -o tectonic.tar.gz \
        "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic@${TECTONIC_VERSION}/${ASSET}" && \
    tar -xzf tectonic.tar.gz && \
    install -m 0755 tectonic /usr/local/bin/tectonic

# ─── Stage 4: Runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    JOBPILOT_HOST=0.0.0.0 \
    JOBPILOT_PORT=8000 \
    JOBPILOT_DATA_DIR=/app/data \
    JOBPILOT_SCRAPER_HEADLESS=true

# Runtime libs for Playwright/Chromium (headless) + wget for HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system --gid 1000 jobpilot && \
    useradd  --system --uid 1000 --gid jobpilot --create-home --shell /bin/bash jobpilot

WORKDIR /app

# Copy venv, tectonic, and app code
COPY --from=python-builder /app/.venv /app/.venv
COPY --from=tectonic-fetcher /usr/local/bin/tectonic /usr/local/bin/tectonic
COPY --chown=jobpilot:jobpilot backend/ ./backend/
COPY --chown=jobpilot:jobpilot alembic/ ./alembic/
COPY --chown=jobpilot:jobpilot alembic.ini start.py pyproject.toml ./
COPY --from=frontend-builder --chown=jobpilot:jobpilot /frontend/build ./frontend/build

# Data dir for SQLite/logs/browser sessions (mounted as a volume in compose).
RUN mkdir -p /app/data && chown -R jobpilot:jobpilot /app

USER jobpilot

# Install Playwright Chromium for browser-use (user-scoped cache).
RUN python -m playwright install chromium 2>/dev/null || true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8000/api/health || exit 1

# Launch uvicorn directly (bypasses start.py's webbrowser.open + 127.0.0.1 default).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
