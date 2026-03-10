"""Tests for ApplicationEngine, DailyLimitGuard, and apply strategies."""

from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.applier.daily_limit import DailyLimitGuard, DailyLimitExceeded
from backend.applier.engine import ApplicationEngine, ApplyMode, ApplicantInfo, ApplicationResult
from backend.applier.manual_apply import ManualApplyStrategy
from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy


# ══════════════════════════════════════════════════════════════════════════════
#  DailyLimitGuard
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_daily_limit_remaining_today():
    """remaining_today returns limit - count."""
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=4)
    guard = DailyLimitGuard(db=db, limit=10)
    remaining = await guard.remaining_today()
    assert remaining == 6


@pytest.mark.asyncio
async def test_daily_limit_can_apply_true():
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=0)
    guard = DailyLimitGuard(db=db, limit=10)
    assert await guard.can_apply() is True


@pytest.mark.asyncio
async def test_daily_limit_can_apply_false_at_limit():
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=10)
    guard = DailyLimitGuard(db=db, limit=10)
    assert await guard.can_apply() is False


@pytest.mark.asyncio
async def test_daily_limit_assert_raises():
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=10)
    guard = DailyLimitGuard(db=db, limit=10)
    with pytest.raises(DailyLimitExceeded):
        await guard.assert_can_apply()


# ══════════════════════════════════════════════════════════════════════════════
#  ManualApplyStrategy
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_manual_apply_calls_webbrowser_open():
    strategy = ManualApplyStrategy()
    with patch("backend.applier.manual_apply.webbrowser.open") as mock_open:
        result = await strategy.apply(apply_url="https://jobs.example.com/42")
    mock_open.assert_called_once_with("https://jobs.example.com/42")
    assert result.status == "manual"
    assert result.method == "manual"


@pytest.mark.asyncio
async def test_manual_apply_browser_fail_returns_message():
    strategy = ManualApplyStrategy()
    with patch("backend.applier.manual_apply.webbrowser.open", side_effect=OSError("no browser")):
        result = await strategy.apply(apply_url="https://jobs.example.com/43")
    assert result.status == "manual"
    assert "Could not open browser" in result.message


# ══════════════════════════════════════════════════════════════════════════════
#  AssistedApplyStrategy (without browser-use)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_assisted_apply_fallback_when_no_browser_use(monkeypatch):
    """When browser-use is unavailable, fall back to webbrowser.open."""
    import backend.applier.assisted_apply as mod

    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", False)
    monkeypatch.setattr(mod, "Agent", None)

    with patch("webbrowser.open") as mock_open:
        strategy = AssistedApplyStrategy(api_key="test-key")
        result = await strategy.apply(apply_url="https://example.com/job")
    assert result.status == "assisted"


# ══════════════════════════════════════════════════════════════════════════════
#  AutoApplyStrategy (without browser-use)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auto_apply_fallback_when_no_browser_use(monkeypatch):
    """When browser-use is unavailable, fall back to webbrowser.open."""
    import backend.applier.auto_apply as mod

    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", False)
    monkeypatch.setattr(mod, "Agent", None)

    with patch("webbrowser.open"):
        strategy = AutoApplyStrategy(api_key="test-key")
        result = await strategy.apply(
            job_id=99,
            apply_url="https://example.com/job",
        )
    assert result.status == "manual"


# ══════════════════════════════════════════════════════════════════════════════
#  ApplicationEngine
# ══════════════════════════════════════════════════════════════════════════════


def _make_engine() -> ApplicationEngine:
    return ApplicationEngine(api_key="test-key", daily_limit=10)


@pytest.mark.asyncio
async def test_engine_manual_apply_records_application():
    engine = _make_engine()

    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=0)  # can apply
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    with patch("backend.applier.manual_apply.webbrowser.open"):
        result = await engine.apply(
            job_match_id=1,
            mode=ApplyMode.MANUAL,
            db=db,
            apply_url="https://jobs.example.com/1",
        )

    assert result.status == "manual"
    assert result.method == "manual"
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_engine_daily_limit_exceeded_returns_cancelled():
    engine = _make_engine()

    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=10)  # at limit

    result = await engine.apply(
        job_match_id=2,
        mode=ApplyMode.AUTO,  # not manual → daily limit applies
        db=db,
        apply_url="https://jobs.example.com/2",
    )

    assert result.status == "cancelled"
    assert "limit" in result.message.lower()


def test_engine_signal_confirm_sets_event():
    engine = _make_engine()
    event = asyncio.Event()
    engine._confirm_events[5] = event
    engine.signal_confirm(5)
    assert event.is_set()


def test_engine_signal_cancel_sets_event():
    engine = _make_engine()
    event = asyncio.Event()
    engine._cancel_events[7] = event
    engine.signal_cancel(7)
    assert event.is_set()


def test_engine_signal_unknown_job_no_error():
    engine = _make_engine()
    engine.signal_confirm(999)  # should not raise
    engine.signal_cancel(999)  # should not raise


@pytest.mark.asyncio
async def test_engine_cancel_apply_returns_cancelled(monkeypatch):
    """If cancel_event is set before confirm, result should be 'cancelled'."""
    import backend.applier.auto_apply as auto_mod

    monkeypatch.setattr(auto_mod, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(auto_mod, "Agent", MagicMock())
    monkeypatch.setattr(auto_mod, "ChatGoogleGenerativeAI", MagicMock())
    monkeypatch.setattr(auto_mod, "Browser", MagicMock())
    monkeypatch.setattr(auto_mod, "BrowserConfig", MagicMock(), raising=False)

    engine = _make_engine()
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=0)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    # Patch auto strategy to immediately set cancel and return cancelled
    async def fake_apply(**kwargs):
        return ApplicationResult(status="cancelled", method="auto", message="Cancelled by user.")

    engine._auto.apply = fake_apply  # type: ignore[method-assign]

    result = await engine.apply(
        job_match_id=10,
        mode=ApplyMode.AUTO,
        db=db,
        apply_url="https://jobs.example.com/10",
    )
    assert result.status == "cancelled"


# ── Tier routing ──────────────────────────────────────────────────────────────

_SANITIZE = "backend.security.sanitizer.sanitize_url"

@pytest.mark.asyncio
async def test_auto_apply_tier1_success_no_tier2():
    """If Tier 1 succeeds, Tier 2 (browser-use) should NOT be called."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")
    fake_result = {"status": "applied", "filled_fields": {}, "screenshot_b64": None}

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(return_value=fake_result)) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock()) as mock_t2:
        result = await strategy.apply(job_id=1, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_not_awaited()
    assert result.status == "applied"


@pytest.mark.asyncio
async def test_auto_apply_tier1_failure_falls_back_to_tier2():
    """If Tier 1 raises, Tier 2 (browser-use) should be called."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from backend.applier.manual_apply import ApplicationResult
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(side_effect=RuntimeError("preflight failed"))) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock(return_value=ApplicationResult(status="applied", method="auto"))) as mock_t2:
        result = await strategy.apply(job_id=2, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_awaited_once()
    assert result.status == "applied"


@pytest.mark.asyncio
async def test_auto_apply_tier1_cancelled_does_not_fall_back():
    """If Tier 1 returns cancelled (user cancelled), do NOT fall back to Tier 2."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AutoApplyStrategy(api_key="key")
    fake_result = {"status": "cancelled", "filled_fields": {}, "screenshot_b64": None}

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(return_value=fake_result)) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock()) as mock_t2:
        result = await strategy.apply(job_id=3, apply_url="https://example.com/job")

    mock_t1.assert_awaited_once()
    mock_t2.assert_not_awaited()
    assert result.status == "cancelled"


@pytest.mark.asyncio
async def test_auto_apply_tier1_disabled_goes_straight_to_tier2(monkeypatch):
    """When APPLY_TIER1_ENABLED=False, skip Tier 1 entirely."""
    from backend.applier.auto_apply import AutoApplyStrategy
    from backend.applier.manual_apply import ApplicationResult
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr("backend.config.settings.APPLY_TIER1_ENABLED", False)
    strategy = AutoApplyStrategy(api_key="key")

    with patch(_SANITIZE, side_effect=lambda u: u), \
         patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock()) as mock_t1, \
         patch.object(strategy, "_browser_use_apply", new=AsyncMock(return_value=ApplicationResult(status="applied", method="auto"))) as mock_t2:
        result = await strategy.apply(job_id=4, apply_url="https://example.com/job")

    mock_t1.assert_not_awaited()
    mock_t2.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_use_apply_parses_additional_answers_json(monkeypatch):
    """Tier 2 _browser_use_apply formats additional_answers as key-value pairs, not raw JSON."""
    import backend.applier.auto_apply as mod
    from backend.applier.auto_apply import AutoApplyStrategy
    from unittest.mock import AsyncMock, MagicMock
    import json

    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(mod, "ChatGoogleGenerativeAI", MagicMock())
    monkeypatch.setattr(mod, "Browser", MagicMock())

    captured_task: list[str] = []

    def fake_agent(task, llm, browser):
        captured_task.append(task)
        m = MagicMock()
        m.run = AsyncMock(return_value=MagicMock(final_result=MagicMock(return_value="")))
        return m

    monkeypatch.setattr(mod, "Agent", fake_agent)

    strategy = AutoApplyStrategy(api_key="key")
    answers = json.dumps({"years_experience": "3", "visa_required": "no"})

    cancel = asyncio.Event()
    cancel.set()

    await strategy._browser_use_apply(
        job_id=99,
        apply_url="https://example.com/job",
        additional_answers=answers,
        cancel_event=cancel,
        confirm_event=asyncio.Event(),
    )

    assert captured_task, "Agent was never called"
    task_str = captured_task[0]
    assert "years_experience" in task_str
    assert "visa_required" in task_str
    assert '{"years_experience"' not in task_str  # raw JSON must not appear


@pytest.mark.asyncio
async def test_assisted_apply_tier1_success():
    """AssistedApplyStrategy uses fill_only() when Tier 1 available."""
    from backend.applier.assisted_apply import AssistedApplyStrategy
    from unittest.mock import AsyncMock, patch

    strategy = AssistedApplyStrategy(api_key="key")
    fake_result = {"status": "assisted", "filled_fields": {"#name": "Alice"}}

    with patch.object(strategy._form_filler, "fill_only", new=AsyncMock(return_value=fake_result)) as mock_t1:
        result = await strategy.apply(apply_url="https://example.com/job", full_name="Alice")

    mock_t1.assert_awaited_once()
    assert result.status == "assisted"
    assert "pre-filled" in result.message.lower()


@pytest.mark.asyncio
async def test_assisted_apply_tier1_failure_falls_back():
    """AssistedApplyStrategy falls back to browser-use when fill_only() raises."""
    import backend.applier.assisted_apply as mod
    from backend.applier.assisted_apply import AssistedApplyStrategy
    from unittest.mock import AsyncMock, MagicMock, patch

    strategy = AssistedApplyStrategy(api_key="key")

    with patch.object(strategy._form_filler, "fill_only", new=AsyncMock(side_effect=RuntimeError("page crash"))), \
         patch.object(mod, "_BROWSER_USE_AVAILABLE", True), \
         patch.object(mod, "Agent", MagicMock(return_value=MagicMock(run=AsyncMock()))), \
         patch.object(mod, "ChatGoogleGenerativeAI", MagicMock()), \
         patch.object(mod, "Browser", MagicMock()):
        result = await strategy.apply(apply_url="https://example.com/job")

    assert result.status == "assisted"


@pytest.mark.asyncio
async def test_resolve_documents_returns_cv_and_letter_paths():
    """_resolve_documents queries tailored_documents and returns Path objects."""
    from backend.api.applications import _resolve_documents
    from backend.models.document import TailoredDocument
    from unittest.mock import AsyncMock, MagicMock
    from pathlib import Path

    cv_doc = MagicMock(spec=TailoredDocument)
    cv_doc.pdf_path = "/data/cvs/cv.pdf"
    letter_doc = MagicMock(spec=TailoredDocument)
    letter_doc.pdf_path = "/data/letters/letter.pdf"

    db = AsyncMock()
    # Each .execute() call returns an object whose .scalar_one_or_none() gives the doc
    db.execute.return_value.scalar_one_or_none = MagicMock(side_effect=[cv_doc, letter_doc])

    cv_path, letter_path = await _resolve_documents(match_id=42, db=db)
    assert cv_path == Path("/data/cvs/cv.pdf")
    assert letter_path == Path("/data/letters/letter.pdf")


@pytest.mark.asyncio
async def test_resolve_documents_returns_none_when_no_docs():
    """_resolve_documents returns (None, None) when no tailored docs exist."""
    from backend.api.applications import _resolve_documents
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)

    cv_path, letter_path = await _resolve_documents(match_id=99, db=db)
    assert cv_path is None
    assert letter_path is None
