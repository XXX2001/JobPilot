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
