"""W06 / F02, F12 — preparation is not submission; submission needs evidence."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.applier.verification import classify_page, verify_page

# ── Destination evidence classification ─────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Thank you for applying! We will be in touch.",
        "Your application has been submitted.",
        "We have received your application for Backend Engineer",
        "Merci pour votre candidature, elle a bien été envoyée.",
    ],
)
def test_confirmation_text_confirms(text):
    check = classify_page(text=text, invalid_count=0, url="https://jobs.example.com/done")
    assert check.outcome == "confirmed"
    assert "https://jobs.example.com/done" in check.evidence


def test_invalid_fields_mean_the_site_rejected_the_submission():
    # Review I4: invalid fields on the unchanged page reject; confirmation still wins.
    assert classify_page(text="Apply", invalid_count=2, url="u", before_url="u").outcome == "rejected"
    assert classify_page(text="Thank you for applying", invalid_count=2, url="u", before_url="u").outcome == "confirmed"


def test_validation_error_text_alone_is_unknown():
    # Review I4: validation-looking text alone is not proof nothing was sent.
    assert classify_page(text="Email: this field is required", invalid_count=0, url="u").outcome == "unknown"


def test_a_page_without_evidence_is_unknown():
    assert (
        classify_page(text="Step 2 of 3 — Experience", invalid_count=0, url="u").outcome
        == "unknown"
    )


def test_unreadable_page_is_unknown_not_confirmed():
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError("target closed")))
    check = asyncio.run(verify_page(page))
    assert check.outcome == "unknown" and "target closed" in check.evidence


def test_verify_page_parses_probe_result():
    probe = json.dumps({"url": "https://x/ok", "invalid": 0, "text": "Application received"})
    page = SimpleNamespace(evaluate=AsyncMock(return_value=probe))
    assert asyncio.run(verify_page(page)).outcome == "confirmed"


# ── Tier 1: click → verify, and no Tier-2 replay after a possible submit ────


class _FakePage:
    def __init__(self, probe: dict, *, pre_click_text: str | None = None):
        self.clicked: list[str] = []
        self._pre_click_text = pre_click_text
        self._probe = probe
        self.url = probe.get("url", "u")

    async def click(self, sel, timeout=None):  # noqa: ARG002
        self.clicked.append(sel)

    async def wait_for_load_state(self, *_a, **_kw):
        return None

    async def evaluate(self, _js):
        if self._pre_click_text is not None and not self.clicked:
            return json.dumps({"url": self.url, "invalid": 0, "text": self._pre_click_text})
        return json.dumps(self._probe)


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        ({"url": "u", "invalid": 0, "text": "Thanks for applying"}, "confirmed"),
        # Post-click: a validation failure is still a possible submission (slot kept).
        ({"url": "u", "invalid": 1, "text": "Apply"}, "needs_verification"),
        ({"url": "u", "invalid": 0, "text": "Review your answers"}, "needs_verification"),
    ],
)
def test_tier1_submit_returns_verified_outcome(probe, expected):
    from backend.applier.form_filler import PlaywrightFormFiller

    filler = PlaywrightFormFiller(llm_client=AsyncMock())
    page = _FakePage(probe, pre_click_text="Apply")
    out = asyncio.run(filler._submit_and_verify(page, {"submit_selector": "#go"}, job_id=1))
    assert page.clicked == ["#go"]
    assert out["status"] == expected


@pytest.mark.parametrize(
    ("tier1_status", "expected"),
    [
        ("confirmed", "confirmed"),
        ("needs_verification", "needs_verification"),
        ("rejected", "failed"),
    ],
)
def test_tier1_outcome_after_click_never_falls_back_to_tier2(tier1_status, expected):
    from backend.applier.auto_apply import AutoApplyStrategy

    strategy = AutoApplyStrategy()
    t1 = {"status": tier1_status, "evidence": "e", "filled_fields": {}, "screenshot_b64": None}
    with (
        patch("backend.security.sanitizer.sanitize_url", side_effect=lambda u: u),
        patch.object(strategy._form_filler, "fill_and_submit", new=AsyncMock(return_value=t1)),
        patch.object(strategy, "_browser_use_apply", new=AsyncMock()) as t2,
    ):
        result = asyncio.run(strategy.apply(job_id=1, apply_url="https://example.com/job"))
    t2.assert_not_awaited()
    assert result.status == expected


# ── Tier 2: agent completion alone is not proof ─────────────────────────────


def _history(successful):
    return SimpleNamespace(is_successful=lambda: successful, final_result=lambda: "Clicked submit")


def test_tier2_agent_success_without_destination_evidence_needs_verification():
    from backend.applier.auto_apply import outcome_after_agent

    browser = SimpleNamespace(
        get_current_page=AsyncMock(
            return_value=_FakePage({"url": "u", "invalid": 0, "text": "Done"})
        )
    )
    result = asyncio.run(outcome_after_agent(browser, _history(True), before_text="Apply"))
    assert result.status == "needs_verification"


def test_tier2_destination_confirmation_confirms():
    from backend.applier.auto_apply import outcome_after_agent

    page = _FakePage(
        {"url": "u", "invalid": 0, "text": "Your application was submitted successfully"}
    )
    browser = SimpleNamespace(get_current_page=AsyncMock(return_value=page))
    result = asyncio.run(outcome_after_agent(browser, _history(None), before_text="Apply"))
    assert result.status == "confirmed" and result.evidence


def test_tier2_submit_exception_is_not_reported_as_cancelled():
    from backend.applier.auto_apply import outcome_after_agent_error

    result = outcome_after_agent_error(RuntimeError("navigation timeout"))
    assert result.status == "needs_verification"


# ── Manual / assisted preparation ───────────────────────────────────────────


def test_manual_preparation_awaits_user_and_reports_unopened_browser(tmp_path, monkeypatch):
    from backend.applier.manual_apply import ManualApplyStrategy

    monkeypatch.setattr("backend.applier.manual_apply.Path.home", lambda: tmp_path)
    with patch("backend.applier.manual_apply.webbrowser.open", return_value=False):
        result = asyncio.run(ManualApplyStrategy().apply(apply_url="https://jobs.example.com/7"))
    assert result.status == "awaiting_user_submission"
    assert result.next_action_url == "https://jobs.example.com/7"
    assert "Could not open" in result.message


# ── Persistence: only confirmed submissions are applications ────────────────


async def _match() -> int:
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job, JobMatch

    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        job = Job(title="E", company=f"C{tag}", url=f"https://e/{tag}", dedup_hash=f"out-{tag}")
        db.add(job)
        await db.flush()
        m = JobMatch(job_id=job.id, score=70.0, status="new")
        db.add(m)
        await db.commit()
        return m.id


async def _record(status: str, method: str = "assisted", evidence: str = "") -> tuple:
    from sqlalchemy import select

    from backend.applier.recorder import ApplicationRecorder
    from backend.database import AsyncSessionLocal
    from backend.models.application import Application
    from backend.models.job import JobMatch

    mid = await _match()
    async with AsyncSessionLocal() as db:
        await ApplicationRecorder().record(
            db=db,
            job_match_id=mid,
            result_status=status,
            result_method=method,
            result_message="m",
            evidence=evidence,
        )
    async with AsyncSessionLocal() as db:
        app = (
            await db.execute(select(Application).where(Application.job_match_id == mid))
        ).scalar_one()
        match = await db.get(JobMatch, mid)
        return app, match


def test_prepared_application_is_not_applied():
    app, match = asyncio.run(_record("awaiting_user_submission"))
    assert app.status == "awaiting_submission" and app.applied_at is None
    assert match.status == "applying"


def test_confirmed_submission_records_destination_evidence():
    app, match = asyncio.run(
        _record("confirmed", method="auto", evidence="Thank you for applying @ u")
    )
    assert app.status == "applied" and app.applied_at is not None
    assert app.confirmation_source == "destination"
    assert "Thank you" in app.confirmation_evidence
    assert match.status == "applied"


def test_possible_submission_is_kept_uncertain():
    app, match = asyncio.run(_record("needs_verification", method="auto"))
    assert app.status == "needs_verification" and app.applied_at is None
    assert match.status == "applying"


def test_user_confirmation_is_a_distinct_evidence_source(test_app):
    app, _ = asyncio.run(_record("awaiting_user_submission", method="manual"))

    resp = test_app.post(f"/api/applications/{app.id}/confirm-submitted")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "applied" and body["confirmation_source"] == "user"
    assert body["applied_at"] is not None
    # Confirming twice (or confirming an applied row) is a conflict.
    assert test_app.post(f"/api/applications/{app.id}/confirm-submitted").status_code == 409


# ── Second review R07: failure or stale text must not read as confirmation ──


@pytest.mark.parametrize(
    "text",
    [
        "Your application was not successfully submitted. Please try again.",
        "Sorry, your application could not be submitted.",
        "We were unable to confirm: application not received.",
        "Votre candidature n'a pas été envoyée. Veuillez réessayer.",
        "Thank you for applying! An error occurred while uploading your CV.",
    ],
)
def test_negated_or_conflicting_confirmation_is_unknown(text):
    assert classify_page(text=text, invalid_count=0, url="u").outcome == "unknown"


def test_confirmation_already_on_the_page_before_submit_is_unknown():
    text = "Thank you for applying to Acme. Fill in the form below."
    check = classify_page(text=text, invalid_count=0, url="u", before_text=text)
    assert check.outcome == "unknown" and "before submit" in check.evidence


def test_new_confirmation_after_submit_still_confirms():
    check = classify_page(
        text="Thank you for applying!", invalid_count=0, url="u", before_text="Apply to Acme"
    )
    assert check.outcome == "confirmed"


def test_tier1_passes_the_pre_click_text_to_the_verifier():
    from backend.applier.form_filler import PlaywrightFormFiller

    class Page:
        url = "https://jobs.example.com/apply"

        async def evaluate(self, _js):  # same text before and after the click
            return json.dumps({"url": self.url, "invalid": 0, "text": "Thank you for applying"})

        async def click(self, *_a, **_kw):
            return None

        async def wait_for_load_state(self, *_a, **_kw):
            return None

    filler = PlaywrightFormFiller(llm_client=AsyncMock())
    result = asyncio.run(filler._submit_and_verify(Page(), {"submit_selector": "#go"}, 1))
    assert result["status"] == "needs_verification"
