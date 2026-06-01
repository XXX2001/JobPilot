# Multi-Provider LLM Abstraction — Design

**Date:** 2026-06-01
**Status:** Approved (pending implementation plan)

## 1. Goal & scope

Replace the hard-wired Gemini dependency with a **provider-agnostic abstraction**.
Each of three LLM touchpoints selects **one** provider via `.env`, applied on restart
(no runtime UI switching, no failover, no round-robin, no per-task routing — see YAGNI):

| Touchpoint | Methods | Config key |
|---|---|---|
| Generation | `generate_text`, `generate_json` | `LLM_PROVIDER` |
| Embeddings | `embed` | `EMBEDDING_PROVIDER` |
| Browser agent (auto/assisted apply) | browser-use `Chat*` | `BROWSER_LLM_PROVIDER` |

**Provider families:**
- **OpenAI-compatible** — OpenAI, DeepSeek, Groq, Together, **and any local runtime**
  (Ollama / LM Studio / vLLM / llama.cpp) via a configurable `base_url`.
- **Anthropic** — native `anthropic` SDK.
- **Gemini** — existing `google.genai` integration.

## 2. Architecture — Protocols + native adapters + factory

### Protocols (`backend/llm/base.py`)
- `LLMClient` (Protocol): `generate_text(prompt, *, response_mime_type=None, response_schema=None) -> str`,
  `generate_json(prompt, schema) -> T`.
- `EmbeddingClient` (Protocol): `embed(texts) -> list[list[float]]`, plus a `model_id: str`
  property (e.g. `"gemini:text-embedding-004"`) and `dimension: int`.

### Adapters (`backend/llm/providers/`)
- `gemini.py` — the existing `GeminiClient`, refactored to conform to the Protocols.
  Minimal change; **keeps** the tuned 15-RPM sliding-window limiter, 429 retry with
  `Retry-After` parsing, and the model-fallback chain.
- `openai_compat.py` — `OpenAICompatClient` built on the `openai` SDK with configurable
  `base_url`. Covers OpenAI + DeepSeek + all local runtimes. `generate_json` uses OpenAI
  JSON mode (`response_format`). Its own rate-limit/retry handling translating provider
  429s to the neutral exception.
- `anthropic.py` — `AnthropicClient` built on the `anthropic` SDK. `generate_json` via
  tool-use (preferred) or prompted JSON + parse, mirroring the existing JSON fallback.

### Factory (`backend/llm/factory.py`)
- `make_llm_client() -> LLMClient` — reads `LLM_PROVIDER`, returns the matching adapter.
- `make_embedding_client() -> EmbeddingClient` — reads `EMBEDDING_PROVIDER`.
- `make_browser_llm()` — reads `BROWSER_LLM_PROVIDER`, returns a browser-use `Chat*`
  instance: `openai` → `ChatOpenAI(base_url=…)`, `gemini` → `ChatGoogle`.
- Unknown provider value → clear, actionable error. Defaults reproduce today's Gemini setup.

### Call-site changes
The existing **injectable-client DI pattern is preserved**: `cv_editor`, `job_analyzer`,
`cv_modifier`, `Embedder`, and both appliers already accept an injected client defaulting
to `GeminiClient()`. The blast radius is:
- those constructors' defaults → call the factory instead of `GeminiClient()`,
- `main.py` health-warmup,
- the two appliers' direct `ChatGoogle` import → `make_browser_llm()`.

## 3. Configuration (`backend/config.py`, `.env.example`)

New settings, each defaulting to **today's Gemini behavior** so existing installs are
unaffected:

```
LLM_PROVIDER=gemini            # gemini | openai | anthropic
LLM_MODEL=                     # provider-specific; per-provider default if empty
LLM_BASE_URL=                  # openai-compatible/local, e.g. http://localhost:11434/v1
LLM_API_KEY=                   # generic; if unset, provider-specific key is used

EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=text-embedding-004
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=

BROWSER_LLM_PROVIDER=gemini
BROWSER_LLM_MODEL=
BROWSER_LLM_BASE_URL=
BROWSER_LLM_API_KEY=
```

Existing `GOOGLE_API_KEY` / `GOOGLE_MODEL` / `GOOGLE_MODEL_FALLBACKS` remain as the
Gemini-provider credentials/model config. Optional per-provider secrets added:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (DeepSeek and locals use `LLM_BASE_URL` +
`LLM_API_KEY`). `.env.example` documents all of the above with examples (incl. an Ollama
local example).

## 4. Error handling — unified exception surface

Today consumers catch `GeminiRateLimitError` / `GeminiJSONError` / `GeminiCallFailed`
(FastAPI handlers in `main.py`, `latex/pipeline.py`, etc.). Introduce provider-neutral
exceptions in `base.py`: `LLMRateLimitError`, `LLMJSONError`, `LLMCallFailed`. Keep the
`Gemini*` names as **aliases** (`GeminiRateLimitError = LLMRateLimitError`, …) for
backward compatibility. Every adapter raises the neutral types; existing handlers and
tests keep working unchanged.

## 5. Embeddings & auto re-embed on mismatch

- Each cached embedding is tagged with the `model_id` that produced it
  (e.g. `gemini:text-embedding-004`).
- On startup (and before a fit run), if the configured embedding `model_id` differs from
  the stored one, **invalidate the cached embeddings** (clear them) so the existing
  `if not skill.embedding` path recomputes them with the new provider — i.e. re-embed
  lazily on the next batch run, plus a background warm-up kick.
- Profiles are already largely re-embedded per run, so the invalidation risk is low.
- `cosine_similarity` already guards zero/empty vectors; the invalidate step prevents
  mixed-dimension comparisons (Gemini 768 vs OpenAI 1536, etc.).

> The exact persistence location of cached skill embeddings (currently held on
> `CVProfile`/`JobProfile` skill objects, populated via the `if not skill.embedding`
> cache path) will be pinned down in the implementation plan; the design requires only
> that whatever store holds them also records the `model_id`, and that a mismatch clears
> them.

## 6. Browser agent

`make_browser_llm()` maps provider → browser-use class: `openai` → `ChatOpenAI(base_url=…)`,
`gemini` → `ChatGoogle`. The two appliers (`auto_apply.py`, `assisted_apply.py`) stop
importing `ChatGoogle` directly and call the factory.

**Known limitation (documented, not solved):** browser-use 0.2 in this project ships
`ChatOpenAI`, `ChatGoogle`, `ChatAzureOpenAI`, `ChatCerebras`, `ChatMistral` — but **no
`ChatAnthropic`**. Selecting Anthropic for the **browser agent** therefore either routes
through an OpenAI-compatible endpoint (`base_url`) or raises a clear "not supported by
browser-use for the browser agent" error. Generation and embeddings are unaffected —
Anthropic works fully there.

## 7. Testing

- **Per-adapter unit tests** with a fake transport (mock the `openai` / `anthropic` /
  `genai` client): assert prompt mapping, JSON-mode handling, and error → neutral-exception
  translation. For Gemini, keep rate-limit behavior coverage.
- **Factory tests:** each `*_PROVIDER` value yields the correct adapter; unknown value →
  clear error; empty/default config reproduces today's Gemini setup.
- **Embedding mismatch test:** changing `model_id` invalidates cached vectors.
- Existing `tests/test_gemini_client.py` stays green via the exception aliases and the
  refactored-but-compatible `GeminiClient`.

## 8. YAGNI — deliberately out of scope

- Runtime UI switching of providers (stays `.env` + restart).
- Cross-provider failover, round-robin / load-balancing, per-task routing.
- Storing provider secrets in the DB (secrets remain in `.env`).
- Solving Anthropic support for the browser agent (documented limitation only).

## 9. New dependencies

- `openai` (OpenAI-compatible adapter + browser-use `ChatOpenAI`).
- `anthropic` (Anthropic generation adapter).
