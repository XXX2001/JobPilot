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
