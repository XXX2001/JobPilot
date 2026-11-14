"""W08 / F03 — only the approved, current, read-back state is ever submitted."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.applier.engine import ApplicationEngine
from backend.applier.form_filler import PlaywrightFormFiller

JOB = 77


class FakePage:
    """Tracks field values; a selector in ``stuck`` silently ignores fills."""

    def __init__(self, values: dict[str, str], *, stuck=(), url="https://jobs.example.com/apply"):
        self.values = dict(values)
        self.stuck = set(stuck)
        self.url = url
        self.clicks: list[str] = []
        self.invalid = 0

    async def fill(self, sel, val, timeout=None):  # noqa: ARG002
        if sel not in self.stuck:
            self.values[sel] = val

    async def input_value(self, sel, timeout=None):  # noqa: ARG002
        return self.values[sel]

    async def screenshot(self, **_kw):
        return b"png"

    async def click(self, sel, timeout=None):  # noqa: ARG002
        self.clicks.append(sel)

    async def wait_for_load_state(self, *_a, **_kw):
        return None

    async def evaluate(self, _js):
        text = "Thank you for applying" if self.clicks else "Apply now"
        return json.dumps({"url": self.url, "invalid": self.invalid, "text": text})


class User:
    """Answers each review round through the engine's real decision path."""

    def __init__(self, engine: ApplicationEngine):
        self.engine = engine
        self.snapshots: list[dict] = []
        self.answers: list = []  # each: ("approve", fields) | ("cancel", None) | callable(snapshot, page)

    async def run(self, page: FakePage):
        seen: set[str] = set()
        while True:
            await asyncio.sleep(0.001)
            snap = self.engine.get_pending_review(JOB)
            if snap is None or snap["review_id"] in seen:
                continue
            seen.add(snap["review_id"])
            self.snapshots.append(snap)
            answer = self.answers.pop(0) if self.answers else ("cancel", None)
            if callable(answer):
                answer = answer(snap, page)
            decision, fields = answer
            self.engine.decide(
                JOB, review_id=snap["review_id"], revision=snap["revision"], decision=decision,
                fields=fields or {}, idempotency_key=f"key-{snap['revision']}",
            )


async def _run(page: FakePage, answers: list, *, cv: Path | None = None, mutate_cv=False):
    engine = ApplicationEngine(daily_limit=10)
    confirm, cancel = asyncio.Event(), asyncio.Event()
    engine._confirm_events[JOB], engine._cancel_events[JOB] = confirm, cancel
    filler = PlaywrightFormFiller(
        llm_client=AsyncMock(),
        on_review=engine.record_pending_review,
        on_get_patches=engine.get_pending_patches,
    )
    user = User(engine)
    user.answers = list(answers)

    def on_first_review(snap, page_):
        if mutate_cv and cv is not None:
            cv.write_bytes(b"%PDF-1.4 SWAPPED")
        return ("approve", {})

    if mutate_cv:
        user.answers.insert(0, on_first_review)
    driver = asyncio.create_task(user.run(page))
    try:
        result = await asyncio.wait_for(
            filler._review_and_submit(
                page,
                job_id=JOB,
                mapping={"submit_selector": "#go"},
                filled_fields=dict(page.values),
                documents=[cv] if cv else [],
                confirm_event=confirm,
                cancel_event=cancel,
            ),
            timeout=5,
        )
    finally:
        driver.cancel()
    return result, user


def test_approval_without_changes_submits_once():
    page = FakePage({"#name": "Ada"})
    result, user = asyncio.run(_run(page, [("approve", {})]))
    assert page.clicks == ["#go"] and result["status"] == "confirmed"
    assert len(user.snapshots) == 1


def test_a_correction_is_applied_read_back_and_re_reviewed_before_submit():
    page = FakePage({"#name": "Ada"})
    result, user = asyncio.run(_run(page, [("approve", {"#name": "Ada L."}), ("approve", {})]))
    assert page.values["#name"] == "Ada L."
    assert len(user.snapshots) == 2
    assert (
        user.snapshots[1]["filled_fields"]["#name"] == "Ada L."
    )  # the new snapshot shows real values
    assert page.clicks == ["#go"]


def test_an_unfillable_correction_never_reaches_submit():
    page = FakePage({"#name": "Ada"}, stuck={"#name"})
    result, user = asyncio.run(_run(page, [("approve", {"#name": "Ada L."}), ("cancel", None)]))
    assert page.clicks == []
    assert result["status"] == "cancelled"
    assert (
        user.snapshots[1]["filled_fields"]["#name"] == "Ada"
    )  # the review shows what the page really has


def test_page_mutation_after_approval_returns_to_review():
    page = FakePage({"#name": "Ada"})

    def mutate_then_approve(snap, page_):
        page_.values["#name"] = "Mallory"  # the site rewrote the field after review
        return ("approve", {})

    result, user = asyncio.run(_run(page, [mutate_then_approve, ("cancel", None)]))
    assert page.clicks == [] and len(user.snapshots) == 2
    assert user.snapshots[1]["filled_fields"]["#name"] == "Mallory"


def test_destination_redirect_returns_to_review():
    page = FakePage({"#name": "Ada"})

    def redirect_then_approve(snap, page_):
        page_.url = "https://phish.example.net/apply"
        return ("approve", {})

    result, _ = asyncio.run(_run(page, [redirect_then_approve, ("cancel", None)]))
    assert page.clicks == []


def test_invalid_required_fields_return_to_review():
    page = FakePage({"#name": "Ada"})
    page.invalid = 1
    result, user = asyncio.run(_run(page, [("approve", {}), ("cancel", None)]))
    assert page.clicks == [] and len(user.snapshots) == 2


def test_a_changed_upload_fails_without_submitting(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 REVIEWED")
    page = FakePage({"#name": "Ada"})
    result, _ = asyncio.run(_run(page, [], cv=cv, mutate_cv=True))
    assert page.clicks == []
    assert result["status"] == "rejected" and "changed" in result["evidence"]


def test_preparation_agents_cannot_navigate_or_run_scripts():
    from backend.applier._strategy_common import restricted_tools

    fill = set(restricted_tools(allow_upload=True).registry.registry.actions)
    submit = set(restricted_tools(allow_upload=False).registry.registry.actions)
    for forbidden in ("search", "evaluate", "switch", "write_file"):
        assert forbidden not in fill and forbidden not in submit
    assert "upload_file" in fill and "upload_file" not in submit
    assert {"click", "input"} <= fill


@pytest.mark.parametrize("text", ["Thanks for applying!", "Application received"])
def test_fill_agent_that_already_submitted_is_detected(text):
    from backend.applier.auto_apply import submitted_during_preparation

    page = FakePage({})
    page.evaluate = AsyncMock(return_value=json.dumps({"url": "u", "invalid": 0, "text": text}))
    browser = type("B", (), {"get_current_page": AsyncMock(return_value=page)})()
    result = asyncio.run(submitted_during_preparation(browser))
    assert result is not None and result.status == "needs_verification"


def test_tier2_consumes_corrections_and_re_reviews_before_submitting(monkeypatch):
    import backend.applier.auto_apply as auto

    tasks: list[str] = []

    class FakeAgent:
        def __init__(self, *, task, tools=None, **_kw):
            tasks.append(task)
            assert tools is not None and "evaluate" not in tools.registry.registry.actions

        async def run(self, max_steps=None):
            return type("H", (), {"is_successful": lambda self: True, "final_result": lambda self: "ok"})()

    page = FakePage({})
    browser = type("B", (), {
        "get_current_page": AsyncMock(return_value=page),
        "get_current_page_url": AsyncMock(return_value="https://jobs.example.com/apply"),
    })()
    monkeypatch.setattr(auto, "Agent", FakeAgent)
    monkeypatch.setattr("backend.llm.factory.make_browser_llm", lambda: object())

    async def go():
        engine = ApplicationEngine(daily_limit=10)
        confirm, cancel = asyncio.Event(), asyncio.Event()
        engine._confirm_events[JOB], engine._cancel_events[JOB] = confirm, cancel
        strategy = auto.AutoApplyStrategy(on_review=engine.record_pending_review, on_get_patches=engine.get_pending_patches)
        user = User(engine)
        user.answers = [("approve", {"#email": "new@example.com"}), ("approve", {})]
        driver = asyncio.create_task(user.run(page))
        try:
            page.clicks.append("(agent)")  # FakePage shows confirmation only after a click
            page.clicks.clear()
            result = await asyncio.wait_for(
                strategy._review_and_submit_tier2(
                    browser, job_id=JOB, filled_fields={"#email": "old@example.com"}, screenshot_b64=None,
                    documents=[], confirm_event=confirm, cancel_event=cancel,
                ),
                timeout=5,
            )
        finally:
            driver.cancel()
        return result, user

    result, user = asyncio.run(go())
    assert len(user.snapshots) == 2
    assert user.snapshots[1]["filled_fields"]["#email"] == "new@example.com"
    assert "new@example.com" in tasks[0] and "Do NOT click Submit" in tasks[0]
    assert len(tasks) == 2 and "Click the final Submit" in tasks[1]
    # No confirmation text on the fake page → submission outcome is unverified, not applied.
    assert result.status == "needs_verification"
