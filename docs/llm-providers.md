# Multi-provider LLM

> JobPilot talks to LLMs through a provider-agnostic layer: three independent roles (generation, embeddings, browser agent) each pick a provider via env, and **no consumer hard-binds to a specific provider**.

## Overview

Every LLM call in JobPilot goes through one of three factory functions in
[`backend/llm/factory.py`](../backend/llm/factory.py). Each factory reads a
`*_PROVIDER` setting, imports the matching adapter lazily, and returns an object
satisfying a small `Protocol`. Consumers depend only on the Protocol — they
never import a concrete provider class — so switching providers is a pure
config change.

```
            ┌─────────────────── role: generation ───────────────────┐
LLM_PROVIDER ─► make_llm_client() ─► LLMClient  (OpenAICompatClient | AnthropicClient)
                                       │
                                       ├─ CVEditor, CVModifier, JobAnalyzer
                                       ├─ ScraplingFetcher
                                       └─ PlaywrightFormFiller (auto_apply / assisted_apply)

            ┌─────────────────── role: embeddings ───────────────────┐
EMBEDDING_PROVIDER ─► make_embedding_client() ─► EmbeddingClient (OpenAICompatEmbeddingClient)
                                                   └─ Embedder ─► cosine_similarity()

            ┌─────────────────── role: browser agent ────────────────┐
BROWSER_LLM_PROVIDER ─► make_browser_llm() ─► browser_use Chat* (ChatOpenAI)
                                                ├─ AdaptiveScraper
                                                └─ auto_apply / assisted_apply browser-use fallback
```

## The contracts: `base.py`

[`backend/llm/base.py`](../backend/llm/base.py) defines two runtime-checkable
Protocols and the **neutral exception types** that every adapter raises (so
callers catch one set of errors regardless of provider).

### `LLMClient` Protocol (`base.py:33`)

```python
async def generate_text(self, prompt, *, response_mime_type=None, response_schema=None) -> str
async def generate_json(self, prompt, schema: Type[T]) -> T
```

`generate_text` is the primitive; `generate_json` builds on it (validating
against a Pydantic model). `response_mime_type="application/json"` is the
cross-provider signal that JSON output is wanted — each adapter implements that
however its provider allows (see the adapter table below).

### `EmbeddingClient` Protocol (`base.py:48`)

```python
@property model_id -> str
@property dimension -> int
async def embed(self, texts: list[str]) -> list[list[float]]
```

`dimension` lets the rest of the system reason about vector size without
knowing the model — it underpins the dimension guard (below).

### Neutral exceptions (`base.py:11-20`)

| Exception | Meaning |
|---|---|
| `LLMRateLimitError` | Provider returned a 429 / rate-limit error |
| `LLMCallFailed` | Non-rate-limit failure (bad key, network, 5xx) |
| `LLMJSONError` | Returned text could not be parsed as the expected JSON |

`parse_json_response` (`base.py:23`) is a shared helper that strips ```` ```json ````
fences before `json.loads`, used by every adapter.

## The factory: `factory.py`

All three functions default to `"openai"` when the env var is unset and raise a
descriptive `ValueError` on an unknown value. Imports are deferred inside each
branch so an unused provider's SDK never needs to be installed.

### `make_llm_client()` — generation (`factory.py:10`)

Switches on `LLM_PROVIDER` → `openai` / `anthropic`, returning
`OpenAICompatClient` or `AnthropicClient` respectively.

### `make_embedding_client()` — embeddings (`factory.py:22`)

Switches on `EMBEDDING_PROVIDER` → `openai`. **`anthropic` is
explicitly rejected** with `"EMBEDDING_PROVIDER=anthropic invalid: Anthropic has
no embeddings API"`.

### `make_browser_llm()` — browser agent (`factory.py:34`)

Returns a [browser-use](https://github.com/browser-use/browser-use) `Chat*`
instance, not an `LLMClient` — the browser agent runs its own loop. For
`BROWSER_LLM_PROVIDER` = `openai` / `anthropic` it returns `ChatOpenAI(...)`.
**browser-use ships no `ChatAnthropic`**, so `anthropic` is only usable through an
OpenAI-compatible endpoint and *requires* `BROWSER_LLM_BASE_URL`; without it
the factory raises a clear `ValueError`. The API key is
resolved from `BROWSER_LLM_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY`,
model defaults to `gpt-4o`.

## The adapters

### `OpenAICompatClient` / `OpenAICompatEmbeddingClient` (`backend/llm/providers/openai_compat.py:29` / `:75`)

Adapter for **any** OpenAI-compatible endpoint — hosted OpenAI, DeepSeek, or a
local server (Ollama, LM Studio, vLLM) via `LLM_BASE_URL` / `EMBEDDING_BASE_URL`.

- Generation default model `gpt-4o-mini`; JSON via `response_format={"type": "json_object"}`.
- Key resolution: `LLM_API_KEY` → `OPENAI_API_KEY`, falling back to `"not-needed"`
  for keyless local servers (`openai_compat.py:22-26`, `:36`).
- Embeddings default model `text-embedding-3-small`; dimension looked up from
  `_EMBED_DIMS` (`text-embedding-3-small`→1536, `text-embedding-3-large`→3072,
  else 1536). `model_id = "openai:<model>"`.

> **Local reasoning models** (Qwen3, DeepSeek-R1): set `LLM_DISABLE_THINKING=true`
> to skip chain-of-thought — typically a large latency win. `LLM_TIMEOUT_SECONDS`
> defaults to `180` to accommodate the latency of these models (a single
> CV-tailoring call can take 100s+).

### `AnthropicClient` (`backend/llm/providers/anthropic_client.py:21`)

Generation-only adapter over the Anthropic Messages API.

- Default model `claude-haiku-4-5-20251001`, `max_tokens = 4096`.
- Key resolution: `LLM_API_KEY` → `ANTHROPIC_API_KEY`.
- **No JSON-mode flag exists** on Anthropic, so JSON requests are steered with a
  system instruction (`"Respond with only a single valid JSON object…"`) and
  parsed downstream; unlike the other two adapters there is **no prose-retry**
  on parse failure — it raises `LLMJSONError` immediately (`anthropic_client.py:59-66`).
- Implements `LLMClient` only — **no `embed`**.

## Provider × role support matrix

| Provider | Generation (`LLM_PROVIDER`) | Embeddings (`EMBEDDING_PROVIDER`) | Browser agent (`BROWSER_LLM_PROVIDER`) |
|---|:---:|:---:|:---:|
| **openai** (+ compatible/local) | ✅ `OpenAICompatClient` | ✅ `OpenAICompatEmbeddingClient` | ✅ `ChatOpenAI` |
| **anthropic** | ✅ `AnthropicClient` | ❌ no embeddings API (factory raises) | ⚠️ only via OpenAI-compatible endpoint — requires `BROWSER_LLM_BASE_URL`; browser-use has no `ChatAnthropic` |

> Any other vendor exposing an OpenAI-compatible endpoint is reached through the `openai` provider by setting `*_BASE_URL` — it needs no dedicated adapter.

## The consumers (none hard-bind to a provider)

Each consumer accepts an injected client (defaulting to the factory) or calls a
factory directly — none imports a concrete provider class.

| Consumer | Role | Wiring |
|---|---|---|
| `CVEditor` | generation | `client or make_llm_client()` — `cv_editor.py:35` |
| `CVModifier` | generation | `client or make_llm_client()` — `cv_modifier.py:43` |
| `JobAnalyzer` | generation | `client or make_llm_client()` — `job_analyzer.py:18` |
| `ScraplingFetcher` | generation | `__init__(llm_client: LLMClient)`, wired with `gen_client` in `main.py:153` |
| `PlaywrightFormFiller` | generation | `llm_client=make_llm_client()` — `auto_apply.py:69`, `assisted_apply.py:64` |
| `Embedder` | embeddings | `embedding_client=make_embedding_client()` — `main.py:175`, type `embedder.py:14` |
| `AutoApply` (browser fallback) | browser | `make_browser_llm()` — `auto_apply.py:316`, `:426` |
| `AssistedApply` (browser fallback) | browser | `make_browser_llm()` — `assisted_apply.py:180` |
| `AdaptiveScraper` | browser | `_make_llm()` → `make_browser_llm()` — `adaptive_scraper.py:37` |

`backend/main.py` builds one shared `gen_client = make_llm_client()` (`main.py:141`)
and threads it into `CVEditor`, `JobAnalyzer`, `CVModifier`, and `ScraplingFetcher`,
while the embedder gets its own `make_embedding_client()`. (Note: the
`gen_client` is still stored as `app.state.llm` for historical reasons, but
it may be any provider.)

## Startup validation: `Settings.validate_runtime_config`

[`backend/config.py:105`](../backend/config.py) returns a list of human-readable
problems (empty == ready). It is **provider-aware**: it validates only the
credentials the *chosen* provider for each role needs, so a fully-local OpenAI
setup is never asked for a hosted key. The app lifespan calls it fail-fast and
aborts startup if it returns anything (`main.py:99-108`).

Checks per role:

- **Generation** — must be `openai|anthropic`. `openai` needs `LLM_BASE_URL`
  (local) **or** `LLM_API_KEY`/`OPENAI_API_KEY` (hosted); `anthropic` needs
  `ANTHROPIC_API_KEY` (or `LLM_API_KEY`).
- **Embeddings** — must be `openai` (anthropic rejected with the
  no-embeddings message). Same key logic as generation for its `EMBEDDING_*`
  vars.
- **Browser** — must be `openai`; needs `BROWSER_LLM_BASE_URL`
  or an OpenAI key.

The `_openai_compatible_ok` helper (`config.py:117`) encodes the rule that a
configured `*_BASE_URL` (a self-hosted server) satisfies the credential check
even without an API key.

## Embedding dimension guard

Because providers emit different vector sizes (e.g. OpenAI 1536 vs 3072, or a
local model's own dimension), mixing providers — or switching providers against an existing vector store —
could feed mismatched vectors into similarity math. The guard lives in
`cosine_similarity` (`backend/matching/fit_engine.py:67`):

```python
def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    ...
```

A length mismatch (or empty vector) returns `0.0` instead of crashing, so a
dimension change degrades a match score rather than raising. This is covered by
`tests/test_embedding_wiring.py` (a 768-vs-1536 mismatch returns `0.0`). The
`EmbeddingClient.dimension` property exposes the active model's size for any
caller that wants to validate up front.

## Related

- [Configuration](configuration.md) — all `*_PROVIDER`, `*_MODEL`, `*_API_KEY`, `*_BASE_URL` env vars
- [Architecture](architecture.md) — how the LLM layer fits the overall system
- [API reference](api-reference.md)
- [File map](file-map.md) — `backend/llm/` layout
- [Deployment](deployment.md) — running with hosted vs local providers
- [Project README](../README.md)
