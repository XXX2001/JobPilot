"""Tests for the M2-T2 patch-fields flow.

Covers three layers:
  1. ``ws_models.ClientMessage`` accepts a ``patch_fields`` message and
     rejects a malformed one.
  2. ``ApplicationEngine.signal_patch_fields`` stores patches,
     ``get_pending_patches`` returns them (or ``{}``), and the entry is
     purged on confirm / cancel.
  3. The Tier-1 form filler re-fills patched selectors before clicking
     submit, and a single failing ``page.fill`` logs a warning without
     aborting the submit.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.api.ws_models import ClientMessage, PatchFields
from backend.applier.engine import ApplicationEngine
from backend.applier.form_filler import PlaywrightFormFiller


# ══════════════════════════════════════════════════════════════════════════════
#  ws_models — PatchFields inbound message
# ══════════════════════════════════════════════════════════════════════════════


def test_client_message_accepts_patch_fields():
    adapter = TypeAdapter(ClientMessage)
    msg = adapter.validate_python(
        {"type": "patch_fields", "job_id": 7, "fields": {"#name": "Alice"}}
    )
    assert isinstance(msg, PatchFields)
    assert msg.job_id == 7
    assert msg.fields == {"#name": "Alice"}


def test_client_message_rejects_malformed_patch_fields():
    adapter = TypeAdapter(ClientMessage)
    # Missing the required ``fields`` mapping.
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "patch_fields", "job_id": 7})


# ══════════════════════════════════════════════════════════════════════════════
#  ApplicationEngine — pending patches store / retrieve / purge
# ══════════════════════════════════════════════════════════════════════════════


def _make_engine() -> ApplicationEngine:
    return ApplicationEngine(api_key="test-key", daily_limit=10)


def test_signal_patch_fields_stores_and_get_returns_dict():
    engine = _make_engine()
    engine.signal_patch_fields(11, {"#email": "a@b.com"})
    assert engine.get_pending_patches(11) == {"#email": "a@b.com"}


def test_get_pending_patches_returns_empty_when_absent():
    engine = _make_engine()
    assert engine.get_pending_patches(999) == {}


def test_pending_patches_cleared_on_confirm():
    engine = _make_engine()
    engine.signal_patch_fields(12, {"#x": "y"})
    assert engine.get_pending_patches(12) == {"#x": "y"}

    engine.signal_confirm(12)
    assert engine.get_pending_patches(12) == {}


def test_pending_patches_cleared_on_cancel():
    engine = _make_engine()
    engine.signal_patch_fields(13, {"#x": "y"})
    assert engine.get_pending_patches(13) == {"#x": "y"}

    engine.signal_cancel(13)
    assert engine.get_pending_patches(13) == {}


def test_engine_injects_on_get_patches_into_form_filler():
    """The accessor callback must be threaded down to the Tier-1 filler the
    same way ``on_review`` is."""
    engine = _make_engine()
    if engine._auto._form_filler is not None:
        assert engine._auto._form_filler._on_get_patches == engine.get_pending_patches


# ══════════════════════════════════════════════════════════════════════════════
#  PlaywrightFormFiller — patches re-filled before submit
# ══════════════════════════════════════════════════════════════════════════════


def _gemini_returning(mapping_json: str) -> MagicMock:
    gemini = MagicMock()
    gemini.generate_text = AsyncMock(return_value=mapping_json)
    return gemini


def _patch_playwright(monkeypatch, page: MagicMock) -> None:
    """Patch ``playwright.async_api.async_playwright`` so ``fill_and_submit``
    drives the supplied mock ``page`` without a real browser."""
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    pw = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=context)
    pw.stop = AsyncMock()

    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)

    import playwright.async_api as pw_mod

    monkeypatch.setattr(pw_mod, "async_playwright", MagicMock(return_value=starter))


def _no_captcha(monkeypatch) -> None:
    import backend.applier.captcha_handler as cap

    monkeypatch.setattr(cap, "check_and_handle_captcha", AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_patches_refilled_before_submit(monkeypatch):
    """When patches exist for the job, ``page.fill`` is called with the
    patched selector/value before ``page.click(submit)``."""
    _no_captcha(monkeypatch)

    page = MagicMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<form><input name='name'/></form>")
    page.fill = AsyncMock()
    page.set_input_files = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"img")
    page.click = AsyncMock()
    _patch_playwright(monkeypatch, page)

    gemini = _gemini_returning(
        '{"fields": [{"selector": "#name", "value": "Auto"}],'
        ' "file_inputs": [], "submit_selector": "#submit"}'
    )

    filler = PlaywrightFormFiller(
        gemini_client=gemini,
        on_get_patches=lambda job_id: {"#name": "Edited", "#phone": "123"},
    )

    confirm = asyncio.Event()
    confirm.set()
    cancel = asyncio.Event()

    result = await filler.fill_and_submit(
        apply_url="https://example.com/job",
        job_id=42,
        confirm_event=confirm,
        cancel_event=cancel,
    )

    assert result["status"] == "applied"

    # Both patched selectors must have been re-filled.
    fill_calls = [c for c in page.fill.await_args_list]
    patched = {(c.args[0], c.args[1]) for c in fill_calls if c.args and c.args[0] in ("#name", "#phone")}
    assert ("#name", "Edited") in patched
    assert ("#phone", "123") in patched

    # Patch fills must happen before the submit click. Inspect the unified
    # call log on the shared mock to assert ordering.
    names = [c[0] for c in page.mock_calls]
    assert "click" in names
    last_fill_idx = max(i for i, c in enumerate(page.mock_calls) if c[0] == "fill")
    click_idx = next(i for i, c in enumerate(page.mock_calls) if c[0] == "click")
    assert last_fill_idx < click_idx


@pytest.mark.asyncio
async def test_failing_patch_logs_warning_but_still_submits(monkeypatch, caplog):
    """A failing ``page.fill`` for one patch logs a warning but the submit
    click still fires."""
    _no_captcha(monkeypatch)

    def fill_side_effect(selector, value, **kwargs):
        if selector == "#broken":
            raise RuntimeError("element not found")
        return None

    page = MagicMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<form></form>")
    page.fill = AsyncMock(side_effect=fill_side_effect)
    page.set_input_files = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"img")
    page.click = AsyncMock()
    _patch_playwright(monkeypatch, page)

    gemini = _gemini_returning(
        '{"fields": [], "file_inputs": [], "submit_selector": "#submit"}'
    )

    filler = PlaywrightFormFiller(
        gemini_client=gemini,
        on_get_patches=lambda job_id: {"#broken": "x", "#ok": "y"},
    )

    confirm = asyncio.Event()
    confirm.set()

    with caplog.at_level(logging.WARNING):
        result = await filler.fill_and_submit(
            apply_url="https://example.com/job",
            job_id=99,
            confirm_event=confirm,
            cancel_event=asyncio.Event(),
        )

    assert result["status"] == "applied"
    page.click.assert_awaited_once()
    assert any("#broken" in rec.getMessage() for rec in caplog.records)
