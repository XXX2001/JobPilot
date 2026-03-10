"""Tests for PlaywrightFormFiller pure helper functions."""
from __future__ import annotations

import pytest
from backend.applier.form_filler import PlaywrightFormFiller


def _filler() -> PlaywrightFormFiller:
    """Create a filler instance without any live clients."""
    from unittest.mock import MagicMock
    return PlaywrightFormFiller(gemini_client=MagicMock())


# ── _clean_form_html ──────────────────────────────────────────────────────────


def test_clean_form_html_removes_scripts():
    filler = _filler()
    html = "<html><body><form><input name='x'/></form><script>evil()</script></body></html>"
    result = filler._clean_form_html(html)
    assert "evil()" not in result
    assert "input" in result.lower() or "x" in result


def test_clean_form_html_removes_nav_and_footer():
    filler = _filler()
    html = (
        "<html><body>"
        "<nav>Menu</nav>"
        "<form><input name='email' placeholder='Email'/></form>"
        "<footer>Footer</footer>"
        "</body></html>"
    )
    result = filler._clean_form_html(html)
    assert "Menu" not in result
    assert "Footer" not in result
    assert "email" in result.lower()


def test_clean_form_html_keeps_form_attributes():
    filler = _filler()
    html = (
        "<form>"
        "<label for='name'>Name</label>"
        "<input id='name' name='full_name' type='text' placeholder='Your name' required/>"
        "<input type='file' name='resume'/>"
        "<button type='submit'>Apply</button>"
        "</form>"
    )
    result = filler._clean_form_html(html)
    assert "name" in result.lower()
    assert "submit" in result.lower() or "apply" in result.lower()


def test_clean_form_html_truncates_to_max():
    filler = _filler()
    big_html = "<form>" + "<input name='x'/>" * 5000 + "</form>"
    result = filler._clean_form_html(big_html)
    assert len(result) <= 15_100  # 15_000 + small markdown overhead


# ── _build_fill_prompt ────────────────────────────────────────────────────────


def test_build_fill_prompt_contains_applicant_fields():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form><input name='email'/></form>",
        full_name="Alice Dupont",
        email="alice@example.com",
        phone="+33 6 00 00 00 00",
        location="Paris, France",
        additional_answers=None,
        has_cv=True,
        has_letter=True,
    )
    assert "Alice Dupont" in prompt
    assert "alice@example.com" in prompt
    assert "+33 6 00 00 00 00" in prompt
    assert "Paris, France" in prompt


def test_build_fill_prompt_mentions_file_upload_when_files_present():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form><input type='file'/></form>",
        full_name="Bob",
        email="bob@x.com",
        phone="",
        location="",
        additional_answers=None,
        has_cv=True,
        has_letter=False,
    )
    assert "cv" in prompt.lower() or "resume" in prompt.lower()


def test_build_fill_prompt_includes_additional_answers():
    filler = _filler()
    import json
    answers = json.dumps({"years_experience": "3", "visa_required": "no"})
    prompt = filler._build_fill_prompt(
        form_content="<form/>",
        full_name="X",
        email="x@x.com",
        phone="",
        location="",
        additional_answers=answers,
        has_cv=False,
        has_letter=False,
    )
    assert "years_experience" in prompt
    assert "visa_required" in prompt


def test_build_fill_prompt_returns_json_schema_instructions():
    filler = _filler()
    prompt = filler._build_fill_prompt(
        form_content="<form/>",
        full_name="X",
        email="x@x.com",
        phone="",
        location="",
        additional_answers=None,
        has_cv=False,
        has_letter=False,
    )
    assert "fields" in prompt
    assert "submit_selector" in prompt
    assert "JSON" in prompt


# ── _parse_gemini_response ────────────────────────────────────────────────────


def test_parse_gemini_response_valid_json():
    filler = _filler()
    raw = '{"fields": [{"selector": "#name", "value": "Alice"}], "file_inputs": [], "submit_selector": "button[type=submit]"}'
    result = filler._parse_gemini_response(raw)
    assert result["fields"][0]["selector"] == "#name"
    assert result["submit_selector"] == "button[type=submit]"


def test_parse_gemini_response_handles_markdown_fences():
    filler = _filler()
    raw = '```json\n{"fields": [], "file_inputs": [], "submit_selector": "#submit"}\n```'
    result = filler._parse_gemini_response(raw)
    assert result["submit_selector"] == "#submit"


def test_parse_gemini_response_returns_defaults_on_empty():
    filler = _filler()
    result = filler._parse_gemini_response("")
    assert result["fields"] == []
    assert result["file_inputs"] == []
    assert "submit_selector" in result


def test_parse_gemini_response_returns_defaults_on_invalid_json():
    filler = _filler()
    result = filler._parse_gemini_response("not json at all")
    assert result["fields"] == []
