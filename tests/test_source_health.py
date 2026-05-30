"""Tests for backend.scraping.source_health.SourceHealthTracker."""

from __future__ import annotations

import pytest

from backend.scraping.source_health import SourceHealthTracker


def test_unknown_source_returns_none():
    tracker = SourceHealthTracker()
    assert tracker.get("linkedin") is None
    assert tracker.snapshot() == []


def test_record_ok_marks_healthy():
    tracker = SourceHealthTracker()
    rec = tracker.record("linkedin", outcome="ok", job_count=12)
    assert rec.status() == "healthy"
    assert rec.last_outcome == "ok"
    assert rec.last_job_count == 12
    assert rec.consecutive_failures == 0
    assert rec.total_attempts == 1
    assert rec.total_jobs == 12
    assert rec.last_success_at is not None
    assert rec.last_error is None


def test_single_empty_marks_degraded():
    """One empty run is enough to flip a source to 'degraded'."""
    tracker = SourceHealthTracker()
    tracker.record("indeed", outcome="ok", job_count=5)
    rec = tracker.record("indeed", outcome="empty")
    assert rec.status() == "degraded"
    assert rec.consecutive_failures == 1


def test_three_failures_mark_down():
    tracker = SourceHealthTracker()
    tracker.record("glassdoor", outcome="error", error="403 Forbidden")
    tracker.record("glassdoor", outcome="error", error="403 Forbidden")
    rec = tracker.record("glassdoor", outcome="empty")
    assert rec.status() == "down"
    assert rec.consecutive_failures == 3
    # Error string from the most recent *error* is retained even after
    # the trailing "empty" run.
    assert rec.last_error == "403 Forbidden"


def test_recovery_resets_consecutive_failures():
    tracker = SourceHealthTracker()
    tracker.record("indeed", outcome="error", error="timeout")
    tracker.record("indeed", outcome="error", error="timeout")
    rec = tracker.record("indeed", outcome="ok", job_count=3)
    assert rec.status() == "healthy"
    assert rec.consecutive_failures == 0
    assert rec.last_error is None


def test_history_window_caps_at_five():
    tracker = SourceHealthTracker()
    for _ in range(10):
        tracker.record("linkedin", outcome="ok", job_count=1)
    rec = tracker.get("linkedin")
    assert rec is not None
    assert len(rec.history) == 5
    assert all(h == "ok" for h in rec.history)


def test_snapshot_is_json_ready_and_sorted():
    tracker = SourceHealthTracker()
    tracker.record("zzz", outcome="ok", job_count=1)
    tracker.record("aaa", outcome="error", error="boom")
    snap = tracker.snapshot()
    assert [s["source"] for s in snap] == ["aaa", "zzz"]
    # Spot-check serialisability — all keys are plain JSON types.
    import json
    assert json.dumps(snap)


def test_long_error_message_is_truncated():
    tracker = SourceHealthTracker()
    rec = tracker.record("indeed", outcome="error", error="x" * 1000)
    assert rec.last_error is not None
    assert len(rec.last_error) <= 500


def test_reset_clears_all():
    tracker = SourceHealthTracker()
    tracker.record("linkedin", outcome="ok", job_count=1)
    tracker.record("indeed", outcome="error", error="boom")
    tracker.reset()
    assert tracker.snapshot() == []


def test_reset_specific_sources():
    tracker = SourceHealthTracker()
    tracker.record("linkedin", outcome="ok", job_count=1)
    tracker.record("indeed", outcome="error", error="boom")
    tracker.reset(["linkedin"])
    snap = tracker.snapshot()
    assert len(snap) == 1
    assert snap[0]["source"] == "indeed"
