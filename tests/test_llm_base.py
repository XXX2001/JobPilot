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
