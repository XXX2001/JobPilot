# Development & contributing

> Developer workflow for JobPilot: repo layout, local setup, the test suite, and quality tooling. For the full contributor guide see [CONTRIBUTING.md](../CONTRIBUTING.md).

JobPilot is a single-user, self-hosted app: a **FastAPI** backend (Python 3.12) that also serves a compiled **SvelteKit** frontend (Node 20), backed by **SQLite** and the **Tectonic** LaTeX compiler. The launcher (`start.py`) runs both halves from one process.

---

## Repository layout

| Path | What lives there |
| --- | --- |
| `backend/` | FastAPI app. Subpackages: `api/` (routers + WebSocket), `applier/` (apply engine), `scraping/` (job-source orchestrator + fetchers), `latex/` (CV tailoring → Tectonic compile), `llm/` (provider factory, CV editor, prompts), `matching/` (fit engine), `gmail/`, `models/` (SQLAlchemy + schema), `scheduler/`, `security/`, `utils/`. Entry point is `backend/main.py:app`; settings in `backend/config.py`; DB engine in `backend/database.py`. |
| `frontend/` | SvelteKit app. Source under `src/`; the compiled site lands in `frontend/build/` (served by the backend in production). `npm run dev` runs Vite with hot reload. |
| `tests/` | Pytest suite (~90 files). `conftest.py` handles DB isolation; `factories.py` builds fixtures; `integration/` holds real-network tests. |
| `alembic/` + `alembic.ini` | Database migrations. The test bootstrap runs `init_db()` which exercises the migration path. |
| `scripts/` | Operational scripts: `download_tectonic.py`, `backup_db.py`, `migrate_legacy_applied.py`, plus `install.sh`/`install.ps1` and `setup.sh`/`setup.ps1`. |
| `bin/` | Bundled native binaries (the downloaded `tectonic` engine). |
| `data/` | Runtime data — `jobpilot.db`, generated CVs/letters, browser sessions, logs. Created on first run. |
| `docs/` | Architecture, subsystem, and reference documentation. |
| `start.py` | Launcher: prerequisite checks, port handling, `uvicorn` boot. |
| `pyproject.toml` | Python deps, dev tools, pytest/coverage/ruff config. |
| `Dockerfile`, `docker-compose.yml` | Container build (pins the same Tectonic version as `download_tectonic.py`). |

---

## Local setup

You need **Python 3.12**, **Node.js 20**, and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Backend dependencies (creates the venv from pyproject.toml + uv.lock)
uv sync

# 2. Tectonic LaTeX compiler (CV/letter PDF generation)
uv run python scripts/download_tectonic.py

# 3. Frontend dependencies + production build
cd frontend && npm ci && npm run build && cd ..

# 4. Environment
cp .env.example .env   # then fill LLM_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY
```

`download_tectonic.py` fetches the platform-correct Tectonic binary into `bin/tectonic` (or `bin/tectonic.exe` on Windows) and skips if a working binary already exists. Its version pin must stay aligned with the `TECTONIC_VERSION` ARG in `Dockerfile` so local dev and the container install the same engine.

### Running the app

```bash
uv run python start.py            # serves the built frontend at http://localhost:8000
```

`start.py:main` (`start.py:100`) first runs `check_prerequisites()` (`start.py:17`), which fails fast if the `data/` dir, the `frontend/build/` output, or the `tectonic` binary are missing. It honors `JOBPILOT_HOST` / `JOBPILOT_PORT` (the same vars `backend/config.py` reads), frees the target port via `free_port()` (`start.py:39`), opens a browser tab, then boots `backend.main:app` under uvicorn.

For frontend work, run the dev server instead, which proxies API calls to the backend on port 8000:

```bash
cd frontend && npm run dev
```

---

## Running the test suite

```bash
uv run pytest                      # full suite
uv run pytest -q                   # quiet (what CI runs)
uv run pytest tests/test_smoke.py  # a single file
```

Pytest config lives in `pyproject.toml` (`[tool.pytest.ini_options]`):

- **`asyncio_mode = "auto"`** — `async def` tests run without an explicit `@pytest.mark.asyncio` decorator. The suite leans heavily on async because the backend is async end-to-end.
- **`testpaths = ["tests"]`**, with `frontend`, `.venv`, `node_modules` etc. excluded via `norecursedirs`.
- A long `addopts` string disables several auto-loaded ROS/launch pytest plugins that would otherwise interfere.

### Per-worker DB isolation

`tests/conftest.py` implements the test-DB strategy. The key invariant: **`JOBPILOT_DATA_DIR` is set before any backend import** (`tests/conftest.py:46`), because `backend.database` builds its engine at import time from `settings.jobpilot_data_dir`.

- **One SQLite file per pytest worker.** Under `pytest-xdist -n auto`, each worker (`gw0`, `gw1`, …) gets its own `tempfile.mkdtemp` data dir, keyed off `PYTEST_XDIST_WORKER`; single-process runs fall back to `main`. Workers never share a file.
- **Schema bootstrap once per worker.** A session-scoped autouse fixture (`_bootstrap_test_db`, `tests/conftest.py:72`) runs `init_db()` — deliberately using the Alembic `upgrade head` path rather than `Base.metadata.create_all`, so migrations are exercised against every test DB.
- **Per-test wipe.** A function-scoped autouse fixture (`_reset_db_between_tests`, `tests/conftest.py:127`) `DELETE`s every table *before* each test (failed rows survive for post-mortem inspection via `sqlite3`). It uses `PRAGMA defer_foreign_keys = ON` so the wipe order isn't load-bearing.
- Dummy `LLM_API_KEY` / `ADZUNA_*` values are seeded so `Settings()` loads on a fresh checkout or CI machine with no real `.env`.

Shared fixtures: `test_app` (a Starlette `TestClient` over `backend.main:app`) and `test_settings` (a deterministic `Settings` instance).

### Integration / smoke tests

A single custom marker is registered: **`integration`** — "real-network integration tests; auto-skip when the endpoint is unreachable."

- `tests/integration/test_full_pipeline.py` exercises the end-to-end pipeline.
- `tests/test_local_provider_smoke.py` is marked `integration` **and** `skipif` on a reachability probe (`_endpoint_reachable`, `tests/test_local_provider_smoke.py:35`): it hits a local OpenAI-compatible LLM endpoint and skips cleanly when nothing is listening, so the default suite stays green offline.
- Plain unit-level smoke files (`test_smoke.py`, `test_provider_smoke.py`, `test_gmail_smoke.py`) are unmarked and always run.

### Coverage

`pytest-cov` is configured (`[tool.coverage.run]`) with `source = ["backend"]`, branch coverage on, and `__init__.py` / test files omitted:

```bash
uv run pytest --cov
```

---

## Quality gates

Every change must pass these before merge. CI (`.github/workflows/ci.yml`) runs the same commands in two parallel jobs (backend + frontend).

| Gate | Command | Requirement |
| --- | --- | --- |
| Backend tests | `uv run pytest` | Green |
| Backend types | `uv run pyright backend/` | At or below the baseline ceiling |
| Backend lint | `uv run ruff check backend/ tests/` | Clean |
| Frontend types/lint | `npm run check -- --fail-on-warnings` (in `frontend/`) | **0 errors / 0 warnings** |
| Frontend tests | `npm run test` (vitest) (in `frontend/`) | Green |

### Baselines

- **pyright** carries pre-existing type debt. The ceiling is **19 errors / 8 warnings**, enforced by the CI step (which parses the pyright summary line and fails only if the counts regress above it). Keep this in sync with [CONTRIBUTING.md](../CONTRIBUTING.md). New errors must be fixed; reducing the count is welcome.
- **svelte-check** is held at **0 errors / 0 warnings** — CI adds `--fail-on-warnings` since `svelte-check` does not fail on warnings by default.

### Linting and tooling

- **Ruff** is the backend linter/formatter (`[tool.ruff]` in `pyproject.toml`): `line-length = 100`, rule sets `E`, `F`, `I` (pycodestyle, pyflakes, isort). Run with `uv run ruff check backend/ tests/`.
- **Vulture** is used ad hoc for dead-code sweeps (unused functions, unreachable code) at `--min-confidence 80`, paired with ruff `--fix` for unused imports/bindings. It is not wired into CI; run it manually when doing a cleanup pass.
- **Codegraph index** — the repo ships a `.codegraph/` SQLite knowledge graph of every symbol/edge/file, queryable via the codegraph MCP tools for fast code navigation. It is regenerated by a file watcher and is gitignored; it is a developer convenience, not a build dependency.

### Pre-commit credential guard

A local (not version-controlled) `.git/hooks/pre-commit` hook blocks secrets from being committed. It:

1. Rejects any staged `.env` / `.env.*` file (but allows `.env.example`).
2. Scans **added lines only** for credential-shaped values — Google AI keys (`AIza…`), OpenAI (`sk-…`), Anthropic (`sk-ant-…`), and PEM private keys — skipping `.env.example` and `*.lock`/`uv.lock`.

It aborts the commit on a match. For a genuine false positive, bypass with `git commit --no-verify`. Because the hook is not in version control, install it manually after cloning.

### Commit style

Conventional-Commits — `type(scope): summary`, e.g. `feat(scraping): add batch dry-run preview` or `fix(config): add missing SERPAPI_KEY to .env.example`. Run `git log --oneline -20` for the live convention.

---

## Related

- [CONTRIBUTING.md](../CONTRIBUTING.md) — the canonical contributor guide.
- [README.md](../README.md) — project overview, install, and quick start.
- [docs/architecture.md](architecture.md) — system internals and request lifecycles.
- [docs/api-reference.md](api-reference.md) — REST + WebSocket reference.
- [docs/file-map.md](file-map.md) — every backend file and its responsibility.
- [docs/index.md](index.md) — full documentation index.
