# Configuration reference

Every JobPilot setting, where it lives, whether it is required, and the startup validation rules that decide which credentials you actually need.

All settings are defined as fields on the `Settings` class in `backend/config.py:11` and loaded from environment variables or a `.env` file (`backend/config.py:14`, `env_file=".env"`, `case_sensitive=False`). A template lives at `.env.example`. Because matching is case-insensitive, the lowercase field `jobpilot_host` reads the env var `JOBPILOT_HOST`, and so on.

Almost nothing is *required at the type level* — every credential field defaults to an empty string so the app can boot with a local model and no cloud keys. What each *configured* provider needs is enforced at startup by `Settings.validate_runtime_config()` (`backend/config.py:105`), called from the FastAPI lifespan (`backend/main.py:99`).

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

JobPilot has three independent LLM roles — generation, embeddings, and the browser agent — each with its own provider/model/base-URL/key. You can mix providers (e.g. local generation + Gemini embeddings).

### Generation (`LLM_*`)

| Env var | Field | Default | Required? |
|---|---|---|---|
| `LLM_PROVIDER` | `LLM_PROVIDER` | `gemini` | One of `gemini` \| `openai` \| `anthropic` |
| `LLM_MODEL` | `LLM_MODEL` | `""` (provider default) | Optional |
| `LLM_BASE_URL` | `LLM_BASE_URL` | `""` | For OpenAI-compatible / local servers, e.g. `http://localhost:11434/v1` |
| `LLM_API_KEY` | `LLM_API_KEY` (secret) | `""` | See validation rules |

`config.py:49-52`. Provider-default and base-URL details are wired through `backend/llm/factory.py` (`make_llm_client`, used at `backend/main.py:141`).

### Embeddings (`EMBEDDING_*`)

| Env var | Field | Default | Required? |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | `EMBEDDING_PROVIDER` | `gemini` | One of `gemini` \| `openai` (anthropic has no embeddings API) |
| `EMBEDDING_MODEL` | `EMBEDDING_MODEL` | `text-embedding-004` | Optional |
| `EMBEDDING_BASE_URL` | `EMBEDDING_BASE_URL` | `""` | OpenAI-compatible endpoint |
| `EMBEDDING_API_KEY` | `EMBEDDING_API_KEY` (secret) | `""` | See validation rules |

`config.py:54-57`. Note: many local builds (e.g. llama.cpp Qwen) do **not** serve `/v1/embeddings` (HTTP 501). Keep embeddings on a backend that does — Gemini, or an OpenAI endpoint with an embedding model — or fit-scoring breaks (`.env.example:69-72`).

### Browser agent (`BROWSER_LLM_*`)

| Env var | Field | Default | Required? |
|---|---|---|---|
| `BROWSER_LLM_PROVIDER` | `BROWSER_LLM_PROVIDER` | `gemini` | One of `gemini` \| `openai` (anthropic only via OpenAI-compatible `base_url`) |
| `BROWSER_LLM_MODEL` | `BROWSER_LLM_MODEL` | `""` | Optional |
| `BROWSER_LLM_BASE_URL` | `BROWSER_LLM_BASE_URL` | `""` | OpenAI-compatible endpoint |
| `BROWSER_LLM_API_KEY` | `BROWSER_LLM_API_KEY` (secret) | `""` | See validation rules |

`config.py:58-62`.

> Changes to any `LLM_*` / `EMBEDDING_*` / `BROWSER_LLM_*` value are **applied on restart** — clients are constructed once in the lifespan, not per-request.

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
| `GOOGLE_API_KEY` | `GOOGLE_API_KEY` (secret) | `""` | Required only if any role uses `gemini` |
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

## App & server settings (`JOBPILOT_*`)

| Env var | Field | Default | Notes |
|---|---|---|---|
| `JOBPILOT_HOST` | `jobpilot_host` | `127.0.0.1` | Bind host |
| `JOBPILOT_PORT` | `jobpilot_port` | `8000` | Bind port |
| `JOBPILOT_LOG_LEVEL` | `jobpilot_log_level` | `info` | Log level |
| `JOBPILOT_SCRAPER_HEADLESS` | `jobpilot_scraper_headless` | `true` | Run the browser scraper headless |
| `JOBPILOT_DATA_DIR` | `jobpilot_data_dir` | `./data` | Resolved to absolute path under project root if relative (`config.py:222`) |
| `JOBPILOT_ALLOWED_ORIGINS` | `jobpilot_allowed_origins` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000` | Comma-separated CORS origins |

`config.py:33-41`. `JOBPILOT_ALLOWED_ORIGINS` is split on commas and trimmed into the CORS allow-list at `backend/main.py:292`. Set it if you serve the frontend behind a reverse proxy or a non-default host.

---

## Models

| Env var | Field | Default | Notes |
|---|---|---|---|
| `GOOGLE_MODEL` | `GOOGLE_MODEL` | `gemini-3-flash-preview` | Primary Gemini model name |
| `GOOGLE_MODEL_FALLBACKS` | `GOOGLE_MODEL_FALLBACKS` | `""` | Comma-separated fallback model names; empty means no fallbacks |

`config.py:42-46`. Role-specific overrides are `LLM_MODEL` / `EMBEDDING_MODEL` / `BROWSER_LLM_MODEL` (above).

---

## Feature flags

| Env var | Field | Default | Effect |
|---|---|---|---|
| `SCRAPLING_ENABLED` | `SCRAPLING_ENABLED` | `true` | Enables the Tier 1 Scrapling fetcher (HTTP + single LLM call). When false, no `ScraplingFetcher` is constructed (`backend/main.py:153`) |
| `APPLY_TIER1_ENABLED` | `APPLY_TIER1_ENABLED` | `true` | Enables the Tier 1 Playwright direct filler (mirrors `SCRAPLING_ENABLED`) |

`config.py:66-69`.

---

## Timeouts (seconds)

| Env var | Field | Default | Notes |
|---|---|---|---|
| `TECTONIC_TIMEOUT_SECONDS` | `TECTONIC_TIMEOUT_SECONDS` | `60.0` | LaTeX (Tectonic) compile timeout — fail loudly instead of hanging |
| `GEMINI_TIMEOUT_SECONDS` | `GEMINI_TIMEOUT_SECONDS` | `45.0` | Gemini request timeout |

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

`Settings.validate_runtime_config()` (`backend/config.py:105`) returns a list of human-readable problems; an empty list means ready to run. The lifespan calls it and **refuses to start** if non-empty (`backend/main.py:99-108`). Each role independently checks only the credentials *its* provider needs — so a local-model user is never asked for a Google key.

The credential helper `is_configured()` (`backend/config.py:83`) treats `""` and the literal `"placeholder"` as not-configured, and transparently unwraps `SecretStr`.

### Generation (`LLM_PROVIDER`)

| Provider | Requirement |
|---|---|
| invalid value | Must be one of `gemini` \| `openai` \| `anthropic` |
| `gemini` | `GOOGLE_API_KEY` must be set |
| `openai` | `LLM_BASE_URL` (local/self-hosted) **or** `LLM_API_KEY` **or** `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` (or `LLM_API_KEY`) |

### Embeddings (`EMBEDDING_PROVIDER`)

| Provider | Requirement |
|---|---|
| invalid value | Must be `gemini` \| `openai` (anthropic has no embeddings API) |
| `gemini` | `GOOGLE_API_KEY` must be set |
| `openai` | `EMBEDDING_BASE_URL` **or** `EMBEDDING_API_KEY` **or** `OPENAI_API_KEY` |

### Browser agent (`BROWSER_LLM_PROVIDER`)

| Provider | Requirement |
|---|---|
| invalid value | Must be `gemini` \| `openai` |
| `gemini` | `GOOGLE_API_KEY` must be set |
| `openai` | `BROWSER_LLM_BASE_URL` **or** `BROWSER_LLM_API_KEY` **or** `OPENAI_API_KEY` |

For `openai`-type roles, a configured `*_BASE_URL` (a local/self-hosted server, where the key is usually ignored) satisfies the requirement on its own; otherwise a hosted-OpenAI key is required (`_openai_compatible_ok`, `backend/config.py:117`).

On failure the lifespan raises `RuntimeError` pointing you at the setup scripts (`scripts/setup.sh` / `scripts/setup.ps1`) or `.env.example`.

---

## Related

- [Architecture](architecture.md) — system overview, credentials & encryption, key rotation/restore, deployment
- [Config & Database module](modules/config-database.md) — settings, DB engine, app entry point
- [LLM module](modules/llm.md) — provider clients and the factory
- [User guide](user-guide.md) — onboarding and day-to-day use
- [API reference](api-reference.md) — REST + WebSocket endpoints
- [README](../README.md) — project overview and quick start
