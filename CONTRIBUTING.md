# Contributing to JobPilot

Thanks for working on JobPilot. This guide covers the development setup, the quality gates every change must pass, the baselines to respect, and the conventions we follow.

JobPilot is a single-user, self-hosted app: a FastAPI backend (Python 3.12) that also serves a compiled SvelteKit frontend (Node 20), backed by SQLite and the Tectonic LaTeX compiler. See [docs/architecture.md](docs/architecture.md) for the full picture.

---

## Development setup

You need **Python 3.12**, **Node.js 20**, and [`uv`](https://docs.astral.sh/uv/).

```bash
# Backend dependencies (creates the virtualenv from pyproject.toml + uv.lock)
uv sync

# Tectonic LaTeX compiler (CV/letter PDF generation)
uv run python scripts/download_tectonic.py

# Frontend dependencies + build
cd frontend && npm ci && npm run build && cd ..

# Environment
cp .env.example .env   # then fill GOOGLE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY
```

Run the app locally with `uv run python start.py` (serves the built frontend at http://localhost:8000), or develop the frontend with hot reload via `cd frontend && npm run dev` (proxies API calls to the backend on port 8000).

---

## Quality gates

Every change must pass these before it is merged. Run them locally first — CI runs the same commands.

| Gate | Command | Requirement |
| --- | --- | --- |
| Backend tests | `uv run pytest` | Green (no failures) |
| Backend types | `uv run pyright backend/` | At or below the baseline ceiling (see below) |
| Frontend types/lint | `cd frontend && npm run check` | **0 errors / 0 warnings** |
| Frontend tests | `cd frontend && npm run test` | Green (vitest) |

The backend also has linting available via `uv run ruff check backend/ tests/`.

### Baselines to respect

- **`pyright backend/`** carries pre-existing type debt. The documented ceiling is **19 errors / 8 warnings** (this is also the threshold enforced by CI — keep the two in sync). Pre-existing debt does not block you, but do **not** push the counts above this ceiling — new type errors must be fixed before merge. If you reduce the count, that's welcome; lowering the ceiling is its own change.
- **`svelte-check` (`npm run check`)** is held at **0 errors / 0 warnings**. The frontend has no slack here: any new error or warning fails the gate.

---

## Commit message style

Look at the recent history for the exact convention:

```bash
git log --oneline -20
```

Commits use **Conventional-Commits-style prefixes**, for example:

- `feat(onboarding): add /onboarding first-run stepper with redirect gate`
- `fix(scraping): add missing SERPAPI_KEY to .env.example`
- `refactor(settings): split the Settings page into per-tab components`
- `docs(api): document the WebSocket protocol`

Format: `type(scope): short imperative summary`. Common types are `feat`, `fix`, `refactor`, `docs`, `ci`. The scope is a feature area (e.g. `onboarding`, `applier`, `scraping`). Keep the summary concise and in the imperative mood.

---

## Documentation

- End-user docs: [docs/user-guide.md](docs/user-guide.md).
- System internals: [docs/architecture.md](docs/architecture.md).
- Keep docs accurate — only document features that actually exist (verify against routes/endpoints).
