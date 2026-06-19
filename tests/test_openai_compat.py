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
