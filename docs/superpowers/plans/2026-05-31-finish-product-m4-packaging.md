# M4 — Packaging, Docs & CI — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-31-finish-product-roadmap-design.md` §8
**Branch:** `feat/finish-product` · **Method:** subagent-driven dev, commit per task.

Baseline at M4 start: backend **557 passed, 5 skipped**; `pyright backend/` **41 errors / 8 warnings**; `svelte-check` **0 errors / 0 warnings**; frontend `vitest` green. Existing infra (already present, in good shape): multi-stage `Dockerfile` (uv + frontend build + Tectonic + slim runtime, non-root, healthcheck), `docker-compose.yml` (single service, volume, env_file), `.env.example`, `start.py` launcher, `scripts/download_tectonic.py`. Missing: `LICENSE`, `CONTRIBUTING.md`, CI, and the Pydantic V2 `Field(env=)` deprecation cleanup.

---

## M4-T1 — One-command install: verify + document

The Docker path largely exists; close the gaps and make it trustworthy.
- Validate the compose/Dockerfile: `docker compose config` parses; confirm `.env.example` lists EVERY env var the app reads (cross-check `backend/config.py` settings vs `.env.example`) and add any missing keys with comments. Do NOT attempt a full image build in this task (heavy/network) — static validation + a documented build command is enough.
- Confirm `scripts/download_tectonic.py` `TECTONIC_VERSION` matches the Dockerfile ARG (the Dockerfile comment says they must stay aligned) — fix drift if any.
- Ensure `start.py check_prerequisites` gives actionable messages and the data-dir bootstrap is correct.
- No code rewrite unless something is genuinely broken; this task is mostly verification + small fixes, feeding the README quickstart in M4-T2.

**Verify:** `docker compose config` exits 0; `.env.example` ⊇ config keys (list the diff in the report). Backend suite unaffected.
**Commit:** `chore(M4): validate one-command install + .env.example completeness`.

---

## M4-T2 — Docs & licensing

- **`LICENSE`**: add MIT. Copyright line: `Copyright (c) 2026 JobPilot contributors` (neutral — the user can replace the holder name).
- **`README.md`**: rewrite the top into a crisp quickstart with TWO supported paths: (A) Docker Compose (`cp .env.example .env` → fill keys → `docker compose up -d --build` → open `http://localhost:8000`); (B) local dev with `uv` (`uv sync`, `uv run python scripts/download_tectonic.py`, build frontend `cd frontend && npm ci && npm run build`, `uv run python start.py`). Keep/trim the existing content below the quickstart. State prerequisites (Python 3.12, Node 20, Docker optional; Google Gemini API key, Adzuna keys).
- **`docs/user-guide.md`** (new): end-to-end walkthrough — onboarding (`/onboarding`), configuring profile + keywords + sources, running a batch (and the dry-run preview), reviewing the queue (auto/assisted/manual, pre-submit field edit), the CV (`/cv`) and Letters (`/letters`) editors + template compile-test, the "Why this score" panel, the application tracker, and Gmail integration. Cross-link `docs/architecture.md` and the credentials/privacy doc if one exists.
- **`CONTRIBUTING.md`** (new): how to set up dev env (uv + frontend), run the gates (`uv run pytest`, `uv run pyright backend/`, `cd frontend && npm run check`, `npm run test`), the branch/commit conventions visible in `git log`, and the subagent/superpowers docs location. Mention the pyright baseline (41/8 ceiling) and svelte-check 0/0 requirement.

**Verify:** links resolve (relative paths exist); no secrets committed; markdown lints clean if a linter exists. The docs describe features that actually shipped in M2/M3 (no phantom features).
**Commit:** `docs(M4): MIT license, quickstart README, user guide, CONTRIBUTING`.

---

## M4-T3 — CI pipeline (GitHub Actions)

New `.github/workflows/ci.yml` running on push + pull_request:
- **backend job** (ubuntu, Python 3.12): install `uv` (astral-sh/setup-uv), `uv sync --frozen`, `uv run pytest -q`, and `uv run pyright backend/` gated by a threshold (FAIL if error count > 41 or warning count > 8 — the documented baseline; implement a tiny shell/py step that parses pyright's summary and exits non-zero above the ceiling, so pre-existing debt doesn't block but NEW debt does). Cache uv.
- **frontend job** (ubuntu, Node 20): `npm ci` in `frontend/`, `npm run check` (must be 0 errors/0 warnings → fail otherwise), `npm run test` (vitest), and `npx eslint`/the lint script if one exists. Cache npm.
- Keep jobs parallel; name them clearly. Do NOT run Docker image build in CI for now (note it as a future enhancement comment).
- Add a CI status badge line to the README (M4-T2 may already touch README; coordinate — fine to add the badge here).

**Verify:** `yamllint`/`actionlint` if available, or at least valid YAML (parse it). Confirm the threshold step logic by running the equivalent commands locally and checking the exit codes (pyright at baseline → pass; simulate +1 error → fail).
**Commit:** `ci(M4): GitHub Actions (pytest, pyright-threshold, svelte-check, vitest, lint)`.

---

## M4-T4 — Pydantic V2 `Field(env=)` deprecation cleanup

`backend/config.py` uses `Field(..., env=...)` (deprecated under pydantic-settings v2; it emits deprecation warnings — part of the 29 pytest warnings / contributes to the pyright warnings). Migrate to the V2-idiomatic form:
- Prefer `pydantic_settings`' env-name resolution: rely on `model_config = SettingsConfigDict(env_prefix=...)` where the field name maps cleanly, or use `validation_alias=AliasChoices(...)` / `Field(validation_alias=...)` for fields whose env var name differs from the attribute. Read `config.py` carefully and choose the minimal change that preserves EXACT env-var names (e.g. `GOOGLE_API_KEY`, `JOBPILOT_*`, `ADZUNA_*`) — env var names MUST NOT change or existing `.env` files break.
- Goal: zero `Field(env=)` usages; deprecation warnings for these gone; settings still load identically from the same env vars.

**TDD/verify:** a test (extend `tests/test_config.py` if it exists, else new) that sets the relevant env vars and asserts `Settings()` picks them up with the SAME names as before; assert no `Field(env=` remains (`rg -n "env=" backend/config.py`). Confirm the pytest warning count drops and `uv run pytest -q && uv run pyright backend/` stays green (≤ 41/8).

**Commit:** `refactor(M4): migrate config to Pydantic v2 env aliases (drop Field(env=))`.

---

## M4 verification (end of milestone)
`uv run pytest -q` green (≥ 557, ideally fewer warnings), `uv run pyright backend/` ≤ 41/8, `cd frontend && npm run check` 0/0 + vitest green, `docker compose config` exits 0, the new CI YAML parses, `LICENSE`/`CONTRIBUTING.md`/`docs/user-guide.md` present and link-valid.
