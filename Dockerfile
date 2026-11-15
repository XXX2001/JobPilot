# syntax=docker/dockerfile:1.7
# JobPilot — production container.
# Multi-stage Python, frontend, Tectonic and optional locked CLI dependencies.

# ─── Stage 1: Python dependencies ────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS python-builder

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
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ─── Stage 3: Tectonic LaTeX engine ──────────────────────────────────────────
FROM python:3.12-slim-bookworm AS tectonic-fetcher
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/tectonic
# Pin a known-good release; static musl build, no extra runtime deps.
# MUST stay aligned with ``TECTONIC_VERSION`` in ``scripts/download_tectonic.py``
# so the container image and the local-dev installer get the same engine.
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

# Optional CLI dependencies: exact @anthropic-ai/claude-code / @openai/codex
# versions and artifact integrity hashes live in docker/cli/package-lock.json.
FROM node:22-bookworm-slim AS cli-builder
USER root
WORKDIR /opt/jobpilot-cli
COPY docker/cli/package.json docker/cli/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
# Claude's pinned postinstall links the locked native package into its bin path.
# Run that one required script offline; no untracked install-time downloads.
RUN --network=none node node_modules/@anthropic-ai/claude-code/install.cjs \
    && ./node_modules/.bin/claude --version \
    && ./node_modules/.bin/codex --version

# ─── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    JOBPILOT_HOST=0.0.0.0 \
    JOBPILOT_PORT=8000 \
    JOBPILOT_DATA_DIR=/app/data \
    JOBPILOT_SCRAPER_HEADLESS=true \
    JOBPILOT_CHROME_VIEW_URL="http://127.0.0.1:6080/vnc.html?autoconnect=true" \
    DISPLAY=:99 \
    HOME=/tmp/jobpilot-home \
    XDG_CONFIG_HOME=/tmp/jobpilot-home/.config \
    XDG_CACHE_HOME=/home/jobpilot/.cache \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# Google Chrome is amd64-only. Keep its repository key scoped with signed-by.
# Debian packages pin noVNC/websockify; never download an unverified git snapshot.
# Runtime libs for Playwright/Chromium + wget for HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates gnupg \
        xvfb x11-utils xauth x11vnc tini \
        novnc=1:1.3.0-1 websockify=0.10.0+dfsg1-4+b1 \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN test "$(dpkg --print-architecture)" = amd64 \
    && mkdir -m 0700 /tmp/google-gnupg \
    && wget -q -O /tmp/google-linux.asc \
        https://dl.google.com/linux/linux_signing_key.pub \
    && gpg --homedir /tmp/google-gnupg --batch --import /tmp/google-linux.asc \
    && GOOGLE_KEY_FINGERPRINT="$(gpg --homedir /tmp/google-gnupg --batch --with-colons \
        --fingerprint EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796 \
        | awk -F: '$1 == "pub" {primary=1; next} primary && $1 == "fpr" {print $10; primary=0}')" \
    && test "$GOOGLE_KEY_FINGERPRINT" = EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796 \
    && gpg --homedir /tmp/google-gnupg --batch --armor \
        --export "$GOOGLE_KEY_FINGERPRINT" > /usr/share/keyrings/google-linux.asc \
    && test -s /usr/share/keyrings/google-linux.asc \
    && rm -rf /tmp/google-gnupg /tmp/google-linux.asc \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && echo "path-include=/usr/share/doc/google-chrome-stable/changelog.gz" \
        > /etc/dpkg/dpkg.cfg.d/zz-jobpilot-chrome-build-date \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && test -s /usr/share/doc/google-chrome-stable/changelog.gz \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s vnc.html /usr/share/novnc/index.html

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
COPY docker/supervisor.py /app/docker/supervisor.py
COPY docker/check_chrome_age.py /app/docker/check_chrome_age.py
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/jobpilot-entrypoint

# Data dir for SQLite/logs/browser sessions (mounted as a volume in compose).
RUN mkdir -p /app/data /home/jobpilot/.cache && chown -R jobpilot:jobpilot /app /home/jobpilot

# Scrapers still use Playwright; install at build time outside the ephemeral HOME.
RUN python -m playwright install chromium \
    && chmod -R a+rX /opt/ms-playwright

USER jobpilot

EXPOSE 8000 6080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8000/api/health || exit 1

# Tini reaps orphaned Chrome children; the supervisor owns all service lifetimes.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/jobpilot-entrypoint"]
CMD ["python", "/app/docker/supervisor.py"]

# Optional CLI writer image, selected by docker-compose.cli.yml.
FROM runtime-base AS cli
USER root
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=cli-builder /opt/jobpilot-cli /opt/jobpilot-cli
RUN ln -s /opt/jobpilot-cli/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -s /opt/jobpilot-cli/node_modules/.bin/codex /usr/local/bin/codex
USER jobpilot

# The default image keeps the API writer path; CLI writers are opt-in.
FROM runtime-base AS runtime
