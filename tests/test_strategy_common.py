"""Tests for the shared Tier-2 apply-strategy helpers (M1-T6).

These pin the de-duplicated helpers extracted into
``backend.applier._strategy_common`` and assert that both Tier-2 apply
strategies reference the shared implementations rather than carrying
literal copies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.applier import _strategy_common
from backend.applier import auto_apply, assisted_apply

_STRATEGY_FILES = [
    Path(auto_apply.__file__),
    Path(assisted_apply.__file__),
]


# ── site_profile_key ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/jobs/view/123", "linkedin_com"),
        ("https://linkedin.com/jobs/view/123", "linkedin_com"),
        ("https://jobs.indeed.com/x", "jobs_indeed_com"),
        ("not a url", "unknown"),
    ],
)
def test_site_profile_key(url: str, expected: str) -> None:
    assert _strategy_common.site_profile_key(url) == expected


def test_site_profile_key_is_canonical() -> None:
    # The shared helper must be the canonical captcha_handler implementation,
    # not a re-defined copy.
    from backend.applier.captcha_handler import site_profile_key as canonical

    assert _strategy_common.site_profile_key is canonical


# ── is_multi_step_site ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/jobs/view/123", True),
        ("https://linkedin.com/jobs/view/123", True),
        ("https://www.indeed.com/job/1", False),
        ("https://example.com/apply", False),
        ("", False),
    ],
)
def test_is_multi_step_site(url: str, expected: bool) -> None:
    assert _strategy_common.is_multi_step_site(url) is expected


# ── build_browser ────────────────────────────────────────────────────────────
def test_build_browser_loads_saved_session(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class _FakeBrowser:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(_strategy_common, "_Browser", _FakeBrowser)

    state = tmp_path / "state.json"
    state.write_text("{}")

    base_kwargs: dict = dict(headless=False, keep_alive=True)
    browser = _strategy_common.build_browser(base_kwargs, state)

    assert isinstance(browser, _FakeBrowser)
    assert captured["storage_state"] == state.resolve().as_posix()
    assert captured["user_data_dir"] is None
    # Preserved base kwargs
    assert captured["headless"] is False
    assert captured["keep_alive"] is True


def test_build_browser_no_session(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class _FakeBrowser:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(_strategy_common, "_Browser", _FakeBrowser)

    missing = tmp_path / "nope.json"
    base_kwargs: dict = dict(headless=False, disable_security=True)
    _strategy_common.build_browser(base_kwargs, missing)

    assert "storage_state" not in captured
    assert "user_data_dir" not in captured
    assert captured["disable_security"] is True


# ── PHONE_NUMBER_NOTE constant ───────────────────────────────────────────────
def test_phone_number_note_constant() -> None:
    # The identical phone-prefix guidance must live once in the shared module.
    assert "+33+33612345678" in _strategy_common.PHONE_NUMBER_NOTE
    assert "country code" in _strategy_common.PHONE_NUMBER_NOTE


def test_both_strategies_embed_phone_note() -> None:
    auto = auto_apply.AutoApplyStrategy.__new__(auto_apply.AutoApplyStrategy)
    assisted = assisted_apply.AssistedApplyStrategy.__new__(
        assisted_apply.AssistedApplyStrategy
    )

    auto_task = auto_apply.AutoApplyStrategy._build_fill_task(
        auto,
        apply_url="https://example.com/job",
        full_name="Jane Doe",
        email="jane@example.com",
        phone="0612345678",
        location="Paris",
        additional_answers="",
        cv_pdf=None,
        letter_pdf=None,
    )
    assisted_task = assisted_apply.AssistedApplyStrategy._build_fill_task(
        assisted,
        apply_url="https://example.com/job",
        full_name="Jane Doe",
        email="jane@example.com",
        phone="0612345678",
        location="Paris",
        additional_answers="",
        cv_pdf=None,
        letter_pdf=None,
    )

    assert _strategy_common.PHONE_NUMBER_NOTE in auto_task
    assert _strategy_common.PHONE_NUMBER_NOTE in assisted_task


# ── no duplicate private helper definitions remain ───────────────────────────
def test_no_duplicate_helper_defs_in_strategy_files() -> None:
    for path in _STRATEGY_FILES:
        src = path.read_text()
        assert "def _is_multi_step_site" not in src, (
            f"{path.name} still defines _is_multi_step_site locally"
        )
        assert "def _site_key" not in src, (
            f"{path.name} still defines _site_key locally"
        )


def test_strategy_files_import_shared_module() -> None:
    for path in _STRATEGY_FILES:
        src = path.read_text()
        assert "_strategy_common" in src, (
            f"{path.name} does not reference _strategy_common"
        )


def test_strategies_reference_shared_is_multi_step_site() -> None:
    assert auto_apply.is_multi_step_site is _strategy_common.is_multi_step_site
    assert assisted_apply.is_multi_step_site is _strategy_common.is_multi_step_site
