"""Fixes from the whole-branch review (C1/C2, I1–I11, M1)."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ── C1 / I11: restricted agents can be built; navigation pinned; no downloads ──


def test_restricted_agent_can_be_constructed_with_a_url_in_its_task():
    from browser_use import Agent
    from browser_use.llm import ChatOpenAI

    from backend.applier._strategy_common import restricted_tools

    for allow_upload in (True, False):
        agent = Agent(
            task="Fill the form.\nURL: https://boards.greenhouse.io/acme/jobs/1\n",
            llm=ChatOpenAI(model="gpt-4o-mini", api_key="x"),
            tools=restricted_tools(allow_upload=allow_upload),
        )
        assert agent is not None


def test_apply_browser_is_pinned_to_the_apply_site_and_cannot_download():
    import backend.applier._strategy_common as common

    captured = {}
    with patch.object(common, "_Browser", side_effect=lambda **kw: captured.update(kw) or object()):
        common.build_browser({}, None, apply_url="https://boards.greenhouse.io/acme/jobs/1")
    assert "boards.greenhouse.io" in captured["allowed_domains"]
    assert "*.greenhouse.io" in captured["allowed_domains"]
    assert captured["accept_downloads"] is False and captured["auto_download_pdfs"] is False


# ── C2: no replay of a possibly-submitted or prepared job ────────────────────


async def _seed_match(status="new", app_status=None, method="auto"):
    import hashlib

    from backend.database import AsyncSessionLocal
    from backend.models.application import Application
    from backend.models.job import Job, JobMatch

    tag = uuid.uuid4().hex[:8]
    company, title = f"R{tag}", "Engineer"
    async with AsyncSessionLocal() as db:
        job = Job(
            title=title, company=company, location="", url=f"https://r/{tag}",
            dedup_hash=hashlib.md5(f"{company}|{title}|".lower().encode()).hexdigest(),
        )
        db.add(job)
        await db.flush()
        m = JobMatch(job_id=job.id, score=90.0, status=status)
        db.add(m)
        await db.flush()
        if app_status:
            db.add(Application(job_match_id=m.id, method=method, status=app_status))
        await db.commit()
        return m.id, company, title


@pytest.mark.parametrize("match_status", ["applying", "applied", "skipped"])
def test_batch_does_not_requeue_an_actioned_job(match_status):
    from backend.database import AsyncSessionLocal
    from backend.matching.filters import JobFilters
    from backend.matching.matcher import JobMatcher
    from backend.models.schemas import JobDetails
    from backend.scheduler.batch_runner import BatchRunner

    async def go():
        _mid, company, title = await _seed_match(status=match_status)
        runner = BatchRunner(scraper=None, matcher=JobMatcher(), cv_pipeline=None, db_factory=AsyncSessionLocal)
        async with AsyncSessionLocal() as db:
            return await runner._store_matches(db, [(JobDetails(title=title, company=company), 90.0)], JobFilters())

    assert asyncio.run(go()) == []


@pytest.mark.parametrize("app_status", ["needs_verification", "applied"])
def test_apply_refuses_replay_of_a_possible_or_confirmed_submission(test_app, app_status):
    from unittest.mock import MagicMock

    from backend.applier.engine import ApplicationEngine

    mid, *_ = asyncio.run(_seed_match(status="applying", app_status=app_status))
    engine = MagicMock(spec=ApplicationEngine)
    engine.apply = AsyncMock()
    test_app.app.state.apply_engine = engine

    resp = test_app.post(f"/api/applications/{mid}/apply", json={"method": "manual"})

    assert resp.status_code == 409, resp.text
    engine.apply.assert_not_awaited()


# ── I1: same-origin commands work on any port ────────────────────────────────


def test_same_origin_command_on_a_custom_port_is_allowed(test_app):
    h = {"Host": "localhost:9000", "Origin": "http://localhost:9000"}
    assert test_app.post("/api/applications", json={"method": "manual"}, headers=h).status_code == 201
    evil = {"Host": "localhost:9000", "Origin": "http://localhost:9001"}
    assert test_app.post("/api/applications", json={"method": "manual"}, headers=evil).status_code == 403


# ── I2: an exception from the submit click itself is a possible submission ───


def test_click_exception_is_needs_verification_not_a_tier2_fallback():
    from backend.applier.form_filler import PlaywrightFormFiller

    class Page:
        url = "https://jobs.example.com/apply"

        async def click(self, *_a, **_k):
            raise RuntimeError("Target page, context or browser has been closed")

    out = asyncio.run(PlaywrightFormFiller(llm_client=AsyncMock())._submit_and_verify(Page(), {}, job_id=1))
    assert out["status"] == "needs_verification"


# ── I4: ambiguous pages are never "rejected" ─────────────────────────────────


def test_confirmation_wins_over_unrelated_invalid_forms():
    from backend.applier.verification import classify_page

    check = classify_page(text="Thanks for applying!", invalid_count=1, url="u", before_url="u")
    assert check.outcome == "confirmed"


@pytest.mark.parametrize(
    ("text", "invalid", "before"),
    [
        ("Fields marked * are required. Apply now", 0, "u"),  # boilerplate, no invalid field
        ("Apply now", 2, "https://other/after"),  # navigated: the old form's state is unknown
    ],
)
def test_ambiguous_pages_need_verification(text, invalid, before):
    from backend.applier.verification import classify_page

    assert classify_page(text=text, invalid_count=invalid, url="u", before_url=before).outcome == "unknown"


def test_invalid_fields_on_the_unchanged_page_reject():
    from backend.applier.verification import classify_page

    assert classify_page(text="Apply", invalid_count=1, url="u", before_url="u").outcome == "rejected"


# ── I5: a failed local write after a possible submission keeps the slot ──────


def test_local_write_failure_after_submit_keeps_the_reservation():
    from backend.applier.engine import ApplicationEngine, ApplyMode
    from backend.applier.manual_apply import ApplicationResult

    engine = ApplicationEngine(daily_limit=10)
    engine._recorder.record = AsyncMock(side_effect=RuntimeError("disk full"))
    engine._recorder.release_reserved_slot = AsyncMock()
    engine._dispatch = AsyncMock(return_value=ApplicationResult(status="confirmed", method="auto"))

    async def go():
        with patch("backend.applier.engine.DailyLimitGuard.reserve_slot", new=AsyncMock(return_value=99)):
            return await engine.apply(job_match_id=1, mode=ApplyMode.AUTO, db=AsyncMock(), apply_url="https://x/y")

    result = asyncio.run(go())
    assert result.status == "needs_verification"
    engine._recorder.release_reserved_slot.assert_not_awaited()


# ── M1 (re-graded Important): an approval during the broadcast is not lost ──


def test_approval_arriving_while_the_review_is_being_published_is_kept():
    from backend.applier.engine import ApplicationEngine
    from backend.applier.form_filler import PlaywrightFormFiller

    from tests.test_submission_boundary import JOB, FakePage

    engine = ApplicationEngine(daily_limit=10)
    confirm, cancel = asyncio.Event(), asyncio.Event()
    engine._confirm_events[JOB], engine._cancel_events[JOB] = confirm, cancel

    def record_and_approve(job_id, **kw):  # the user approves before the broadcast returns
        snap = engine.record_pending_review(job_id, **kw)
        engine.decide(job_id, review_id=snap["review_id"], revision=snap["revision"], decision="approve",
                      fields={}, idempotency_key="fast-click")
        return snap

    filler = PlaywrightFormFiller(llm_client=AsyncMock(), on_review=record_and_approve, on_get_patches=engine.get_pending_patches)
    page = FakePage({"#n": "A"})

    async def go():
        return await asyncio.wait_for(
            filler._review_and_submit(page, job_id=JOB, mapping={"submit_selector": "#go"}, filled_fields={"#n": "A"},
                                      documents=[], confirm_event=confirm, cancel_event=cancel),
            timeout=2,
        )

    assert asyncio.run(go())["status"] == "confirmed"


# ── I3: a preparation agent that raises may still have submitted ─────────────


def _confirmation_browser():
    page = type("P", (), {
        "evaluate": AsyncMock(return_value=json.dumps({"url": "u", "invalid": 0, "text": "Thank you for applying"})),
        "screenshot": AsyncMock(return_value=None),
    })()
    return type("B", (), {
        "get_current_page": AsyncMock(return_value=page),
        "get_current_page_url": AsyncMock(return_value="https://jobs.example.com/apply"),
        "stop": AsyncMock(),
    })()


class _CrashingAgent:
    def __init__(self, **_kw):
        pass

    async def run(self):
        raise RuntimeError("LLM connection reset")


@pytest.mark.parametrize("module", ["auto_apply", "assisted_apply"])
def test_preparation_agent_crash_after_a_submit_is_needs_verification(monkeypatch, module):
    import importlib

    mod = importlib.import_module(f"backend.applier.{module}")
    monkeypatch.setattr(mod, "Agent", _CrashingAgent)
    monkeypatch.setattr(mod, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(mod, "build_browser", lambda *a, **k: _confirmation_browser())
    monkeypatch.setattr("backend.llm.factory.make_browser_llm", lambda: object())
    monkeypatch.setattr("backend.config.settings.APPLY_TIER1_ENABLED", False)
    if module == "auto_apply":
        result = asyncio.run(mod.AutoApplyStrategy()._browser_use_apply(job_id=5, apply_url="https://jobs.example.com/apply"))
    else:
        with patch("backend.security.sanitizer.sanitize_url", side_effect=lambda u: u):
            result = asyncio.run(mod.AssistedApplyStrategy().apply(apply_url="https://jobs.example.com/apply"))
    assert result.status == "needs_verification"


# ── I7: Today uses the same quota and the real "to submit" statuses ──────────


def test_today_page_agrees_with_the_quota_guard_and_lists_unsubmitted_work(test_app):
    from backend.applier.daily_limit import DailyLimitGuard
    from backend.database import AsyncSessionLocal

    asyncio.run(_seed_match(status="applying", app_status="awaiting_submission", method="manual"))

    async def guard():
        async with AsyncSessionLocal() as db:
            g = await DailyLimitGuard.from_settings(db)
            return g.limit, await g.used_today()

    body = test_app.get("/api/today").json()
    limit, used = asyncio.run(guard())
    assert (body["week_stats"]["daily_limit_total"], body["week_stats"]["daily_limit_used"]) == (limit, used)
    kinds = {a["kind"]: a["count"] for a in body["blocked_actions"]["actions"]}
    assert kinds.get("pending_application", 0) >= 1
