"""Phase 4 — apply-engine safety: the reproduced double-apply / false-"applied" bugs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.applier.engine import ApplicationEngine, ApplyMode
from backend.applier.manual_apply import ApplicationResult
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.application import Application, ApplicationEvent
from backend.models.job import Job, JobMatch
from backend.utils.time import naive_utc_now

URL = "https://jobs.example.com/apply"


async def _seed_job(matches: int = 1) -> list[int]:
    """One job reachable through *matches* match ids."""
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        job = Job(
            title="Engineer",
            company=f"S{tag}",
            location="",
            url=f"https://s/{tag}",
            dedup_hash=hashlib.md5(tag.encode()).hexdigest(),
        )
        db.add(job)
        await db.flush()
        rows = [
            JobMatch(job_id=job.id, score=90.0, status="new", batch_date=date(2026, 1, 1 + i))
            for i in range(matches)
        ]
        db.add_all(rows)
        await db.commit()
        return [m.id for m in rows]


async def _applications(match_id: int) -> list[Application]:
    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(select(Application).where(Application.job_match_id == match_id))
            ).scalars()
        )


class _Page:
    """A page whose probe result is ``before`` until ``clicked``, then ``after``."""

    url = URL

    def __init__(self, before: dict | None, after: dict):
        self.before, self.after, self.clicked = before, after, False

    async def click(self, *_a, **_k):
        self.clicked = True

    async def wait_for_load_state(self, *_a, **_k):
        return None

    async def screenshot(self, **_k):
        return b"png"

    async def evaluate(self, _js):
        probe = self.after if self.clicked else self.before
        if probe is None:
            raise RuntimeError("page not readable")
        return json.dumps({"url": URL, "invalid": 0, **probe})


def _browser(page) -> SimpleNamespace:
    return SimpleNamespace(
        get_current_page=AsyncMock(return_value=page),
        get_current_page_url=AsyncMock(return_value=URL),
        stop=AsyncMock(),
    )


def _history(done: bool = True):
    return SimpleNamespace(
        is_done=lambda: done, is_successful=lambda: done, final_result=lambda: ""
    )


def _tier2(monkeypatch, agent_cls, browser) -> None:
    import backend.applier.auto_apply as auto

    monkeypatch.setattr(auto, "Agent", agent_cls)
    monkeypatch.setattr(auto, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(auto, "build_browser", lambda *a, **k: browser)
    monkeypatch.setattr(auto, "restricted_tools", lambda **_k: None)
    monkeypatch.setattr("backend.applier._strategy_common.restricted_tools", lambda **_k: None)
    monkeypatch.setattr("backend.llm.factory.make_browser_llm", lambda: object())


# ── 1. a post-click outcome never releases the slot ──────────────────────────


async def test_post_click_validation_failure_keeps_the_reservation():
    """Tier 1 clicked submit and the site showed invalid fields: the attempt
    must stay a possible submission (slot kept, replay blocked), not 'failed'."""
    from backend.applier.form_filler import PlaywrightFormFiller

    (match_id,) = await _seed_job()
    engine = ApplicationEngine(daily_limit=10)
    page = _Page({"text": "Apply"}, {"text": "Apply", "invalid": 2})

    async def tier1(**_kw):
        return await PlaywrightFormFiller(llm_client=AsyncMock())._submit_and_verify(
            page, {}, job_id=match_id
        )

    engine._auto._form_filler = SimpleNamespace(fill_and_submit=tier1)
    async with AsyncSessionLocal() as db:
        result = await engine.apply(
            job_match_id=match_id, mode=ApplyMode.AUTO, db=db, apply_url=URL
        )

    assert page.clicked and result.status == "needs_verification"
    (app,) = await _applications(match_id)
    assert app.status == "needs_verification" and app.quota_reserved_at is not None


async def test_tier2_post_click_validation_failure_is_not_failed():
    from backend.applier.auto_apply import outcome_after_agent

    page = _Page(None, {"text": "This field is required", "invalid": 1})
    page.clicked = True
    result = await outcome_after_agent(_browser(page), _history(), before_text="Apply")
    assert result.status == "needs_verification"


# ── 2. the in-flight guard is keyed on the job ───────────────────────────────


async def test_same_job_through_two_match_ids_is_applied_once():
    first, second = await _seed_job(matches=2)
    engine = ApplicationEngine(daily_limit=10)
    started, release = asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    async def slow_apply(*, job_id, **_kw):
        calls.append(job_id)
        started.set()
        await release.wait()
        return ApplicationResult(status="needs_verification", method="auto")

    engine._auto.apply = slow_apply  # type: ignore[method-assign]

    async def run(match_id):
        async with AsyncSessionLocal() as db:
            return await engine.apply(
                job_match_id=match_id, mode=ApplyMode.AUTO, db=db, apply_url=URL
            )

    running = asyncio.create_task(run(first))
    await asyncio.wait_for(started.wait(), 5)
    duplicate = await asyncio.wait_for(run(second), 5)
    release.set()
    await running

    assert calls == [first]
    assert duplicate.status == "cancelled" and "in progress" in duplicate.message
    (released,) = await _applications(second)
    assert released.status == "cancelled" and released.quota_reserved_at is None
    assert engine._in_flight_jobs == set()  # the job can be applied to again later


# ── 3. startup sweep of stale ``pending`` ────────────────────────────────────


async def _seed_stale_rows() -> tuple[int, int, int]:
    (match_id,) = await _seed_job()
    async with AsyncSessionLocal() as db:
        rows = [
            Application(
                job_match_id=match_id,
                method="auto",
                status="pending",
                quota_reserved_at=naive_utc_now(),
            ),
            Application(method="manual", status="pending"),  # tracker entry, no reservation
        ]
        db.add_all(rows)
        await db.commit()
        return match_id, rows[0].id, rows[1].id


async def _status(app_id: int) -> str:
    async with AsyncSessionLocal() as db:
        return (await db.get(Application, app_id)).status


async def test_sweep_moves_stale_reservations_to_needs_verification():
    from backend.applier.recorder import sweep_stale_pending

    match_id, stale, tracker = await _seed_stale_rows()
    async with AsyncSessionLocal() as db:
        assert await sweep_stale_pending(db) == 1
        assert await sweep_stale_pending(db) == 0  # idempotent

    async with AsyncSessionLocal() as db:
        app = await db.get(Application, stale)
        assert app.status == "needs_verification" and app.applied_at is None
        assert app.quota_reserved_at is not None  # still holds its slot
        assert (await db.get(JobMatch, match_id)).status == "applying"
        events = (
            await db.execute(
                select(ApplicationEvent).where(ApplicationEvent.application_id == stale)
            )
        ).scalars()
        assert [e.event_type for e in events] == ["needs_verification"]
    assert await _status(tracker) == "pending"


def test_startup_runs_the_sweep_and_the_job_cannot_be_replayed(request):
    match_id, stale, _ = asyncio.run(_seed_stale_rows())
    client = request.getfixturevalue("test_app")  # runs the app lifespan
    assert asyncio.run(_status(stale)) == "needs_verification"
    resp = client.post(f"/api/applications/{match_id}/apply", json={"method": "manual"})
    assert resp.status_code == 409, resp.text


# ── 4. Tier 2 confirms only on text that is new after the click ──────────────


class _SubmitAgent:
    """Stands in for every browser-use Agent; the submit agent 'clicks'."""

    steps: list[int] = []
    page: _Page | None = None

    def __init__(self, *, task, **_kw):
        self.task = task

    async def run(self, max_steps=None):
        type(self).steps.append(max_steps)
        if "Click the final Submit" in self.task and self.page is not None:
            self.page.clicked = True
        return _history()


async def _approve_and_submit(monkeypatch, page, agent_cls=_SubmitAgent) -> ApplicationResult:
    import backend.applier.auto_apply as auto

    agent_cls.page, agent_cls.steps = page, []
    _tier2(monkeypatch, agent_cls, None)
    confirm, cancel = asyncio.Event(), asyncio.Event()
    strategy = auto.AutoApplyStrategy(on_review=lambda *_a, **_k: confirm.set())
    return await asyncio.wait_for(
        strategy._review_and_submit_tier2(
            _browser(page),
            job_id=1,
            filled_fields={},
            screenshot_b64=None,
            documents=[],
            confirm_event=confirm,
            cancel_event=cancel,
        ),
        timeout=5,
    )


async def test_tier2_confirmation_text_already_on_the_page_is_not_evidence(monkeypatch):
    stale = {"text": "Thank you for applying to our newsletter. Apply below."}
    result = await _approve_and_submit(monkeypatch, _Page(stale, stale))
    assert result.status == "needs_verification"


async def test_tier2_new_confirmation_text_confirms(monkeypatch):
    result = await _approve_and_submit(
        monkeypatch, _Page({"text": "Apply"}, {"text": "Thank you for applying"})
    )
    assert result.status == "confirmed"


async def test_tier2_without_a_baseline_never_confirms(monkeypatch):
    result = await _approve_and_submit(monkeypatch, _Page(None, {"text": "Thank you for applying"}))
    assert result.status == "needs_verification" and "baseline" in result.evidence


# ── 5. Tier 2 step cap and time cap ──────────────────────────────────────────


def test_tier2_caps_have_sane_defaults():
    from backend.config import Settings

    fields = Settings.model_fields
    assert fields["APPLY_TIER2_MAX_STEPS"].default == 25
    assert fields["APPLY_TIER2_TIMEOUT_SECONDS"].default == 300


async def test_tier2_agents_run_under_the_step_cap(monkeypatch):
    monkeypatch.setattr(settings, "APPLY_TIER2_MAX_STEPS", 7)
    await _approve_and_submit(monkeypatch, _Page({"text": "Apply"}, {"text": "Apply"}))
    assert _SubmitAgent.steps == [7]


async def test_tier2_fill_that_hits_the_step_cap_is_not_reviewed_or_submitted(monkeypatch):
    import backend.applier.auto_apply as auto

    class Unfinished(_SubmitAgent):
        async def run(self, max_steps=None):
            return _history(done=False)

    _tier2(monkeypatch, Unfinished, _browser(_Page({"text": "Apply"}, {"text": "Apply"})))
    cancel = asyncio.Event()
    review = MagicMock(side_effect=lambda *_a, **_k: cancel.set())  # a review would end, not hang
    result = await auto.AutoApplyStrategy(on_review=review)._browser_use_apply(
        job_id=1, apply_url=URL, cancel_event=cancel
    )
    assert result.status == "cancelled" and "steps" in result.message
    review.assert_not_called()


async def test_tier2_submit_past_the_time_cap_fails_closed(monkeypatch):
    class Hanging(_SubmitAgent):
        async def run(self, max_steps=None):
            if "Click the final Submit" in self.task:
                self.page.clicked = True
                await asyncio.sleep(60)
            return _history()

    monkeypatch.setattr(settings, "APPLY_TIER2_TIMEOUT_SECONDS", 0.05)
    page = _Page({"text": "Apply"}, {"text": "Thank you for applying"})
    result = await _approve_and_submit(monkeypatch, page, Hanging)
    assert result.status == "needs_verification"


async def test_assisted_agent_runs_under_the_caps(monkeypatch):
    import backend.applier.assisted_apply as assisted

    agent = MagicMock(run=AsyncMock(return_value=_history()))
    monkeypatch.setattr(assisted, "Agent", lambda **_k: agent)
    monkeypatch.setattr(assisted, "_BROWSER_USE_AVAILABLE", True)
    monkeypatch.setattr(
        assisted, "build_browser", lambda *a, **k: _browser(_Page({"text": "Apply"}, {}))
    )
    monkeypatch.setattr(assisted, "restricted_tools", lambda **_k: None)
    monkeypatch.setattr("backend.llm.factory.make_browser_llm", lambda: object())
    monkeypatch.setattr(settings, "APPLY_TIER1_ENABLED", False)
    await assisted.AssistedApplyStrategy().apply(apply_url=URL)
    agent.run.assert_awaited_once_with(max_steps=settings.APPLY_TIER2_MAX_STEPS)


# ── 6. Tier 1 never reviews or submits an empty mapping ──────────────────────


@pytest.mark.parametrize(
    "llm_answer", ["I could not find a form.", '{"fields": [], "submit_selector": "#go"}']
)
async def test_tier1_empty_mapping_raises_and_never_clicks(monkeypatch, llm_answer):
    from backend.applier.form_filler import PlaywrightFormFiller
    from backend.browser import chrome

    page = MagicMock(url=URL)
    for name in ("goto", "content", "evaluate", "fill", "click", "close", "screenshot"):
        setattr(page, name, AsyncMock(return_value=""))
    context = MagicMock(new_page=AsyncMock(return_value=page), close=AsyncMock())

    async def open_context(pw, profile_dir, args):
        return context, True

    monkeypatch.setattr(chrome, "open_context", open_context)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: MagicMock(start=AsyncMock(return_value=MagicMock(stop=AsyncMock()))),
    )
    monkeypatch.setattr(
        "backend.applier.captcha_handler.check_and_handle_captcha", AsyncMock(return_value=False)
    )
    review = MagicMock()
    filler = PlaywrightFormFiller(
        llm_client=MagicMock(generate_text=AsyncMock(return_value=llm_answer)), on_review=review
    )
    confirm = asyncio.Event()
    confirm.set()

    with pytest.raises(RuntimeError, match="nothing to submit"):
        await asyncio.wait_for(
            filler.fill_and_submit(apply_url=URL, job_id=1, confirm_event=confirm), timeout=5
        )
    page.click.assert_not_awaited()
    review.assert_not_called()
    context.close.assert_not_awaited()  # attached: only our own tab is closed
    page.close.assert_awaited_once()


# ── 7. the live browser handle is per run ────────────────────────────────────


async def test_concurrent_applies_do_not_share_a_browser_handle(monkeypatch):
    """Cancelling one apply must stop its own Tier-2 browser, not the other run's."""
    import backend.applier.auto_apply as auto

    browsers = {u: _browser(_Page({"text": "Apply"}, {})) for u in (f"{URL}/a", f"{URL}/b")}
    running = {u: asyncio.Event() for u in browsers}

    class Blocking:
        def __init__(self, *, browser, **_kw):
            self.url = next(u for u, b in browsers.items() if b is browser)

        async def run(self, max_steps=None):
            running[self.url].set()
            await asyncio.Event().wait()

    _tier2(monkeypatch, Blocking, None)
    monkeypatch.setattr(
        auto, "build_browser", lambda _kw, _state, *, apply_url: browsers[apply_url]
    )
    monkeypatch.setattr(settings, "APPLY_TIER1_ENABLED", False)
    engine = ApplicationEngine(daily_limit=10)

    def db():
        session = AsyncMock()
        session.add = MagicMock()
        session.execute.return_value.scalar_one_or_none = MagicMock(return_value=0)
        return session

    async def run(match_id, url):
        return await engine.apply(
            job_match_id=match_id, mode=ApplyMode.AUTO, db=db(), apply_url=url
        )

    first = asyncio.create_task(run(1, f"{URL}/a"))
    await asyncio.wait_for(running[f"{URL}/a"].wait(), 5)
    second = asyncio.create_task(run(2, f"{URL}/b"))
    await asyncio.wait_for(running[f"{URL}/b"].wait(), 5)

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    try:
        browsers[f"{URL}/a"].stop.assert_awaited_once()
        browsers[f"{URL}/b"].stop.assert_not_awaited()
    finally:
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
    browsers[f"{URL}/b"].stop.assert_awaited_once()


# ── 8. the Tier-1 prompt gets the form's fields, not page markdown ───────────


async def test_tier1_prompt_content_is_the_dom_field_inventory():
    from backend.applier.form_filler import PlaywrightFormFiller

    fields = [
        {"selector": "#email", "name": "email", "label": "Email", "type": "email", "required": True}
    ]
    page = MagicMock(evaluate=AsyncMock(return_value=json.dumps(fields)), content=AsyncMock())
    content = await PlaywrightFormFiller(llm_client=MagicMock())._form_content(page)
    assert '"selector": "#email"' in content and '"required": true' in content
    page.content.assert_not_awaited()


@pytest.mark.parametrize("probe", [RuntimeError("no page"), "[]"])
async def test_tier1_prompt_falls_back_to_markdown_without_an_inventory(probe):
    from backend.applier.form_filler import PlaywrightFormFiller

    page = MagicMock(
        evaluate=AsyncMock(side_effect=[probe]),
        content=AsyncMock(return_value="<form><label>Email</label><input name='email'/></form>"),
    )
    assert "Email" in await PlaywrightFormFiller(llm_client=MagicMock())._form_content(page)


async def test_automatic_applications_on_one_site_are_spaced(monkeypatch):
    from backend.applier.engine import ApplicationEngine
    from backend.config import settings

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(settings, "APPLY_MIN_GAP_SECONDS", 90.0)
    monkeypatch.setattr("backend.applier.engine.asyncio.sleep", sleep)
    engine = ApplicationEngine()
    await engine._pace("https://jobs.example.com/a")  # first one starts at once
    await engine._pace("https://other.example.org/b")  # another site is independent
    assert slept == []
    await engine._pace("https://jobs.example.com/c")
    assert len(slept) == 1 and 85 <= slept[0] <= 225
