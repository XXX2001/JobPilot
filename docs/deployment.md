# Deployment & install

How to self-host JobPilot in production via Docker, or run it locally for development — covering the setup/install scripts, the multi-stage image, Compose hardening, and data persistence.

JobPilot ships as a single FastAPI service (SvelteKit frontend baked in) plus a bundled SQLite database, the Tectonic LaTeX engine, and a headless Chromium for browser automation. There are two supported paths: **Docker** (recommended for self-hosting) and **local dev** (`uv` + Node + Tectonic + `start.py`). Both share the same `.env` produced by the interactive setup scripts.

---

## 1. Configure `.env` (both paths)

Every install starts by writing a valid `.env`. Use the interactive setup script — it picks an AI provider, wires the right env vars, and generates a persistent credential-encryption key.

| OS | Command |
| --- | --- |
| Linux / macOS | `bash scripts/setup.sh` |
| Windows (PowerShell) | `.\scripts\setup.ps1` |

`scripts/setup.sh:53` copies `.env.example` to `.env`, then blanks the example's placeholder credentials so leftover strings like `your_gemini_api_key` are not read as real values (`scripts/setup.sh:58-62`). It then prompts for a provider:

| Choice | Sets | Notes |
| --- | --- | --- |
| 1 — Local / self-hosted | `LLM_PROVIDER=openai` + `LLM_BASE_URL` (+ matching `BROWSER_LLM_*`) | OpenAI-compatible servers (Ollama, llama.cpp, LM Studio, vLLM). Defaults base URL to `http://host.docker.internal:11434/v1`. Embeddings can point at Gemini (free) or the same local server (`scripts/setup.sh:73-98`). |
| 2 — Google Gemini | `LLM/EMBEDDING/BROWSER_LLM_PROVIDER=gemini`, `GOOGLE_API_KEY` | Free tier; one key covers all three roles. |
| 3 — OpenAI | all three providers `=openai`, `OPENAI_API_KEY` | |
| 4 — Anthropic | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` | Generation only; embeddings + browser agent fall back to Gemini, so it also asks for a `GOOGLE_API_KEY` (`scripts/setup.sh:113-124`). |

It optionally prompts for Adzuna keys (the job-search source) and finally writes a Fernet `CREDENTIAL_KEY` (`scripts/setup.sh:140`). The PowerShell variant (`scripts/setup.ps1`) is functionally identical and is built for the Docker Desktop path — no Python/uv/Node required, only PowerShell.

> ### CREDENTIAL_KEY must persist
> The setup scripts write `CREDENTIAL_KEY` into `.env` on purpose. In Docker the `env_file` is **not** writable from inside the container, so a key generated at runtime would be regenerated on every restart — and every previously stored credential would become undecryptable. Keep the same `.env` (and its `CREDENTIAL_KEY`) for the life of your data. See `scripts/setup.sh:137-140`.

---

## 2. Path A — Docker (recommended)

### Compose v2 requirement

JobPilot requires **Docker Engine 20.10+** and the **Docker Compose v2 plugin** (the `docker compose` subcommand), not the legacy `docker-compose` v1 binary. Docker Desktop bundles v2. On a Linux server that only has v1, install the plugin once (`README.md:33-41`):

```bash
sudo apt-get install docker-compose-plugin        # Debian/Ubuntu, OR:
mkdir -p ~/.docker/cli-plugins && curl -fsSL \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o ~/.docker/cli-plugins/docker-compose && chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version    # verify
```

**v1 fallback:** the compose file is compatible with v1 syntax — `docker-compose up -d --build` (note the hyphen) works if you cannot install the plugin (`README.md:146`).

### Build and run

```bash
# Linux / macOS
bash scripts/setup.sh
docker compose up -d --build

# Windows (PowerShell)
.\scripts\setup.ps1
docker compose up -d --build
```

Then browse to `http://<host>:8000` and complete the in-app onboarding wizard. The build + boot path has been verified healthy.

### The image (`Dockerfile`)

A four-stage multi-stage build keeps the runtime image slim:

| Stage | Base | Purpose |
| --- | --- | --- |
| `python-builder` | `python:3.12-slim` | Installs Python deps with **uv** (`uv:0.5.11` copied from `ghcr.io/astral-sh/uv`) via `uv sync --frozen --no-install-project --no-dev` against `pyproject.toml` + `uv.lock` (`Dockerfile:6-22`). |
| `frontend-builder` | `node:20-slim` | `npm ci` then `npm run build` of the SvelteKit frontend (`Dockerfile:25-30`). |
| `tectonic-fetcher` | `python:3.12-slim` | Downloads a pinned **Tectonic** static-musl binary (`ARG TECTONIC_VERSION=0.15.0`, arch-aware x86_64/aarch64) and installs it to `/usr/local/bin/tectonic` (`Dockerfile:33-51`). |
| `runtime` | `python:3.12-slim` | Slim final image — see below. |

The **runtime** stage (`Dockerfile:54-100`):

- Installs only the shared libraries headless Chromium/Playwright needs (`libnss3`, `libgbm1`, `libasound2`, …) plus `wget` for the healthcheck (`Dockerfile:65-70`).
- Creates a non-root `jobpilot` user (uid/gid 1000) and runs as it (`Dockerfile:73-89`).
- Copies the prebuilt venv, the Tectonic binary, backend code, Alembic, `start.py`, and the frontend `build/` output (`Dockerfile:79-84`).
- Installs Playwright Chromium user-scoped (`python -m playwright install chromium`, `Dockerfile:92`).
- Defines a `HEALTHCHECK` hitting `/api/health` and launches **uvicorn directly** — `CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]` — bypassing `start.py`'s `webbrowser.open` and its `127.0.0.1` default (`Dockerfile:96-100`).

> The Tectonic version pin in `Dockerfile:41` must stay aligned with `TECTONIC_VERSION` in `scripts/download_tectonic.py:34` so the container and local-dev paths install the same engine.

### Compose hardening (`docker-compose.yml`)

The single `jobpilot` service applies several production-oriented settings:

| Setting | Value | Why |
| --- | --- | --- |
| `shm_size` | `"1gb"` | Chromium (browser-use/Playwright) crashes with the default 64 MB `/dev/shm` (`docker-compose.yml:39`). |
| `extra_hosts` | `host.docker.internal:host-gateway` | Makes a model running on the Docker host reachable from inside the container; adds Linux support for what Docker Desktop already provides (`docker-compose.yml:42-43`). |
| `restart` | `unless-stopped` | Survives reboots / crashes. |
| `logging` | `json-file`, `max-size: 10m`, `max-file: 3` | Caps log growth so a long-running container can't fill host disk (`docker-compose.yml:61-65`). |
| `healthcheck` | `wget --spider http://localhost:8000/api/health` | 30s interval, 5s timeout, 3 retries, **40s `start_period`** because first boot runs DB migrations + a Playwright check (`docker-compose.yml:66-72`). |
| `ports` | `8000:8000` | |
| `environment` | overrides `JOBPILOT_HOST=0.0.0.0`, sets `JOBPILOT_DATA_DIR=/app/data`, `JOBPILOT_SCRAPER_HEADLESS=true`, and `JOBPILOT_ALLOWED_ORIGINS` (CORS allow-list) | Binds to all interfaces so the container is reachable externally (`docker-compose.yml:48-56`). |

The compose file also documents (commented-out) a `postgres:16-alpine` service for swapping the bundled SQLite for Postgres via a `DATABASE_URL` env var (`docker-compose.yml:12-28`).

### Using a model on the host machine

From inside Docker, `localhost` is the container itself. Point `LLM_BASE_URL` (and `BROWSER_LLM_BASE_URL`) at `http://host.docker.internal:<port>/v1`. The setup script defaults to this, and `extra_hosts` makes the alias resolve on Linux. If the model runs on another machine, use that machine's LAN IP, e.g. `http://192.168.1.50:8080/v1` (`README.md:150`).

---

## 3. Path B — Local dev

**Prerequisites:** Python 3.12, Node.js 20, plus the Tectonic binary (downloaded by the script below).

```bash
uv sync                                         # install Python dependencies
uv run python scripts/download_tectonic.py      # download Tectonic into bin/
cd frontend && npm ci && npm run build && cd ..  # build the web UI
bash scripts/setup.sh                           # pick a provider, writes .env
uv run python start.py                          # launch
```

`scripts/download_tectonic.py` fetches the pinned engine (`TECTONIC_VERSION` defaults to `0.15.0`, override via env var) into `bin/tectonic`, skipping if a functional binary already exists (`scripts/download_tectonic.py:34`).

`start.py` is the local launcher:

- `check_prerequisites()` verifies the `data/` dir, `frontend/build/`, and the Tectonic binary exist, exiting with a clear message otherwise (`start.py:17-29`).
- It creates the data subdirectories (`cvs`, `letters`, `templates`, `browser_sessions`, `browser_profiles`, `logs`) (`start.py:106-114`).
- It honors `JOBPILOT_HOST` / `JOBPILOT_PORT` (default `127.0.0.1:8000`), frees the port via `free_port()` if something is already listening, opens a browser tab, then runs uvicorn (`start.py:119-148`).

Unlike Docker, the local default binds to `127.0.0.1` and opens your browser automatically.

---

## 4. Full installers (optional)

For a one-command local-dev bootstrap, `scripts/install.sh` (Linux/macOS) and `scripts/install.ps1` (Windows) bundle the Path-B steps. `install.sh` is idempotent and needs no sudo. It runs nine steps (`scripts/install.sh`):

1. Install `uv` and `uv python install 3.12`.
2. Check Node.js ≥ 18 (warns, does not fail).
3. `uv sync` Python deps.
4. Install Playwright Chromium (`--with-deps` on Linux) **and** patchright Chromium, which browser-use uses internally (`scripts/install.sh:74-106`).
5. Download Tectonic via `scripts/download_tectonic.py`.
6. Build the frontend (skipped with a warning if node/npm absent).
7. Create data directories and seed `data/templates/` from the bundled example CV (`scripts/install.sh:141-153`).
8. Configure `.env` by delegating to `scripts/setup.sh` (falls back to copying `.env.example`).
9. Create a `start-jobpilot.sh` launcher and a desktop shortcut (`.command` on macOS, `.desktop` on Linux).

Each browser/Tectonic/frontend step degrades gracefully — a failure warns rather than aborting, so the rest of the install completes.

---

## 5. Data persistence

All mutable state lives under `./data`, mounted as a volume in compose: `./data:/app/data` (`docker-compose.yml:57-59`). This holds the SQLite database, logs, generated CVs/letters, templates, and browser sessions/profiles, and survives container restarts and rebuilds. Combined with the persistent `CREDENTIAL_KEY` in `.env`, this is everything you need to back up.

> **Backup tip:** snapshot both `./data` and `.env` together. Restoring `./data` without the matching `CREDENTIAL_KEY` leaves stored credentials undecryptable.

---

## Related

- [Documentation index](index.md)
- [Architecture](architecture.md) — system overview, request lifecycles, DB schema, and the credentials & encryption reference
- [Config & Database module](modules/config-database.md) — all environment variables, the DB engine, and the FastAPI app entry point
- [User guide](user-guide.md) — end-to-end feature walkthrough, including in-app onboarding
- [Custom CV templates](custom-templates.md) — bring your own LaTeX CV template
- [Project README](../README.md) — quickstart and provider selection
