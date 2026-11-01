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

from backend.applier.engine import ApplicationEngine
from backend.applier.form_filler import PlaywrightFormFiller


# ══════════════════════════════════════════════════════════════════════════════
#  ws_models — PatchFields inbound message
# ══════════════════════════════════════════════════════════════════════════════


# W07: the WS patch_fields/confirm_submit messages were replaced by the
# acknowledged review-decision command (tests/test_review_decision.py).


# ══════════════════════════════════════════════════════════════════════════════
#  ApplicationEngine — pending patches store / retrieve / purge
# ══════════════════════════════════════════════════════════════════════════════


def _make_engine() -> ApplicationEngine:
    return ApplicationEngine(daily_limit=10)


def test_signal_patch_fields_stores_and_get_returns_dict():
    engine = _make_engine()
    engine.signal_patch_fields(11, {"#email": "a@b.com"})
    assert engine.get_pending_patches(11) == {"#email": "a@b.com"}


def test_get_pending_patches_returns_empty_when_absent():
    engine = _make_engine()
    assert engine.get_pending_patches(999) == {}


def test_pending_patches_survive_confirm():
    """Regression guard: ``signal_confirm`` must NOT purge patches.

    ``event.set()`` only schedules the waiter wakeup; the form-filler
    coroutine resumes *after* ``signal_confirm`` returns and only then reads
    the patches. Purging here (as the original M2-T2 commit did) cleared the
    user's edits before they were re-filled, making the feature a no-op.
    """
    engine = _make_engine()
    engine.signal_patch_fields(12, {"#x": "y"})
    assert engine.get_pending_patches(12) == {"#x": "y"}

    engine.signal_confirm(12)
    # Patches must still be readable so the form filler can re-fill them.
    assert engine.get_pending_patches(12) == {"#x": "y"}


def test_pending_patches_cleared_on_cancel():
    engine = _make_engine()
    engine.signal_patch_fields(13, {"#x": "y"})
    assert engine.get_pending_patches(13) == {"#x": "y"}

    engine.signal_cancel(13)
    assert engine.get_pending_patches(13) == {}


@pytest.mark.asyncio
async def test_pending_patches_cleared_after_apply_finally():
    """The ``apply()`` ``finally`` block purges any leftover patches so a
    later apply for the same job_id never re-fills stale edits."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from unittest.mock import patch

    from backend.applier.engine import ApplyMode

    engine = _make_engine()
    engine.signal_patch_fields(14, {"#x": "y"})
    assert engine.get_pending_patches(14) == {"#x": "y"}

    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    with patch("backend.applier.manual_apply.webbrowser.open"):
        await engine.apply(
            job_match_id=14,
            mode=ApplyMode.MANUAL,
            db=db,
            apply_url="https://jobs.example.com/14",
        )

    assert engine.get_pending_patches(14) == {}


def test_engine_injects_on_get_patches_into_form_filler():
    """The accessor callback must be threaded down to the Tier-1 filler the
    same way ``on_review`` is."""
    engine = _make_engine()
    if engine._auto._form_filler is not None:
        assert engine._auto._form_filler._on_get_patches == engine.get_pending_patches


# ══════════════════════════════════════════════════════════════════════════════
#  PlaywrightFormFiller — patches re-filled before submit
# ══════════════════════════════════════════════════════════════════════════════


def _llm_returning(mapping_json: str) -> MagicMock:
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=mapping_json)
    return llm


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


# W08: Tier-1 correction handling (re-fill, read-back, re-review, never submit
# on a failed correction) is covered end to end in tests/test_submission_boundary.py.
