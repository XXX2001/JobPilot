# JobPilot — Naming & Code-Standards Backlog (2026-05-22)

Pre-ship audit of `backend/` + `tests/` for **professional naming** and **Python code standards**.
Each row links a self-contained mini-report scoped as **one agent-executable refactor unit**.

> Scope: Python backend + tests (`backend/**`, `tests/**`, `start.py`, `scripts/`, `alembic/`). Frontend (JS/TS) excluded.
> Source: 5 parallel read-only scans (naming, error-handling, type-safety, docs/dead-code, standards/structure).
> Some items recur from the older [code-review.md](../../code-review.md) — cross-referenced where relevant.

## How to use this backlog
- Pick a task by ID. Each file is self-contained: problem, locations (`file:line`), proposed change, acceptance criteria, blast radius, dependencies.
- Respect the **Dependencies** field — e.g. most type-safety tasks need `TY-01` (enable Pyright) done first so fixes are verifiable.
- **Wire contracts must not be renamed blindly**: HTTP route paths, JSON field names, DB columns, and `env=` bindings are external contracts. Tasks call these out explicitly.

## Priority tiers

### 🚢 Ship blockers — do before release
| ID | Title | Cat | Effort | Why blocker |
|---|---|---|---|---|
| [RG-01](cross-cutting/RG-01-fix-regenerate-endpoint.md) | Fix or finish the `/regenerate` documents endpoint | feature/naming | M | Endpoint lies — returns `"queued"` but does nothing |
| [ST-01](standards/ST-01-cors-lockdown.md) | Lock down permissive CORS | security | S | `allow_origins=["*"]` + credentials in prod |
| [ST-02](standards/ST-02-secretstr-secrets.md) | Type API secrets as `SecretStr` | security | M | Keys render in logs/tracebacks/`repr` |
| [EH-01](error-handling/EH-01-record-application-commit.md) | Stop swallowing DB-commit failure when recording an application | error-handling | S | Apply sent but no record → re-applies, data loss |
| [EH-02](error-handling/EH-02-assisted-apply-false-success.md) | Don't report success when assisted-apply agent throws | error-handling | S | User told form is ready in a stopped browser |
| [EH-05](error-handling/EH-05-http-exception-leak.md) | Stop leaking raw exception text to HTTP clients | error-handling | S | Internal paths/tokens/SQL can leak to UI |

### 🔴 High — naming the owner asked for + correctness
| ID | Title | Cat | Effort | Notes |
|---|---|---|---|---|
| [NM-01](naming/NM-01-rename-morning-batch.md) | Rename `morning_batch` / `MorningBatchRunner` → neutral term | naming | M | The owner's #1 example; "morning" is also factually wrong (on-demand) |
| [TY-01](type-safety/TY-01-enable-pyright.md) | Enable Pyright checking + stubs | type-safety | S+backlog | `typeCheckingMode:"off"` today — unlocks all other TY tasks |
| [EH-03](error-handling/EH-03-apply-json-parse.md) | Log credential/profile JSON parse failures in apply endpoint | error-handling | S | Silent dropping of user answers |
| [EH-04](error-handling/EH-04-session-autologin-silent.md) | Surface auto-login failures in `SessionManager` | error-handling | M | Login failure looks like "no creds" |
| [DC-01](docs-deadcode/DC-01-remove-apscheduler-deadcode.md) | Remove never-started APScheduler scaffolding | dead-code | S | Implies scheduling exists when it doesn't (cf. code-review HR-01) |

### 🟡 Medium — naming consistency, typing, structure
| ID | Title | Cat | Effort |
|---|---|---|---|
| [NM-02](naming/NM-02-jobpilot-config-prefix.md) | Drop `jobpilot_*` brand prefix on config fields (keep `env=`/DB names) | naming | M |
| [NM-03](naming/NM-03-site-source-terminology.md) | Unify "site" vs "source" terminology (internal only) | naming | L |
| [EH-06](error-handling/EH-06-ws-receive-loop.md) | Tighten WebSocket receive loop (no swallow-and-spin) | error-handling | M |
| [EH-07](error-handling/EH-07-typed-domain-exceptions.md) | Replace generic `RuntimeError` with typed domain exceptions | error-handling | M |
| [EH-08](error-handling/EH-08-fire-and-forget-task.md) | Retain reference + done-callback for fire-and-forget task | error-handling | S |
| [EH-09](error-handling/EH-09-observable-silent-fallbacks.md) | Make silent except-blocks observable (logging) — 5 sites | error-handling | S |
| [TY-02](type-safety/TY-02-remove-import-type-ignore.md) | Remove 47 `# type: ignore` on third-party imports | type-safety | M |
| [TY-03](type-safety/TY-03-api-return-annotations.md) | Add return annotations to all API route handlers | type-safety | M |
| [TY-04](type-safety/TY-04-annotate-untyped-params.md) | Annotate untyped params (Playwright `page`, DI collaborators) | type-safety | S |
| [TY-05](type-safety/TY-05-formfiller-typeddict.md) | Replace bare `dict` apply-boundary return with `TypedDict` | type-safety | M |
| [TY-06](type-safety/TY-06-typing-style-consistency.md) | Standardize `X \| None` + lowercase generics (+ ruff `UP`) | type-safety | M |
| [TY-08](type-safety/TY-08-review-coded-type-ignore.md) | Review 10 coded `# type: ignore[...]` for masked bugs | type-safety | M |
| [DC-02](docs-deadcode/DC-02-remove-generate-diff.md) | Remove unused legacy `generate_diff` helper | dead-code | S |
| [DC-04](docs-deadcode/DC-04-add-init-docstrings.md) | Add missing `__init__`/validator docstrings | docs | M |
| [ST-03](standards/ST-03-fernet-helper.md) | Extract duplicated Fernet credential logic into one helper | structure | M |
| [ST-04](standards/ST-04-centralize-constants.md) | Centralize magic numbers + path literals into `defaults.py` | structure | M |
| [ST-05](standards/ST-05-utcnow-migration.md) | Migrate `datetime.utcnow()` → tz-aware + consolidate `_now()` | structure | M |
| [ST-06](standards/ST-06-jobboard-url-registry.md) | Move hardcoded job-board URLs into the site-config registry | structure | M |
| [ST-09](standards/ST-09-singletons-import-sideeffects.md) | Move `.env`-writing bootstrap out of import time | structure | M |

### 🔵 Low — cleanups
| ID | Title | Cat | Effort |
|---|---|---|---|
| [NM-04](naming/NM-04-rename-concurrency-gemini.md) | Rename `CONCURRENCY_GEMINI` → `GEMINI_MAX_CONCURRENCY` | naming | S |
| [NM-05](naming/NM-05-clean-html-encapsulation.md) | Stop calling private `ScraplingFetcher._clean_html` cross-module | naming | S |
| [TY-07](type-safety/TY-07-engine-implicit-optional.md) | Fix `model: str = None` implicit-Optional in `engine.py` | type-safety | S |
| [DC-03](docs-deadcode/DC-03-fix-stale-called-by.md) | Fix stale "Called by" docstring in `pipeline.py` | docs | S |
| [ST-07](standards/ST-07-hoist-imports.md) | Hoist stdlib/intra-package imports to module top | structure | S |
| [ST-08](standards/ST-08-refactor-large-functions.md) | Refactor the 8 largest multi-responsibility functions | structure | L |

## What's already clean (verified — don't re-flag)
- **No** TODO/FIXME/HACK markers in shippable code; **no** `print()` debug leftovers; **no** commented-out code blocks; **no** breakpoints.
- **No** wildcard imports; **no** mutable-default-argument bugs.
- Logging is consistent (`getLogger(__name__)`, lazy `%`-args, no f-strings in log calls).
- Module docstring coverage is 100%; class/function coverage high.
- LLM/scraper boundaries are well-typed (`JobContext`, `CVModifierOutput`, `RawJob`, `JobDetails`).
- The `try/except ImportError` fallback shims (`ws.py`, `ws_models.py`, `morning_batch.py`) are deliberate optional-dependency design — **do not delete**.

## Cross-references to the older code-review
| code-review.md finding | This backlog |
|---|---|
| CR-01 (no auth) / CORS | ST-01 (CORS); auth itself still open — see code-review |
| HR-02 (regenerate no-op) | RG-01 |
| HR-03 (CREDENTIAL_KEY plain str) | ST-02 |
| HR-01 (scheduler never started) | DC-01 (remove dead scaffolding) |
| LR-01 (`utcnow` deprecated) | ST-05 |
| LR-05 (hardcoded scraper consts) | ST-04 |
