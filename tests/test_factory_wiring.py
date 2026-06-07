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
