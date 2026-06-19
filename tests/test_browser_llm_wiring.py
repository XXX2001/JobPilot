import backend.llm.factory as factory

def test_make_browser_llm_called_for_openai(monkeypatch):
    called = {}
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_PROVIDER", "openai")
    monkeypatch.setattr(factory.settings, "BROWSER_LLM_BASE_URL", "http://localhost:11434/v1")

    class _FakeChatOpenAI:
        def __init__(self, **kw): called.update(kw)

    import browser_use.llm.models as models
    monkeypatch.setattr(models, "ChatOpenAI", _FakeChatOpenAI)
    llm = factory.make_browser_llm()
    assert isinstance(llm, _FakeChatOpenAI)
    assert "model" in called and "api_key" in called
