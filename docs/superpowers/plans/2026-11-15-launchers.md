# Launchers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `./jobpilot start` runs JobPilot natively with its own Chrome after checking prerequisites; an optional Docker setup runs the app and its own Chrome in a hardened container, viewed through noVNC.

**Architecture:** A bash launcher wraps the existing backend and `backend/browser/chrome.py` helpers. The Docker image adds Xvfb, Google Chrome, x11vnc and noVNC under a small supervisor; the app learns a `JOBPILOT_CHROME_VIEW_URL` setting so its browser controls point at the noVNC view in Docker.

**Tech Stack:** bash, Python 3.12 / FastAPI, Docker + Compose, Xvfb, x11vnc, noVNC, Svelte 5.

**Spec:** `docs/superpowers/specs/2026-11-15-launchers-design.md`

## Global Constraints

- Native stays the default; nothing in the native path depends on Docker.
- Docker ports bind `127.0.0.1` only: `8000` (app), `6080` (noVNC). The Chrome debug port is never published.
- No `--no-sandbox` for the JobPilot Chrome in Docker; no `privileged`; `no-new-privileges:true`; non-root user; read-only root filesystem with tmpfs; the data volume is the only persistent writable path.
- The noVNC password lives in the data volume with mode 600 and is never logged.
- Mounted CLI credentials are read-only on the host side.
- Commit format `<type>(<scope>): <desc>`, under 72 chars, no co-author trailers.
- Python tests: `env -u PYTHONPATH .venv/bin/python -m pytest`.

## Review Focus

1. `./jobpilot start` run twice: the second run must not start a second backend or Chrome.
2. A stale pid file after a crash: `start` must not refuse or kill an unrelated process with that pid.
3. Port 8000 held by something else: `start` says so and stops; it never kills foreign processes.
4. Docker on a host where Chrome's sandbox cannot start under the seccomp profile: fail loudly with the documented fix, never silently fall back to `--no-sandbox`.
5. The noVNC password file missing or unreadable after a volume restore: regenerate, do not start unprotected.

---

### Task 1: Native launcher

**Files:**
- Create: `jobpilot` (bash, executable, repo root), `scripts/launcher/` helpers only if the script grows past ~250 lines
- Modify: `start.py` only if the launcher reuses it
- Test: `tests/test_launcher.py` (runs the script with stubbed commands on PATH via a temp dir)

**Interfaces:**
- Produces: `./jobpilot start|stop [--chrome]|status|logs|help`; pid file `data/jobpilot.pid`; log `data/logs/backend.out`; exit 0 on success, 1 on a failed check with the fix printed.

- [ ] **Step 1: Failing tests** (`tests/test_launcher.py`, subprocess with a fake PATH containing stub `uv`, `google-chrome`, `curl` scripts that record calls):
  - `start` with every prerequisite stubbed starts the backend once and records `/api/health` polling; a second `start` does not start another backend.
  - missing frontend build → exit 1, message contains `npm ci && npm run build`.
  - missing Tectonic → exit 1, message contains `scripts/download_tectonic.py`.
  - no writer (no claude, no codex, no LLM_API_KEY) → warning line, still exit 0.
  - stale pid file pointing at a dead pid → treated as stopped; a pid of a live non-JobPilot process → not killed by `stop`.
  - port 8000 busy by a foreign process → exit 1, "port 8000 is in use".
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** implement `jobpilot`. `start` runs `uv run uvicorn backend.main:app --host 127.0.0.1 --port ${JOBPILOT_PORT:-8000}` under `nohup` with `env -u PYTHONPATH`, waits up to 60 s for `/api/health`, then `uv run python -c "from backend.browser.chrome import show_app; ..."` to open the app in the JobPilot Chrome (falls back to printing the URL). The pid file stores the pid and the process start time (`/proc/<pid>/stat` or `ps -o lstart`) so a reused pid is never mistaken for ours.
- [ ] **Step 4:** PASS; then a real run on this machine: `./jobpilot start`, `./jobpilot status`, `./jobpilot stop`.
- [ ] **Step 5:** commit `feat(launcher): ./jobpilot start, stop, status and logs`.

### Task 2: Docker with its own Chrome (optional)

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example`, `backend/config.py` (`JOBPILOT_CHROME_VIEW_URL: str = ""`), `backend/api/browser.py`, `frontend/src/lib/components/BrowserLogins.svelte`, `frontend/src/routes/onboarding/+page.svelte` (browser controls only)
- Create: `docker/entrypoint.sh`, `docker/supervisord.conf` (or equivalent), `docker/chrome-seccomp.json`, `docker-compose.cli.yml`
- Test: `tests/test_browser_api.py` (Docker-mode responses), `tests/test_docker_config.py` (static checks of compose/Dockerfile)

**Interfaces:**
- Consumes: existing `ensure_chrome_running`, `running_endpoint`, `LOGIN_PAGES`.
- Produces: `GET /api/browser/status` adds `"view_url": str | None` (the noVNC URL when `JOBPILOT_CHROME_VIEW_URL` is set) and `"view_password_hint": "data/chrome-view.password"` in Docker mode; `POST /api/browser/open` in Docker mode opens the missing sign-in pages in the container's Chrome and returns `view_url`.

- [ ] **Step 1: Failing tests**: status/open include `view_url` when the setting is set and not otherwise; `tests/test_docker_config.py` parses `docker-compose.yml` and asserts every published port starts with `127.0.0.1:`, no `privileged`, `security_opt` includes `no-new-privileges:true` and the seccomp file, `read_only: true`, the debug port is not published; parses the Dockerfile for no `--no-sandbox`; `docker-compose.cli.yml` mounts are `:ro`.
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** implement. Image: Google Chrome stable (amd64; document arm64 as unsupported or Chromium fallback), Xvfb, x11vnc, noVNC/websockify, a supervisor; the entrypoint creates `data/chrome-view.password` (random, 600) if missing, copies mounted CLI credentials (if `/host-cli/.claude` or `/host-cli/.codex` exist) into `$HOME`, then starts Xvfb `:99`, Chrome with `--user-data-dir=/app/data/jobpilot-chrome --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1`, x11vnc with the password on `localhost:5900`, websockify/noVNC on `0.0.0.0:6080` (published as `127.0.0.1:6080` by compose), and uvicorn. Compose: `read_only: true`, `tmpfs: [/tmp, /run, /home/jobpilot/.cache]`, `security_opt: [no-new-privileges:true, seccomp=docker/chrome-seccomp.json]`, `shm_size: 1gb`. The seccomp profile is the widely used Chrome profile (cite its source in a comment) allowing the user-namespace syscalls Chrome's sandbox needs. `docker-compose.cli.yml` adds the CLI image stage (`npm i -g @anthropic-ai/claude-code @openai/codex`) and mounts `~/.claude`, `~/.claude.json`, `~/.codex` read-only under `/host-cli/`.
- [ ] **Step 4:** PASS; `docker compose build` and `docker compose up -d` on this machine: `/api/health` 200, noVNC at `http://127.0.0.1:6080` asks for the password and shows Chrome, `/api/browser/status` returns `running: true`; `docker compose -f docker-compose.yml -f docker-compose.cli.yml up -d`: `claude --version` and `codex --version` work as the container user. `docker compose down`.
- [ ] **Step 5:** commit `feat(docker): own Chrome viewed through noVNC, hardened`.

### Task 3: Docs

**Files:** `README.md` (a "Run it" section: native first, Docker optional), `docs/browser.md`, `docs/docker.md` (create: ports, password, remote access through SSH tunnel or Tailscale, the two writer options and the power of mounted logins, the seccomp profile and what to do if Chrome's sandbox fails), `CHANGELOG.md`.

- [ ] **Step 1:** write; every command in the docs was run in Task 1 or 2.
- [ ] **Step 2:** commit `docs: native and Docker launch`.
