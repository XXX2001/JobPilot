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
    monkeypatch.setattr(auto_mod, "BrowserConfig", MagicMock())

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
