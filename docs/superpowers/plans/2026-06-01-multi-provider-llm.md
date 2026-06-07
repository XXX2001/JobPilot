# Multi-Provider LLM Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generation, embeddings, and the browser agent each select one LLM provider (OpenAI-compatible / Anthropic / Gemini, incl. DeepSeek + local) via `.env`, applied on restart.

**Architecture:** Two `typing.Protocol`s (`LLMClient`, `EmbeddingClient`) with provider-neutral exceptions in a new `backend/llm/base.py`. Native adapters per family, selected by a factory that reads config. The existing injectable-client DI pattern is preserved; the existing `GeminiClient` stays where it is and is made to conform (minimal blast radius).

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, `openai` SDK, `anthropic` SDK, `google.genai`, browser-use 0.2, pytest + pytest-asyncio.

---

## Notes & deviations from the spec (read first)

1. **Gemini adapter stays in `backend/llm/gemini_client.py`** (not moved to `backend/llm/providers/`). Moving it would touch ~10 import sites for no behavioral gain; the spec's intent was "minimal change," which keeping it in place better serves. New adapters go in `backend/llm/providers/`.
2. **Embeddings are transient.** Verified during planning: `CVProfile`/`JobProfile` skill vectors are computed fresh each batch run and never persisted to DB/disk (`fit_assessment_json` stores the assessment, not vectors). So a provider switch "auto re-embeds" for free on the next run — no migration/invalidation infrastructure is needed. The only residual hazard is a dimension mismatch *within* one run; Task 9 adds a length guard in `cosine_similarity`. Each `EmbeddingClient` still exposes `model_id`/`dimension` for observability and that guard.
3. **Anthropic has no embeddings API and browser-use 0.2 ships no `ChatAnthropic`.** The factory raises a clear error for `EMBEDDING_PROVIDER=anthropic`, and for `BROWSER_LLM_PROVIDER=anthropic` it requires an OpenAI-compatible `base_url` (else a clear error).
4. **SDK signatures:** The async signatures used below (`AsyncOpenAI().chat.completions.create`, `AsyncAnthropic().messages.create`, `client.embeddings.create`) are stable. If anything mismatches the installed version, confirm with the `get-api-docs` skill before adjusting.

## File structure

| File | Responsibility |
|---|---|
| `backend/llm/base.py` (new) | `LLMClient` + `EmbeddingClient` Protocols; neutral exceptions `LLMRateLimitError`/`LLMJSONError`/`LLMCallFailed`; shared `parse_json_response` helper |
| `backend/llm/gemini_client.py` (modify) | Import neutral exceptions; keep `Gemini*` as aliases; add `model_id`/`dimension` |
| `backend/llm/providers/__init__.py` (new) | Package marker |
| `backend/llm/providers/openai_compat.py` (new) | `OpenAICompatClient` (generation) + `OpenAICompatEmbeddingClient` |
| `backend/llm/providers/anthropic_client.py` (new) | `AnthropicClient` (generation) |
| `backend/llm/factory.py` (new) | `make_llm_client` / `make_embedding_client` / `make_browser_llm` |
| `backend/config.py` (modify) | New `LLM_*` / `EMBEDDING_*` / `BROWSER_LLM_*` settings |
| `backend/llm/cv_editor.py`, `job_analyzer.py`, `cv_modifier.py` (modify) | Default client via factory |
| `backend/main.py` (modify) | Warmup builds clients via factory; exception-handler imports |
| `backend/matching/embedder.py`, `backend/matching/fit_engine.py` (modify) | Use `EmbeddingClient`; dimension guard |
| `backend/applier/auto_apply.py`, `assisted_apply.py` (modify) | Browser LLM via `make_browser_llm` |
| `pyproject.toml`, `.env.example` (modify) | Deps + documented config |
| `tests/test_llm_*.py` (new) | Adapter + factory tests |

---

### Task 1: Protocols + neutral exceptions (`backend/llm/base.py`)

**Files:**
- Create: `backend/llm/base.py`
- Test: `tests/test_llm_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_base.py
import pytest
from backend.llm import base


def test_neutral_exceptions_exist():
    assert issubclass(base.LLMRateLimitError, Exception)
    assert issubclass(base.LLMJSONError, Exception)
    assert issubclass(base.LLMCallFailed, Exception)


def test_parse_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert base.parse_json_response(raw) == {"a": 1}


def test_parse_json_plain():
    assert base.parse_json_response('{"b": 2}') == {"b": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_base.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.llm.base`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/llm/base.py
from __future__ import annotations

import json
from typing import Protocol, Type, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMRateLimitError(Exception):
    """Provider returned a rate-limit (429) error."""


class LLMCallFailed(Exception):
    """A non-rate-limit provider failure (bad key, network, backend 5xx)."""


class LLMJSONError(Exception):
    """Provider returned text that could not be parsed as the expected JSON."""


def parse_json_response(raw: str) -> dict:
    """Parse model text into a dict, tolerating ```json fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


@runtime_checkable
class LLMClient(Protocol):
    """Text/JSON generation contract implemented by every provider adapter."""

    async def generate_text(
        self,
        prompt: str,
        *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str: ...

    async def generate_json(self, prompt: str, schema: Type[T]) -> T: ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """Embedding contract implemented by embedding-capable adapters."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_base.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/llm/base.py tests/test_llm_base.py
git commit -m "feat(llm): add provider-neutral protocols and exceptions"
```

---

### Task 2: Configuration settings (`backend/config.py`)

**Files:**
- Modify: `backend/config.py:38-42` (after the Google model settings block)
- Test: `tests/test_llm_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_config.py
from backend.config import settings


def test_provider_defaults_are_gemini():
    assert settings.LLM_PROVIDER == "gemini"
    assert settings.EMBEDDING_PROVIDER == "gemini"
    assert settings.BROWSER_LLM_PROVIDER == "gemini"
    assert settings.EMBEDDING_MODEL == "text-embedding-004"


def test_provider_optional_fields_default_empty():
    assert settings.LLM_BASE_URL == ""
    assert settings.LLM_MODEL == ""
    assert settings.OPENAI_API_KEY.get_secret_value() == ""
    assert settings.ANTHROPIC_API_KEY.get_secret_value() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'LLM_PROVIDER'`

- [ ] **Step 3: Add settings**

In `backend/config.py`, immediately after line 42 (`GOOGLE_MODEL_FALLBACKS: str = ""`), insert:

```python
    # ── Multi-provider LLM selection (applied on restart) ────────────────
    # Generation
    LLM_PROVIDER: str = "gemini"        # gemini | openai | anthropic
    LLM_MODEL: str = ""                 # provider default if empty
    LLM_BASE_URL: str = ""              # openai-compatible/local, e.g. http://localhost:11434/v1
    LLM_API_KEY: SecretStr = SecretStr("")
    # Embeddings (anthropic unsupported — has no embeddings API)
    EMBEDDING_PROVIDER: str = "gemini"  # gemini | openai
    EMBEDDING_MODEL: str = "text-embedding-004"
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: SecretStr = SecretStr("")
    # Browser agent (anthropic only via openai-compatible base_url)
    BROWSER_LLM_PROVIDER: str = "gemini"  # gemini | openai
    BROWSER_LLM_MODEL: str = ""
    BROWSER_LLM_BASE_URL: str = ""
    BROWSER_LLM_API_KEY: SecretStr = SecretStr("")
    # Per-provider keys (used when the generic *_API_KEY is empty)
    OPENAI_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_llm_config.py
git commit -m "feat(config): add multi-provider LLM settings"
```

---

### Task 3: Conform GeminiClient to the protocols

**Files:**
- Modify: `backend/llm/gemini_client.py:14-19` (imports + exception classes) and add properties after `__init__`
- Test: `tests/test_gemini_conform.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gemini_conform.py
from backend.llm import base
from backend.llm.gemini_client import (
    GeminiClient, GeminiRateLimitError, GeminiJSONError, GeminiCallFailed,
)


def test_gemini_exceptions_alias_neutral():
    assert GeminiRateLimitError is base.LLMRateLimitError
    assert GeminiJSONError is base.LLMJSONError
    assert GeminiCallFailed is base.LLMCallFailed


def test_gemini_client_exposes_model_id_and_dimension(monkeypatch):
    monkeypatch.setattr(
        "backend.llm.gemini_client.genai.Client", lambda **kw: object()
    )
    c = GeminiClient()
    assert c.model_id.startswith("gemini:")
    assert c.dimension == 768
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gemini_conform.py -v`
Expected: FAIL (`GeminiRateLimitError is not base.LLMRateLimitError`)

- [ ] **Step 3: Edit `gemini_client.py`**

Replace lines 14-16 (imports) — add the base import:

```python
from backend.config import settings
from backend.defaults import GEMINI_FALLBACK_MODEL, EMBEDDING_MODEL
from backend.llm.base import (
    LLMCallFailed, LLMJSONError, LLMRateLimitError, parse_json_response,
)
```

Replace the three exception class definitions (lines 66-75) with aliases:

```python
# Backward-compatible aliases — consumers still import the Gemini* names.
GeminiRateLimitError = LLMRateLimitError
GeminiCallFailed = LLMCallFailed
GeminiJSONError = LLMJSONError
```

Add these properties right after `__init__` (after line 101):

```python
    @property
    def model_id(self) -> str:
        return f"gemini:{EMBEDDING_MODEL}"

    @property
    def dimension(self) -> int:
        return 768  # text-embedding-004
```

In `generate_json`, replace the inline `_parse` body (lines 222-228) to reuse the shared helper:

```python
        def _parse(raw: str) -> T:
            data = parse_json_response(raw)
            return schema.model_validate(data)
```

(The existing `embed()` already imports `EMBEDDING_MODEL` locally on line 265 — that local import is now redundant but harmless; leave it.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_gemini_conform.py tests/test_gemini_client.py -v`
Expected: PASS (existing gemini tests still green via aliases)

- [ ] **Step 5: Commit**

```bash
git add backend/llm/gemini_client.py tests/test_gemini_conform.py
git commit -m "refactor(llm): conform GeminiClient to neutral protocol + aliases"
```

---

### Task 4: OpenAI-compatible generation adapter

**Files:**
- Create: `backend/llm/providers/__init__.py` (empty)
- Create: `backend/llm/providers/openai_compat.py`
- Test: `tests/test_openai_compat.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_openai_compat.py
import pytest
from backend.llm import base
from backend.llm.providers.openai_compat import OpenAICompatClient


class _FakeChoice:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content=None, exc=None): self._content, self._exc = content, exc
    async def create(self, **kw):
        if self._exc: raise self._exc
        return _FakeResp(self._content)


class _FakeClient:
    def __init__(self, content=None, exc=None):
        self.chat = type("C", (), {"completions": _FakeCompletions(content, exc)})


@pytest.mark.asyncio
async def test_generate_text_returns_content():
    c = OpenAICompatClient(api_key="k", model="m")
    c._client = _FakeClient(content="hello")
    assert await c.generate_text("hi") == "hello"


@pytest.mark.asyncio
async def test_generate_json_parses():
    from pydantic import BaseModel
    class Out(BaseModel): a: int
    c = OpenAICompatClient(api_key="k", model="m")
    c._client = _FakeClient(content='{"a": 5}')
    assert (await c.generate_json("hi", Out)).a == 5


@pytest.mark.asyncio
async def test_rate_limit_translates():
    c = OpenAICompatClient(api_key="k", model="m")
    c._client = _FakeClient(exc=Exception("Error code: 429 too many requests"))
    with pytest.raises(base.LLMRateLimitError):
        await c.generate_text("hi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement adapter**

```python
# backend/llm/providers/__init__.py
```

```python
# backend/llm/providers/openai_compat.py
from __future__ import annotations

import logging
from typing import Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import (
    LLMCallFailed, LLMJSONError, LLMRateLimitError, parse_json_response,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}


def _resolve_key() -> str:
    return (
        settings.LLM_API_KEY.get_secret_value()
        or settings.OPENAI_API_KEY.get_secret_value()
    )


class OpenAICompatClient:
    """Generation adapter for any OpenAI-compatible endpoint (OpenAI, DeepSeek, local)."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None) -> None:
        self._model = model or settings.LLM_MODEL or _DEFAULT_MODEL
        self._client = AsyncOpenAI(
            api_key=api_key or _resolve_key() or "not-needed",
            base_url=base_url or settings.LLM_BASE_URL or None,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )

    async def generate_text(
        self, prompt: str, *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if response_mime_type == "application/json":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                raise LLMRateLimitError(msg) from e
            raise LLMCallFailed(msg) from e

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        text = await self.generate_text(prompt, response_mime_type="application/json")
        try:
            return schema.model_validate(parse_json_response(text))
        except Exception as first:  # noqa: BLE001
            try:
                text2 = await self.generate_text(prompt)
                return schema.model_validate(parse_json_response(text2))
            except Exception as retry:  # noqa: BLE001
                raise LLMJSONError(
                    f"Invalid JSON from LLM (after retry): {retry}\nRaw: {text[:200]}"
                ) from first


class OpenAICompatEmbeddingClient:
    """Embedding adapter for OpenAI-compatible endpoints (OpenAI, local Ollama, etc.)."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None) -> None:
        self._model = model or settings.EMBEDDING_MODEL or _DEFAULT_EMBED_MODEL
        self._client = AsyncOpenAI(
            api_key=(api_key or settings.EMBEDDING_API_KEY.get_secret_value()
                     or settings.OPENAI_API_KEY.get_secret_value() or "not-needed"),
            base_url=base_url or settings.EMBEDDING_BASE_URL or None,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        self._dimension = _EMBED_DIMS.get(self._model, 1536)

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.embeddings.create(model=self._model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                raise LLMRateLimitError(msg) from e
            raise LLMCallFailed(msg) from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/llm/providers/__init__.py backend/llm/providers/openai_compat.py tests/test_openai_compat.py
git commit -m "feat(llm): OpenAI-compatible generation + embedding adapters"
```

---

### Task 5: Anthropic generation adapter

**Files:**
- Create: `backend/llm/providers/anthropic_client.py`
- Test: `tests/test_anthropic_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anthropic_client.py
import pytest
from backend.llm import base
from backend.llm.providers.anthropic_client import AnthropicClient


class _Block:
    def __init__(self, text): self.text = text


class _FakeResp:
    def __init__(self, text): self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, text=None, exc=None): self._text, self._exc = text, exc
    async def create(self, **kw):
        if self._exc: raise self._exc
        return _FakeResp(self._text)


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self.messages = _FakeMessages(text, exc)


@pytest.mark.asyncio
async def test_generate_text():
    c = AnthropicClient(api_key="k", model="m")
    c._client = _FakeClient(text="hi there")
    assert await c.generate_text("hi") == "hi there"


@pytest.mark.asyncio
async def test_generate_json():
    from pydantic import BaseModel
    class Out(BaseModel): a: int
    c = AnthropicClient(api_key="k", model="m")
    c._client = _FakeClient(text='{"a": 7}')
    assert (await c.generate_json("hi", Out)).a == 7


@pytest.mark.asyncio
async def test_rate_limit_translates():
    c = AnthropicClient(api_key="k", model="m")
    c._client = _FakeClient(exc=Exception("Error code: 429"))
    with pytest.raises(base.LLMRateLimitError):
        await c.generate_text("hi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_anthropic_client.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement adapter**

```python
# backend/llm/providers/anthropic_client.py
from __future__ import annotations

import logging
from typing import Type, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import (
    LLMCallFailed, LLMJSONError, LLMRateLimitError, parse_json_response,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 4096


class AnthropicClient:
    """Generation adapter for the Anthropic Messages API."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._model = model or settings.LLM_MODEL or _DEFAULT_MODEL
        self._client = AsyncAnthropic(
            api_key=(api_key or settings.LLM_API_KEY.get_secret_value()
                     or settings.ANTHROPIC_API_KEY.get_secret_value()),
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )

    async def generate_text(
        self, prompt: str, *,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        # Anthropic has no JSON-mode flag; when JSON is requested we steer via
        # a system instruction and parse downstream in generate_json.
        system = (
            "Respond with only a single valid JSON object, no prose, no code fences."
            if response_mime_type == "application/json" else None
        )
        try:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": _MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = await self._client.messages.create(**kwargs)
            return "".join(getattr(b, "text", "") for b in resp.content)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                raise LLMRateLimitError(msg) from e
            raise LLMCallFailed(msg) from e

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        text = await self.generate_text(prompt, response_mime_type="application/json")
        try:
            return schema.model_validate(parse_json_response(text))
        except Exception as first:  # noqa: BLE001
            raise LLMJSONError(
                f"Invalid JSON from Anthropic: {first}\nRaw: {text[:200]}"
            ) from first
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_anthropic_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/llm/providers/anthropic_client.py tests/test_anthropic_client.py
git commit -m "feat(llm): Anthropic generation adapter"
```

---

### Task 6: Factory

**Files:**
- Create: `backend/llm/factory.py`
- Test: `tests/test_llm_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_factory.py
import pytest
from backend.llm import factory
from backend.llm.gemini_client import GeminiClient
from backend.llm.providers.openai_compat import (
    OpenAICompatClient, OpenAICompatEmbeddingClient,
)
from backend.llm.providers.anthropic_client import AnthropicClient


def _patch_gemini(monkeypatch):
    monkeypatch.setattr("backend.llm.gemini_client.genai.Client", lambda **kw: object())


def test_make_llm_client_gemini(monkeypatch):
    _patch_gemini(monkeypatch)
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "gemini")
    assert isinstance(factory.make_llm_client(), GeminiClient)


def test_make_llm_client_openai(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "openai")
    assert isinstance(factory.make_llm_client(), OpenAICompatClient)


def test_make_llm_client_anthropic(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "anthropic")
    assert isinstance(factory.make_llm_client(), AnthropicClient)


def test_make_llm_client_unknown_raises(monkeypatch):
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        factory.make_llm_client()


def test_make_embedding_anthropic_unsupported(monkeypatch):
    monkeypatch.setattr(factory.settings, "EMBEDDING_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="no embeddings"):
        factory.make_embedding_client()


def test_make_embedding_openai(monkeypatch):
    monkeypatch.setattr(factory.settings, "EMBEDDING_PROVIDER", "openai")
    assert isinstance(factory.make_embedding_client(), OpenAICompatEmbeddingClient)


def test_make_browser_llm_anthropic_requires_base_url(monkeypatch):
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="base_url"):
        factory.make_browser_llm()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_factory.py -v`
Expected: FAIL (`ModuleNotFoundError: backend.llm.factory`)

- [ ] **Step 3: Implement factory**

```python
# backend/llm/factory.py
from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


def make_llm_client():
    """Return an LLMClient for the configured LLM_PROVIDER."""
    provider = (settings.LLM_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from backend.llm.gemini_client import GeminiClient
        return GeminiClient()
    if provider == "openai":
        from backend.llm.providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient()
    if provider == "anthropic":
        from backend.llm.providers.anthropic_client import AnthropicClient
        return AnthropicClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (gemini|openai|anthropic)")


def make_embedding_client():
    """Return an EmbeddingClient for the configured EMBEDDING_PROVIDER."""
    provider = (settings.EMBEDDING_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from backend.llm.gemini_client import GeminiClient
        return GeminiClient()
    if provider == "openai":
        from backend.llm.providers.openai_compat import OpenAICompatEmbeddingClient
        return OpenAICompatEmbeddingClient()
    if provider == "anthropic":
        raise ValueError("EMBEDDING_PROVIDER=anthropic invalid: Anthropic has no embeddings API")
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider!r} (gemini|openai)")


def make_browser_llm():
    """Return a browser-use Chat* instance for the configured BROWSER_LLM_PROVIDER."""
    provider = (settings.BROWSER_LLM_PROVIDER or "gemini").lower()
    if provider == "gemini":
        from browser_use.llm.models import ChatGoogle
        return ChatGoogle(
            model=settings.BROWSER_LLM_MODEL or settings.GOOGLE_MODEL,
            api_key=settings.GOOGLE_API_KEY.get_secret_value(),
        )
    if provider in ("openai", "anthropic"):
        from browser_use.llm.models import ChatOpenAI
        base_url = settings.BROWSER_LLM_BASE_URL
        if provider == "anthropic" and not base_url:
            raise ValueError(
                "BROWSER_LLM_PROVIDER=anthropic needs BROWSER_LLM_BASE_URL "
                "(browser-use ships no ChatAnthropic; use an OpenAI-compatible endpoint)"
            )
        key = (settings.BROWSER_LLM_API_KEY.get_secret_value()
               or settings.OPENAI_API_KEY.get_secret_value()
               or settings.ANTHROPIC_API_KEY.get_secret_value())
        kwargs = {"model": settings.BROWSER_LLM_MODEL or "gpt-4o", "api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    raise ValueError(f"Unknown BROWSER_LLM_PROVIDER: {provider!r} (gemini|openai)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_factory.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/llm/factory.py tests/test_llm_factory.py
git commit -m "feat(llm): provider factory for gen/embed/browser"
```

---

### Task 7: Wire generation call sites to the factory

**Files:**
- Modify: `backend/llm/cv_editor.py:35-36`, `backend/llm/job_analyzer.py:19-20`, `backend/llm/cv_modifier.py:45-46`
- Modify: `backend/main.py:119-123`
- Test: `tests/test_factory_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_factory_wiring.py
def test_consumers_default_to_factory(monkeypatch):
    import backend.llm.factory as factory
    sentinel = object()
    monkeypatch.setattr(factory, "make_llm_client", lambda: sentinel)
    from backend.llm.cv_editor import CVEditor
    from backend.llm.job_analyzer import JobAnalyzer
    from backend.llm.cv_modifier import CVModifier
    assert CVEditor()._client is sentinel
    assert JobAnalyzer()._client is sentinel
    assert CVModifier()._client is sentinel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_wiring.py -v`
Expected: FAIL (default constructs a real `GeminiClient`, not the sentinel)

- [ ] **Step 3: Edit the three consumers**

In `backend/llm/cv_editor.py` change the constructor (line 35-36):

```python
    def __init__(self, client: "LLMClient | None" = None) -> None:
        from backend.llm.factory import make_llm_client
        self._client = client or make_llm_client()
```

And change the import on line 10 to also pull the protocol type for the annotation:

```python
from backend.llm.base import LLMClient, LLMJSONError as GeminiJSONError  # noqa: F401
```

(Keep the existing `GeminiJSONError` usage working; `cv_editor` imports it from `gemini_client` today — the aliased re-export keeps behavior identical. If `cv_editor` references `GeminiClient` elsewhere, leave that import line intact and only swap the constructor body.)

In `backend/llm/job_analyzer.py` (line 19-20):

```python
    def __init__(self, client=None) -> None:
        from backend.llm.factory import make_llm_client
        self._client = client or make_llm_client()
```

In `backend/llm/cv_modifier.py` (line 45-46):

```python
    def __init__(self, client=None) -> None:
        from backend.llm.factory import make_llm_client
        self._client = client or make_llm_client()
```

In `backend/main.py` (lines 119-123), replace the direct construction:

```python
        from backend.llm.factory import make_llm_client
        gen_client = make_llm_client()
        cv_editor = CVEditor(client=gen_client)
        cv_pipeline = CVPipeline(
            job_analyzer=JobAnalyzer(client=gen_client),
            cv_modifier=CVModifier(client=gen_client),
            cv_applicator=CVApplicator(),
        )
```

Note: lines 129/131/143/156 still reference `gemini` for `ScraplingFetcher`, `AdaptiveScraper`, `ApplicationEngine`, and `Embedder`. Update the `scrapling` line (131) and `app.state.gemini` (160) to use `gen_client`:
- Line 131: `scrapling = ScraplingFetcher(gemini_client=gen_client) if settings.SCRAPLING_ENABLED else None`
- Line 160: `app.state.gemini = gen_client`
(`AdaptiveScraper`/`ApplicationEngine` keep taking the raw Google API key — they drive browser-use separately and are handled in Task 9.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_factory_wiring.py tests/test_gemini_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/llm/cv_editor.py backend/llm/job_analyzer.py backend/llm/cv_modifier.py backend/main.py tests/test_factory_wiring.py
git commit -m "feat(llm): route generation consumers through factory"
```

---

### Task 8: Wire embeddings to the factory + dimension guard

**Files:**
- Modify: `backend/matching/embedder.py:14-16` (constructor type hint only — already injectable)
- Modify: `backend/main.py:156` (Embedder gets an embedding client)
- Modify: `backend/matching/fit_engine.py:68` (cosine_similarity length guard)
- Test: `tests/test_embedding_wiring.py`, extend fit-engine test

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embedding_wiring.py
from backend.matching.fit_engine import cosine_similarity


def test_cosine_mismatched_dimensions_returns_zero():
    # Gemini 768 vs OpenAI 1536 must never crash — guard returns 0.0
    assert cosine_similarity([0.1, 0.2, 0.3], [0.1, 0.2]) == 0.0


def test_cosine_normal():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_embedding_wiring.py -v`
Expected: FAIL (mismatched lengths raise or compute wrong value)

- [ ] **Step 3: Implement**

In `backend/matching/fit_engine.py`, find `cosine_similarity` (line 68) and add a length guard as the first check inside the function body:

```python
def cosine_similarity(a, b) -> float:
    """Compute cosine similarity between two vectors. Returns 0.0 for zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    # ... existing implementation unchanged ...
```

In `backend/main.py` line 156, build the embedder from a dedicated embedding client:

```python
        from backend.llm.factory import make_embedding_client
        # ... inside BatchRunner(...) kwargs:
            embedder=Embedder(gemini_client=make_embedding_client()),
```

In `backend/matching/embedder.py`, update the constructor type hint/param name for clarity (behavior unchanged — it only calls `.embed`):

```python
    def __init__(self, gemini_client) -> None:  # accepts any EmbeddingClient
        self._client = gemini_client
```

(The param name stays `gemini_client` to avoid touching the `main.py`/`batch_runner.py` call sites that pass it by keyword.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_embedding_wiring.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/matching/fit_engine.py backend/main.py backend/matching/embedder.py tests/test_embedding_wiring.py
git commit -m "feat(llm): route embeddings through factory + dimension guard"
```

---

### Task 9: Wire the browser agent to the factory

**Files:**
- Modify: `backend/applier/auto_apply.py:322-325`, `434-437`, and the import block (lines 26/33)
- Modify: `backend/applier/assisted_apply.py:186-189`, import block (lines 25/32)
- Test: `tests/test_browser_llm_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_browser_llm_wiring.py
import backend.llm.factory as factory


def test_make_browser_llm_called_for_gemini(monkeypatch):
    called = {}
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "gemini")

    class _FakeChatGoogle:
        def __init__(self, **kw): called.update(kw)

    import browser_use.llm.models as models
    monkeypatch.setattr(models, "ChatGoogle", _FakeChatGoogle)
    llm = factory.make_browser_llm()
    assert isinstance(llm, _FakeChatGoogle)
    assert "model" in called and "api_key" in called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_browser_llm_wiring.py -v`
Expected: FAIL only if factory import path wrong; if Task 6 done, this passes — then proceed to wire appliers (the value is wiring the appliers, below).

- [ ] **Step 3: Edit the appliers**

In `backend/applier/auto_apply.py`, replace BOTH `ChatGoogle(...)` blocks (lines 322-325 and 434-437) with:

```python
            from backend.llm.factory import make_browser_llm
            llm = make_browser_llm()
```

and (second site):

```python
            from backend.llm.factory import make_browser_llm
            llm2 = make_browser_llm()
```

Remove the now-unused top-of-file `ChatGoogle` import guard (lines 26 and 33) — or leave the import but stop using it. Preferred: delete lines 26 and 33's `ChatGoogle` references since the factory owns construction now. The `self._model`/`self._api_key` fields stay (still used for logging / Gemini key resolution inside the factory default).

In `backend/applier/assisted_apply.py`, replace the `ChatGoogle(...)` block (lines 186-189):

```python
            from backend.llm.factory import make_browser_llm
            llm = make_browser_llm()
```

and remove/ignore the `ChatGoogle` import (lines 25/32).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_browser_llm_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/applier/auto_apply.py backend/applier/assisted_apply.py tests/test_browser_llm_wiring.py
git commit -m "feat(llm): route browser agent through make_browser_llm"
```

---

### Task 10: Dependencies, `.env.example`, exception handlers, settings API

**Files:**
- Modify: `pyproject.toml:11-12` (add deps)
- Modify: `.env.example`
- Modify: `backend/main.py:387-392` (handler import comment — provider-neutral)
- Test: `tests/test_provider_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provider_smoke.py
def test_openai_and_anthropic_importable():
    import openai  # noqa: F401
    import anthropic  # noqa: F401


def test_exception_handlers_use_aliases():
    # main.py imports GeminiJSONError/GeminiRateLimitError which now alias neutral types
    from backend.llm.base import LLMJSONError, LLMRateLimitError
    from backend.llm.gemini_client import GeminiJSONError, GeminiRateLimitError
    assert GeminiJSONError is LLMJSONError
    assert GeminiRateLimitError is LLMRateLimitError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_provider_smoke.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'openai'`)

- [ ] **Step 3: Add deps + docs**

In `pyproject.toml`, add to the dependencies list (near lines 11-12):

```toml
    "openai>=1.40",
    "anthropic>=0.40",
```

Install: `uv sync`

Append to `.env.example`:

```
# ── Multi-provider LLM (applied on restart) ─────────────────────────────
# Generation: gemini | openai | anthropic
LLM_PROVIDER=gemini
LLM_MODEL=
# For openai-compatible/local (DeepSeek, Ollama, LM Studio, vLLM):
#   LLM_PROVIDER=openai
#   LLM_BASE_URL=http://localhost:11434/v1   # Ollama example
LLM_BASE_URL=
LLM_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Embeddings: gemini | openai  (anthropic has no embeddings API)
EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=text-embedding-004
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=

# Browser agent: gemini | openai  (anthropic only via OpenAI-compatible base_url)
BROWSER_LLM_PROVIDER=gemini
BROWSER_LLM_MODEL=
BROWSER_LLM_BASE_URL=
BROWSER_LLM_API_KEY=
```

In `backend/main.py`, update the comment above the handler import (lines 387-392) to note the names are now neutral aliases (no code change needed — the import keeps working):

```python
try:
    # These names are now aliases of the provider-neutral LLM* exceptions
    # (see backend/llm/base.py), so the handlers cover every provider.
    from backend.llm.gemini_client import GeminiJSONError as _GeminiJSONErr
    from backend.llm.gemini_client import GeminiRateLimitError as _GeminiRateErr
except ImportError:
    _GeminiJSONErr = None  # type: ignore[assignment,misc]
    _GeminiRateErr = None  # type: ignore[assignment,misc]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_provider_smoke.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .env.example backend/main.py tests/test_provider_smoke.py
git commit -m "build(llm): add openai+anthropic deps, document providers"
```

---

### Task 11: Full regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (existing + new). Investigate any failure before proceeding.

- [ ] **Step 2: Manual provider smoke (optional, requires keys)**

With `LLM_PROVIDER=openai` + `OPENAI_API_KEY` (or `LLM_BASE_URL` to a local Ollama), start the server and trigger one job analysis; confirm a structured result returns and logs show no Gemini calls. Repeat with `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`.

- [ ] **Step 3: Commit (if any fixups)**

```bash
git add -A && git commit -m "test(llm): regression fixups for multi-provider"
```

---

## Self-review checklist (completed by plan author)

- **Spec §2 generation / §3 config / §4 exceptions / §6 browser** → Tasks 1–10 cover each.
- **Spec §5 embeddings auto-reembed** → reframed (Task 8): embeddings are transient, so a provider switch self-heals on next run; dimension guard added. Deviation documented in Notes.
- **Type consistency:** `make_llm_client`/`make_embedding_client`/`make_browser_llm`, `model_id`, `dimension`, `parse_json_response`, `LLM{RateLimit,JSON,Call}Error` used identically across tasks.
- **No placeholders:** every code step shows real code.
