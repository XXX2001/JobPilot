# Launchers: native by default, Docker optional, each with its own Chrome

Date: 2026-11-15. Status: approved in chat by the owner.

## Why

JobPilot needs its own Chrome (the applications browser: Google, LinkedIn and job-site
logins, the harness's tabs). Today the owner starts it with `uv run uvicorn …` or
`python start.py`, with no checks; Docker ships only a headless Chromium, so the owner
cannot sign in, solve a captcha or press a final submit there. The owner wants one
launch command, with Docker as an option.

## Owner's decisions

1. **Native is the default**: `./jobpilot start | stop | status | logs`.
2. **Docker is optional**: `docker compose up -d` for those who want it.
3. **In Docker, Chrome is seen in a browser tab** (noVNC), my recommendation accepted
   after the owner asked which option is better and safer: sharing the desktop's X11
   socket would let the container read every key and window of the whole desktop.
4. **Docker writer: both options** — an API key in `.env`, or the owner's Claude Code /
   Codex CLI logins mounted read-only.

## Native launcher (`./jobpilot`, bash, Linux and macOS)

- `start`: checks, then starts the backend in the background (pid file and log under
  `data/`), waits for `/api/health`, starts the JobPilot Chrome (existing
  `ensure_chrome_running`) and opens or focuses the app tab in it (`show_app`). Prints
  the URL. Idempotent: already running → just focuses the tab.
- Checks, each with a one-line fix: `uv` present; Chrome present; frontend build present
  (offer `npm ci && npm run build`); Tectonic (offer `scripts/download_tectonic.py`);
  writing model (Claude Code, Codex or an API key: a warning, not a failure); port 8000
  free or ours.
- `stop`: stops the backend; `stop --chrome` also closes the JobPilot Chrome (its
  logins stay in the profile). `status`: backend, Chrome, logins (`/api/browser/status`),
  decision model reachability. `logs`: tails the backend log.
- `start.py` keeps working; the launcher may reuse it.

## Docker (optional)

- One image, processes under a small supervisor: Xvfb (virtual screen), Google Chrome on
  the JobPilot profile in the data volume with its debug port bound to 127.0.0.1 inside
  the container, x11vnc + noVNC for viewing, and the backend.
- Ports published on 127.0.0.1 only: 8000 (app) and 6080 (Chrome view). The debug port is
  never published. Remote servers: SSH tunnel or Tailscale.
- The Chrome view needs a password, generated on first start into the data volume
  (mode 600) and shown by the app's browser panel in Docker mode.
- Hardening: non-root user, no `privileged`, `no-new-privileges`, read-only root
  filesystem with tmpfs for /tmp and runtime dirs, data volume the only writable path,
  Chrome keeps its sandbox through a seccomp profile shipped in the repo (no
  `--no-sandbox`).
- The app knows it runs in Docker (`JOBPILOT_CHROME_VIEW_URL`): its "Open the JobPilot
  browser" / "Open sign-in pages" controls open the sign-in pages inside the container's
  Chrome and link the owner to the Chrome view instead of trying to show a local window.
- Writer: `LLM_API_KEY` in `.env`, or `docker-compose.cli.yml` (an override) that mounts
  `~/.claude` and `~/.codex` read-only and an image stage with the Claude Code and Codex
  CLIs; the entrypoint copies the credentials into the container user's home so the CLIs
  can write their own state. Documented: the container then holds those logins' powers.
- `DECISION_MODEL_URL` works unchanged (e.g. OpenJev on mouad-ba over Tailscale).

## Not doing

- Windows launcher (the existing PowerShell scripts stay as they are).
- Publishing images to a registry; TLS for the Chrome view.

## Left to the builder

- Supervisor choice (supervisord, s6, or a small shell entrypoint), noVNC packaging,
  the seccomp profile's origin (document it).
