# Configuration reference

Every JobPilot setting, where it lives, whether it is required, and the startup validation rules that decide which credentials you actually need.

All settings are defined as fields on the `Settings` class in `backend/config.py:11` and loaded from environment variables or a `.env` file (`backend/config.py:14`, `env_file=".env"`, `case_sensitive=False`). A template lives at `.env.example`. Because matching is case-insensitive, the lowercase field `jobpilot_host` reads the env var `JOBPILOT_HOST`, and so on.

Almost nothing is *required at the type level* — every credential field defaults to an empty string. The app can boot with a logged-in CLI, a local model, or no writing model. Explicit generation-provider requirements are enforced at startup by `Settings.validate_runtime_config()`, called from the FastAPI lifespan. Browser-model problems only produce warnings and disable Tier 2.

---

## How settings load

```
.env file  ─┐
env vars   ─┼─►  Settings()  ─►  _load_settings()  ─►  validate_runtime_config() at startup
defaults   ─┘    (config.py)     (friendly errors)     (provider-aware, fail-fast)
```

- `_load_settings()` (`backend/config.py:166`) instantiates `Settings`. On a pydantic `ValidationError` it prints a concise "missing X" banner pointing at `.env.example` and exits non-zero (so the launcher / Docker healthcheck detects it).
- After load, a `CREDENTIAL_KEY` is auto-generated if empty (`backend/config.py:202`; see below).
- `DATA_DIR` is resolved from `JOBPILOT_DATA_DIR` relative to the project root if not absolute (`backend/config.py:222`).

---

## AI provider selection

JobPilot has two independent LLM roles — generation and the browser agent — each with its own provider/model/base-URL/key, so you can mix providers. Job scoring and fit assessment use no model at all (see [Matching](matching.md)); `EMBEDDING_*` keys left in an older `.env` are ignored. `openai` here means **any** OpenAI-compatible endpoint — hosted OpenAI, a local server (Ollama, LM Studio, vLLM, llama.cpp), or another vendor's OpenAI-compatible endpoint — selected with `*_BASE_URL`.

### Generation (`LLM_*`)

| Env var | Field | Default | Required? |
|---|---|---|---|
| `LLM_PROVIDER` | `LLM_PROVIDER` | `auto` | One of `auto` \| `claude` \| `codex` \| `openai` \| `anthropic` |
| `LLM_MODEL` | `LLM_MODEL` | `""` (API provider default) | Optional; used by `openai` and `anthropic` |
| `LLM_CLI_MODEL` | `LLM_CLI_MODEL` | `""` | Optional; Claude uses `sonnet`, Codex uses its configured default |
| `LLM_BASE_URL` | `LLM_BASE_URL` | `""` | For OpenAI-compatible / local servers, e.g. `http://localhost:11434/v1` |
| `LLM_API_KEY` | `LLM_API_KEY` (secret) | `""` | See validation rules |
| `LLM_DISABLE_THINKING` | `LLM_DISABLE_THINKING` | `false` | For local reasoning models (Qwen3, DeepSeek-R1): when true, asks the server to skip chain-of-thought via `chat_template_kwargs.enable_thinking=false` — typically a large latency win (measured ~124s → ~16s on a CV-tailoring call). Ignored by servers that don't support the flag |

`config.py:49-52`. Provider-default and base-URL details are wired through `backend/llm/factory.py` (`make_llm_client`, used at `backend/main.py:141`).

With `LLM_PROVIDER=auto`, JobPilot chooses `claude` on PATH, then `codex`, then OpenAI
when `LLM_BASE_URL`, `LLM_API_KEY`, or `OPENAI_API_KEY` is configured, then Anthropic
when `ANTHROPIC_API_KEY` is configured. Log in to the chosen CLI beforehand; executable
detection does not check authentication. Set an explicit provider to override this order.
If none is available, startup logs "no writing model", health reports `llm_key_set=false`,
and generation calls fail with that message while other services remain available.

CLI calls send prompts on stdin, remove `PYTHONPATH` from the child environment, and
use `LLM_TIMEOUT_SECONDS`. Claude runs without tools, settings sources, MCP servers,
or session persistence; schema responses come from `structured_output`. Codex runs
in a fresh temporary directory with the read-only sandbox and ephemeral sessions,
and returns its last-message file. `LLM_CLI_MODEL` is separate from API model names.

### Browser decision harness

| Env var | Default | Effect |
|---|---|---|
| `DECISION_MODEL_URL` | empty | System One endpoint; non-empty runs the harness first for ASSISTED and AUTO |
| `DECISION_MODEL_API_KEY` | empty | Optional bearer key for that endpoint |
| `DECISION_MODEL_NAME` | `jev-latest` | Model name sent with each decision request |
| `HARNESS_MAX_ACTIONS` | `60` | Maximum actions per harness run |

For OpenJev on mouad-ba over Tailscale:

```env
DECISION_MODEL_URL=http://100.100.9.110:8009/v1/systemone
DECISION_MODEL_NAME=jev-latest
LLM_PROVIDER=auto
```

For hosted Jev, use `DECISION_MODEL_URL=https://api.typesafe.ai/v1/systemone` and set
`DECISION_MODEL_API_KEY` to its bearer key. Decision requests include the owner's
profile values, job title/company, page text and recent actions. The local endpoint
receives them on mouad-ba; the hosted endpoint sends them to TypeSafe. The separate
writing provider (`LLM_PROVIDER`, including Claude/Codex CLI) receives the base CV
source, profile, question, nearby page text and job context for free-text answers.
It is instructed to use only stored facts, answer in the question's language and
return at most 1,200 characters; absent facts leave the field empty. No available
writer is required for profile filling.

ASSISTED leaves the tab open for manual submission. AUTO still requires the existing
feature flag and owner review, with another review after corrections. An uncertain
post-click result stops for verification. Fallback to Tier 1/2 requires evidence that
no submitting control was clicked after typing. Empty `DECISION_MODEL_URL` preserves
the existing paths. Login/CAPTCHA hand-offs use the existing WebSocket frame and send
an ntfy push when `NTFY_URL` is set. See [browser behavior](browser.md#application-harness).

### Browser agent (`BROWSER_LLM_*`)

| Env var | Field | Default | Required? |
|---|---|---|---|
| `BROWSER_LLM_PROVIDER` | `BROWSER_LLM_PROVIDER` | `openai` | `openai` only (anthropic only via OpenAI-compatible `base_url`) |
| `BROWSER_LLM_MODEL` | `BROWSER_LLM_MODEL` | `""` | Optional |
| `BROWSER_LLM_BASE_URL` | `BROWSER_LLM_BASE_URL` | `""` | OpenAI-compatible endpoint |
| `BROWSER_LLM_API_KEY` | `BROWSER_LLM_API_KEY` (secret) | `""` | See validation rules |

`config.py:58-62`.

CLI providers supply text generation only. Tier 2 still needs a configured browser
endpoint or API key; otherwise it logs a warning and returns the application URL
for manual completion without starting the browser agent.

> Changes to any `LLM_*` / `BROWSER_LLM_*` value are **applied on restart** — clients are constructed once in the lifespan, not per-request.

### Using a local / self-hosted OpenAI-compatible server

Set the provider to `openai` and point `*_BASE_URL` at the server. Local endpoints usually need no key; any non-empty value works if one is required. Example (`.env.example:58-64`):

```env
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1     # Ollama
# LLM_BASE_URL=http://192.168.1.50:8080/v1 # llama.cpp server on the LAN
LLM_MODEL=Qwen3.6-27B-MTP                  # must match the served model id
```

---

## Credentials

| Env var | Field | Default | Required? |
|---|---|---|---|
| `OPENAI_API_KEY` | `OPENAI_API_KEY` (secret) | `""` | Fallback key for `openai`-provider roles when the generic `*_API_KEY` is empty |
| `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` (secret) | `""` | Required if generation provider is `anthropic` (or set `LLM_API_KEY`) |
| `ADZUNA_APP_ID` | `ADZUNA_APP_ID` | `""` | Optional — enables the Adzuna job source |
| `ADZUNA_APP_KEY` | `ADZUNA_APP_KEY` (secret) | `""` | Optional — paired with `ADZUNA_APP_ID` |
| `SERPAPI_KEY` | `SERPAPI_KEY` (secret) | `""` | Optional — Google-jobs search fallback; app runs fine without it |
| `CREDENTIAL_KEY` | `CREDENTIAL_KEY` (secret) | `""` → auto-generated | See below |

`config.py:21-27, 64-65`. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are per-provider keys used when the generic role key (`LLM_API_KEY` etc.) is empty.

If `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are unset, the lifespan logs a warning and disables the Adzuna source; other scraping sources and manual entry still work (`backend/main.py:109-113`).

### `CREDENTIAL_KEY` — generation, persistence, and the Docker caveat

`CREDENTIAL_KEY` is a Fernet key that **encrypts every stored credential** — `SiteCredential` rows, Gmail OAuth tokens, and OAuth-state HMACs in `jobpilot.db`.

- **Auto-generation**: if empty after load, `backend/config.py:202-218` generates a new Fernet key (`Fernet.generate_key()`) and **writes it back into `.env`** at the project root (replacing an existing `CREDENTIAL_KEY=` line, or appending one, or creating the file). This runs once on first launch — no installer needed.
- **Docker caveat**: inside a container the generated key **cannot be persisted** — `.env` is typically not writable / not mounted back, so a fresh key is minted on every restart and all previously encrypted data becomes undecryptable. The lifespan warns about exactly this (`backend/main.py:114-119`). **Set `CREDENTIAL_KEY` explicitly in `.env`** (the setup script does this) before running in Docker so stored credentials survive restarts.
- **No recovery**: losing the key means re-entering every site credential and re-running the Gmail OAuth flow. Back the generated value up to a password manager before editing `.env` again (`.env.example:13-21`). See the rotation/restore procedure in [architecture.md](architecture.md).

---

## Job-site accounts

The shared job-site password saved in the app is encrypted with `CREDENTIAL_KEY`
on the profile row. `JOB_SITE_PASSWORD` is an optional environment fallback;
the app value takes precedence. Clearing the app value restores the environment
fallback. If both are empty, email/password sign-up is disabled; Google sign-in
remains available. The password endpoint accepts 8–128 characters, or an empty
string to clear it, and returns only whether a password is set and its source.

Settings → Sites → **Job-site accounts** lists accounts recorded by the harness,
including their sign-in method, email, status, creation time, last use, and login
link. The registry stores encrypted email, with the shared password kept only on
the profile. Existing legacy credentials stay unchanged and are excluded from
the registry. Deleting a registry entry removes JobPilot's record, not the
account on the external site or its browser session.

The daily limit counts registry entries created during the computer's current
local calendar day, including failed and signed-in entries as a conservative
quota measure. Updating an older entry does not count it again. Local midnight
boundaries account for daylight-saving changes; stored timestamps remain UTC.

---

## App & server settings (`JOBPILOT_*`)

| Env var | Field | Default | Notes |
|---|---|---|---|
| `JOBPILOT_HOST` | `jobpilot_host` | `127.0.0.1` | Bind host |
| `JOBPILOT_PORT` | `jobpilot_port` | `8000` | Bind port |
| `JOBPILOT_LOG_LEVEL` | `jobpilot_log_level` | `info` | Log level |
| `JOBPILOT_SCRAPER_HEADLESS` | `jobpilot_scraper_headless` | `true` | Run the browser scraper headless |
| `JOBPILOT_APPLY_HEADLESS` | `jobpilot_apply_headless` | `false` | Tier-2 apply uses a *visible* browser by default so you can watch and intervene on logins and captchas. Set true for headless/server/Docker runs with no display. The apply browser always launches with container-safe Chromium flags (`--no-sandbox` / `--disable-dev-shm-usage`), so headless works in Docker |
| `JOBPILOT_AUTO_SUBMIT_ENABLED` | `jobpilot_auto_submit_enabled` | `false` | Allows the `auto` apply method (JobPilot clicks the final submit). Off by default; enabling it still requires owner review and destination verification. With it off, `POST /api/applications/{id}/apply` rejects `auto` with 409 and the UI hides Auto. Assisted and manual apply are unaffected. |
| `JOBPILOT_ALLOWED_HOSTS` | `jobpilot_allowed_hosts` | `""` | Extra `Host` header names (comma-separated) accepted besides loopback and the hosts of `JOBPILOT_ALLOWED_ORIGINS`. Only needed behind a local reverse proxy; any other Host gets 403 (DNS-rebinding defence). |
| `JOBPILOT_DATA_DIR` | `jobpilot_data_dir` | `./data` | Resolved to absolute path under project root if relative (`config.py:222`) |
| `JOBPILOT_ALLOWED_ORIGINS` | `jobpilot_allowed_origins` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000` | Comma-separated CORS origins |

`config.py:33-41`. `JOBPILOT_ALLOWED_ORIGINS` is split on commas and trimmed into the CORS allow-list at `backend/main.py:292`. Set it if you serve the frontend behind a reverse proxy or a non-default host.

---

## Models

Model names are configured via `LLM_MODEL` for API generation, `LLM_CLI_MODEL` for CLI generation, and `BROWSER_LLM_MODEL` for the browser agent. Each is optional; when empty the provider adapter uses its default.

---

## Feature flags

| Env var | Field | Default | Effect |
|---|---|---|---|
| `SCRAPLING_ENABLED` | `SCRAPLING_ENABLED` | `true` | Enables the Tier 1 Scrapling fetcher (HTTP + single LLM call). When false, no `ScraplingFetcher` is constructed (`backend/main.py:153`) |
| `APPLY_TIER1_ENABLED` | `APPLY_TIER1_ENABLED` | `true` | Enables the Tier 1 Playwright direct filler (mirrors `SCRAPLING_ENABLED`) |

`config.py:66-69`.

---

## Discovery monitor

| Env var | Field | Default | Effect |
|---|---|---|---|
| `MONITOR_ENABLED` | `MONITOR_ENABLED` | `false` | Schedules the monitor tick. Off: nothing polls |
| `MONITOR_TICK_SECONDS` | `MONITOR_TICK_SECONDS` | `30` | Seconds between ticks; each tick polls the sources that are due |
| `MONITOR_MAX_CONCURRENCY` | `MONITOR_MAX_CONCURRENCY` | `8` | Sources polled at the same time within a tick |
| `APPLY_MIN_GAP_SECONDS` | `90` | Minimum pause between two automatic applications on the same site; the real pause is 1 to 2.5 times this. `0` disables it |
| `APPLY_TIER2_MAX_STEPS` | `25` | Step cap for one run of the browser agent; past it the run stops without submitting |
| `APPLY_TIER2_TIMEOUT_SECONDS` | `300` | Time cap for one run of the browser agent |
| `NTFY_URL` | `NTFY_URL` | empty | ntfy topic URL (`https://ntfy.sh/<topic>` or a self-hosted server) for new-match and source-disabled notifications. Empty: no outbound notification; new matches still reach the web app over the WebSocket. The topic name is the only protection: anyone who knows it can read the pushes |

Applied on restart. Per-source intervals are on the `job_sources` rows, not in `.env` (set with `interval_s` when a source is added). See [Getting started with the monitor](getting-started-monitor.md) for the owner's walkthrough and [Job scraping & sources](scraping.md#the-discovery-monitor) for the internals.

These monitor limits are fixed in code, not settings: LinkedIn job pages opened in the JobPilot Chrome are capped at 5 per poll and 80 per day, with 6–18 s between pages (`backend/monitor/linkedin_detail.py`); a failing source waits at most 6 h before its next try (`backend/monitor/poller.py`).

---

## Files in the data directory

Some behaviour is set by files in the data directory (`JOBPILOT_DATA_DIR`, default `./data`) rather than by `.env`:

| File | Effect |
|---|---|
| `my_skills.txt` | Your skills, one per line (`name \| alias \| alias`). When it lists at least one skill, jobs are scored against it instead of the skills read from the CV. Read and replace it with `GET` / `PUT /api/settings/skills`. Example: `scripts/defaults/my_skills.example.txt`. See [Matching](matching.md#your-own-skill-files-data-directory) |
| `skills.txt` | Optional. Replaces the bundled skill vocabulary (`backend/matching/data/skills.txt`) |
| `jobpilot-chrome/` | The JobPilot Chrome's own profile, where your manual job-site logins are kept. Created by `POST /api/browser/open`. Never your everyday Chrome profile. Requires Google Chrome (`google-chrome` on the PATH) |
| `.session_secret` | Per-installation key. Every POST/PUT/PATCH/DELETE under `/api` must send it, as the `jobpilot_session` cookie (the web app does this) or the `X-JobPilot-Session` header (for `curl` and scripts). Created on first start |

---

## Timeouts (seconds)

| Env var | Field | Default | Notes |
|---|---|---|---|
| `TECTONIC_TIMEOUT_SECONDS` | `TECTONIC_TIMEOUT_SECONDS` | `60.0` | LaTeX (Tectonic) compile timeout — fail loudly instead of hanging |
| `LLM_TIMEOUT_SECONDS` | `LLM_TIMEOUT_SECONDS` | `180.0` | LLM request timeout. Defaults to 180 because local reasoning models can take 100s+ on a single CV-tailoring call |

`config.py:73-74`.

---

## Gmail integration

All optional — leave empty to disable. Obtain client credentials at `console.cloud.google.com` (`.env.example:46-52`).

| Env var | Field | Default | Notes |
|---|---|---|---|
| `GMAIL_CLIENT_ID` | `GMAIL_CLIENT_ID` | `""` | OAuth client id |
| `GMAIL_CLIENT_SECRET` | `GMAIL_CLIENT_SECRET` (secret) | `""` | OAuth client secret |
| `GMAIL_REDIRECT_URI` | `GMAIL_REDIRECT_URI` | `http://localhost:8000/api/gmail/oauth/callback` | Must match the registered redirect URI |
| `GMAIL_BACKFILL_DAYS` | `GMAIL_BACKFILL_DAYS` | `30` | Days of history to backfill on first connect |
| `GMAIL_POLL_INTERVAL_MINUTES` | `GMAIL_POLL_INTERVAL_MINUTES` | `5` | Polling cadence for new mail |

`config.py:77-81`.

---

## Startup validation rules (`validate_runtime_config`)

`Settings.validate_runtime_config()` returns a list of human-readable generation-provider problems; the lifespan **refuses to start** if non-empty. `auto` with no available writer is allowed and logs a warning. `Settings.validate_browser_llm_config()` checks the browser role separately; its problems warn and disable Tier 2.

The credential helper `is_configured()` (`backend/config.py:83`) treats `""` and the literal `"placeholder"` as not-configured, and transparently unwraps `SecretStr`.

### Generation (`LLM_PROVIDER`)

| Provider | Requirement |
|---|---|
| invalid value | Must be one of `auto` \| `claude` \| `codex` \| `openai` \| `anthropic` |
| `auto` | No requirement; missing writing model is a warning |
| `claude` / `codex` | Selected executable must be on PATH; log in before generation |
| `openai` | `LLM_BASE_URL` (local/self-hosted) **or** `LLM_API_KEY` **or** `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` (or `LLM_API_KEY`) |

### Browser agent (`BROWSER_LLM_PROVIDER`)

| Provider | Requirement |
|---|---|
| invalid value | Must be `openai`, or `anthropic` with an OpenAI-compatible base URL |
| `openai` | `BROWSER_LLM_BASE_URL` **or** `BROWSER_LLM_API_KEY` **or** a provider API key |
| `anthropic` | `BROWSER_LLM_BASE_URL` is required; browser-use uses the OpenAI-compatible endpoint |

For `openai`-type roles, a configured `*_BASE_URL` (a local/self-hosted server, where the key is usually ignored) satisfies the requirement on its own; otherwise a hosted-OpenAI key is required (`_openai_compatible_ok`, `backend/config.py:117`).

On a generation configuration error the lifespan raises `RuntimeError` pointing you at the setup scripts (`scripts/setup.sh` / `scripts/setup.ps1`) or `.env.example`. Browser configuration errors never prevent startup.

---

## Related

- [Architecture](architecture.md) — system overview, credentials & encryption, key rotation/restore, deployment
- [Config & Database module](modules/config-database.md) — settings, DB engine, app entry point
- [LLM module](modules/llm.md) — provider clients and the factory
- [User guide](user-guide.md) — onboarding and day-to-day use
- [API reference](api-reference.md) — REST + WebSocket endpoints
- [README](../README.md) — project overview and quick start
