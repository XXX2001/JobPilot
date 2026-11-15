"""Harness integration, with local Chromium pages and no model/network calls."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pydantic import ValidationError

from backend.applier.assisted_apply import AssistedApplyStrategy
from backend.applier.auto_apply import AutoApplyStrategy
from backend.applier.engine import ApplicantInfo, ApplicationEngine, ApplyMode
from backend.applier.harness import agent, runner
from backend.applier.harness.agent import HarnessBlocked, HarnessResult
from backend.applier.harness.decide import Decision, DecisionError
from backend.applier.manual_apply import ApplicationResult
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.defaults import MAX_LEN_LOCATION
from backend.models.job import Job, JobMatch
from backend.models.user import UserProfile

FIXTURES = Path(__file__).parent / "fixtures/harness"
URL = "https://jobs.example.com/apply"
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("backend.llm.factory.make_llm_client", Mock(return_value=AsyncMock()))
    monkeypatch.setattr(runner, "make_llm_client", Mock(return_value=AsyncMock()))
    monkeypatch.setattr("backend.api.ws.manager.broadcast", AsyncMock())
    monkeypatch.setattr("backend.monitor.notify.notify_text", AsyncMock())
    monkeypatch.setattr(settings, "DECISION_MODEL_URL", "http://model.invalid/v1/systemone")
    monkeypatch.setattr(settings, "APPLY_TIER1_ENABLED", True)
    monkeypatch.setattr(settings, "jobpilot_auto_submit_enabled", True)
    monkeypatch.setattr(
        runner,
        "SystemOneClient",
        Mock(
            side_effect=lambda *a, **k: SimpleNamespace(
                model="scripted",
                post=AsyncMock(side_effect=AssertionError("Unexpected model call")),
                aclose=AsyncMock(),
            )
        ),
    )


@pytest_asyncio.fixture(scope="module")
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture(loop_scope="module")
async def tab(browser, monkeypatch):
    context = await browser.new_context()
    page = await context.new_page()
    await context.route("http://**/*", lambda route: route.abort())
    await context.route("https://**/*", lambda route: route.abort())
    state = SimpleNamespace(page=page, attached=True, launches=[], clicks=[], closed=AsyncMock())
    await page.expose_function("recordSubmit", lambda: state.clicks.append(1))
    await page.add_init_script("""document.addEventListener('click', e => {
        if (e.target.id === 'submit') window.recordSubmit();
    }, true)""")
    state.pw = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=state.pw)),
    )

    async def open_context(pw, profile_dir, args):
        state.launches.append((profile_dir, args))
        state.context = SimpleNamespace(
            new_page=AsyncMock(return_value=page), close=state.closed
        )
        return state.context, state.attached

    monkeypatch.setattr("backend.browser.chrome.open_context", open_context)
    yield state
    await context.close()


def applicant():
    return ApplicantInfo(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="0123456789",
        location="Paris",
        linkedin_url="https://example.com/ada",
        additional_answers_json='{"country": "France"}',
    )


def ready(**kwargs):
    return HarnessResult("ready", None, {}, [], [], [], 0, **kwargs)


async def apply(tab, *, mode="assisted", fixture="greenhouse.html", **kwargs):
    async with asyncio.timeout(15):
        return await runner.harness_apply(
            mode=mode,
            apply_url=(FIXTURES / fixture).resolve().as_uri(),
            job_id=kwargs.pop("job_id", None),
            applicant=applicant(),
            cv_pdf=kwargs.pop("cv_pdf", None),
            letter_pdf=None,
            **kwargs,
        )


def script(monkeypatch, steps=None):
    steps = iter(
        steps
        or [
            ("TYPE_TEXT", label)
            for label in ("First Name", "Last Name", "Email", "Phone", "Location")
        ]
        + [("CLICK", "Submit Application")]
    )

    async def decide(client, snap, goal, history):
        operation, label = next(steps)
        if operation == "ERROR":
            raise HarnessBlocked("model unavailable")
        kind = {"TYPE_TEXT": "fill", "CLICK": "click"}.get(operation)
        action = next(
            (a for a in snap["actions"] if a["kind"] == kind and a["label"] == label), None
        )
        return Decision(operation, action, "application_form", 0.99, None, 0.99, 0.99, 0.99, 0)

    async def pick_slot(client, field, title, slots):
        return field["label"].lower().replace(" ", "_"), 0.99

    monkeypatch.setattr(agent, "decide", decide)
    monkeypatch.setattr(agent, "pick_slot", pick_slot)
    monkeypatch.setattr(runner, "classify_page", AsyncMock(return_value=("other", 0.99)))


def review(confirm, cancel=None, *, corrections=False):
    reviews, patches = [], {}

    def on_review(job_id, *, filled_fields, screenshot_b64):
        reviews.append(dict(filled_fields))
        patches.clear()
        if corrections and len(reviews) == 1:
            key = next(k for k, v in filled_fields.items() if v == "ada@example.com")
            assert len(key.split(":")) == 2
            patches[key] = "corrected@example.com"
        (cancel or confirm).set()
        return {"review_id": "review", "revision": len(reviews)}

    return on_review, lambda _: dict(patches), reviews


@pytest.mark.parametrize("url", [URL, "https://www.linkedin.com/jobs/view/123"])
async def test_assisted_runs_harness_first_and_returns_awaiting_user(monkeypatch, url):
    expected = ApplicationResult(
        status="awaiting_user_submission", method="assisted", message="ready"
    )
    run = AsyncMock(return_value=expected)
    monkeypatch.setattr(runner, "harness_apply", run)
    strategy = AssistedApplyStrategy()
    strategy._form_filler.fill_only = AsyncMock()
    assert await strategy.apply(apply_url=url, job_id=42, linkedin_url="linkedin") == expected
    assert run.await_args.kwargs["job_id"] == 42
    assert run.await_args.kwargs["applicant"].linkedin_url == "linkedin"
    strategy._form_filler.fill_only.assert_not_awaited()


@pytest.mark.parametrize("strategy_cls", [AssistedApplyStrategy, AutoApplyStrategy])
async def test_harness_error_before_submit_falls_back_to_tier1(monkeypatch, tab, strategy_cls):
    monkeypatch.setattr(
        runner,
        "run_harness",
        AsyncMock(side_effect=HarnessBlocked("stuck", result=ready(clicked=2))),
    )
    # Strategies require an HTTP URL; the fake opener still loads a local page.
    goto = tab.page.goto
    monkeypatch.setattr(
        tab.page, "goto", lambda *a, **k: goto((FIXTURES / "greenhouse.html").as_uri())
    )
    strategy = strategy_cls()
    strategy._form_filler = SimpleNamespace(
        fill_only=AsyncMock(), fill_and_submit=AsyncMock(return_value={"status": "confirmed"})
    )
    result = await strategy.apply(apply_url=URL, job_id=42)
    method = (
        strategy._form_filler.fill_only
        if strategy_cls is AssistedApplyStrategy
        else (strategy._form_filler.fill_and_submit)
    )
    method.assert_awaited_once()
    assert result.status in {"awaiting_user_submission", "confirmed"}


@pytest.mark.parametrize("strategy_cls", [AssistedApplyStrategy, AutoApplyStrategy])
async def test_harness_disabled_without_url_keeps_old_path(monkeypatch, strategy_cls):
    monkeypatch.setattr(settings, "DECISION_MODEL_URL", "")
    run = AsyncMock()
    monkeypatch.setattr(runner, "harness_apply", run)
    strategy = strategy_cls()
    strategy._form_filler = SimpleNamespace(
        fill_only=AsyncMock(), fill_and_submit=AsyncMock(return_value={"status": "confirmed"})
    )
    await strategy.apply(apply_url=URL, job_id=42)
    run.assert_not_awaited()


@pytest.mark.parametrize("attached", [True, False])
async def test_assisted_leaves_tab_open_and_lists_generated_and_missing_fields(
    monkeypatch, tab, attached
):
    retained = []
    monkeypatch.setattr(runner, "_OPEN_BROWSERS", retained, raising=False)
    tab.attached = attached
    result = ready()
    result.typed = {"0:1": {"label": "First Name", "value": "Ada", "source": "first_name"}}
    result.written, result.needs_you = ["Motivation"], ["Visa"]
    monkeypatch.setattr(runner, "run_harness", AsyncMock(return_value=result))
    outcome = await apply(tab)
    assert outcome.status == "awaiting_user_submission"
    assert all(label in outcome.message for label in ("First Name", "Motivation", "Visa"))
    assert not tab.page.is_closed()
    tab.closed.assert_not_awaited()
    if attached:
        tab.pw.stop.assert_awaited_once()
        assert retained == []
    else:
        tab.pw.stop.assert_not_awaited()
        assert retained == [(tab.pw, tab.context, tab.page)]
    profile, args = tab.launches[0]
    assert profile.parent.name == "browser_profiles"
    assert "--no-first-run" in args and "--disable-infobars" in args


async def test_auto_review_approval_clicks_submit_once_and_verifies(monkeypatch, tab):
    script(monkeypatch)
    confirm = asyncio.Event()
    on_review, patches, reviews = review(confirm)
    outcome = await apply(
        tab,
        mode="auto",
        job_id=42,
        confirm_event=confirm,
        on_review=on_review,
        on_get_patches=patches,
    )
    assert outcome.status == "confirmed"
    assert len(reviews) == 1 and tab.clicks == [1]
    assert "received.html" in tab.page.url
    assert tab.page.is_closed()
    tab.closed.assert_not_awaited()  # attached context belongs to the owner


async def test_auto_cancel_never_clicks(monkeypatch, tab):
    script(monkeypatch)
    confirm, cancel = asyncio.Event(), asyncio.Event()
    on_review, patches, reviews = review(confirm, cancel)
    outcome = await apply(
        tab,
        mode="auto",
        job_id=42,
        confirm_event=confirm,
        cancel_event=cancel,
        on_review=on_review,
        on_get_patches=patches,
    )
    assert outcome.status == "cancelled" and tab.clicks == [] and len(reviews) == 1


async def test_auto_correction_refills_by_harness_key(monkeypatch, tab):
    script(monkeypatch)
    confirm = asyncio.Event()
    on_review, patches, reviews = review(confirm, corrections=True)
    outcome = await apply(
        tab,
        mode="auto",
        job_id=42,
        confirm_event=confirm,
        on_review=on_review,
        on_get_patches=patches,
    )
    assert outcome.status == "confirmed" and tab.clicks == [1]
    assert len(reviews) == 2 and "corrected@example.com" in reviews[-1].values()


async def test_job_and_linkedin_reach_the_goal(monkeypatch, tab, tmp_path):
    cv = tmp_path / "base.tex"
    cv.write_text("Ada builds compilers.")
    async with AsyncSessionLocal() as db:
        job = Job(title="Compiler Engineer", company="Example", url=URL, dedup_hash="runner")
        db.add(job)
        await db.flush()
        match = JobMatch(job_id=job.id, score=90)
        db.add_all(
            [match, UserProfile(full_name="Ada", email="ada@example.com", base_cv_path=str(cv))]
        )
        await db.commit()
        match_id = match.id
    captured = {}
    llm = AsyncMock()
    llm.generate_text.return_value = "Answer"
    monkeypatch.setattr(runner, "make_llm_client", lambda: llm)

    async def run(page, **kwargs):
        captured.update(kwargs)
        assert await kwargs["writer"]({"label": "Pourquoi?", "page_text": "x" * 2000}) == "Answer"
        return ready()

    monkeypatch.setattr(runner, "run_harness", run)
    await apply(tab, job_id=match_id)
    assert "Compiler Engineer at Example" in captured["goal"]
    assert "LinkedIn https://example.com/ada" in captured["goal"]
    assert any(value == "France" for _, value in captured["slots"].values())
    prompt = llm.generate_text.await_args.args[0]
    for text in (
        "Ada builds compilers.",
        "Compiler Engineer",
        "Example",
        "Pourquoi?",
        "France",
        "language of the question",
        "1,200",
        "exactly NONE",
        "eligibility",
        "salary",
        "notice period",
        "visa",
        "dates",
    ):
        assert text in prompt
    assert "x" * 1501 not in prompt


async def test_no_writing_model_passes_none(monkeypatch, tab):
    monkeypatch.setattr(runner, "make_llm_client", Mock(side_effect=ValueError("no writing model")))
    run = AsyncMock(return_value=ready())
    monkeypatch.setattr(runner, "run_harness", run)
    await apply(tab)
    assert run.await_args.kwargs["writer"] is None


@pytest.mark.parametrize("kind", ["login", "captcha"])
async def test_handoff_broadcasts_matching_frame_and_ntfy(monkeypatch, tab, kind):
    from backend.api.ws import manager
    from backend.monitor import notify

    monkeypatch.setattr(settings, "NTFY_URL", "https://ntfy.invalid/topic")

    async def run(page, **kwargs):
        await kwargs["notify"](kind, f"{kind} on jobs.example.com")
        return ready()

    monkeypatch.setattr(runner, "run_harness", run)
    await apply(tab, job_id=42)
    manager.broadcast.assert_awaited_once()
    frame = manager.broadcast.await_args.args[0]
    assert frame.type == ("login_required" if kind == "login" else "captcha_detected")
    if kind == "captcha":
        assert frame.job_id == 42
        assert frame.message == "captcha on jobs.example.com"
    notify.notify_text.assert_awaited_once_with(
        "Login needed" if kind == "login" else "Captcha", f"{kind} on jobs.example.com"
    )


async def test_error_after_submitting_advance_does_not_fall_back(monkeypatch, tab):
    script(
        monkeypatch,
        [("TYPE_TEXT", "Full Name"), ("TYPE_TEXT", "Email"), ("CLICK", "Next"), ("ERROR", None)],
    )
    outcome = await apply(tab, fixture="easy_apply.html")
    assert outcome.status == "needs_verification" and tab.clicks == []


async def test_auto_no_submit_node_leaves_tab_for_owner(monkeypatch, tab):
    script(monkeypatch, [("DONE", None)])
    outcome = await apply(tab, mode="auto", fixture="aria_dropdown.html")
    assert outcome.status == "awaiting_user_submission" and not tab.page.is_closed()


async def test_already_received_is_needs_verification(monkeypatch, tab):
    result = ready()
    result.stop = "already_received"
    monkeypatch.setattr(runner, "run_harness", AsyncMock(return_value=result))
    outcome = await apply(tab)
    assert outcome.status == "needs_verification"
    assert "page already shows a confirmation" in outcome.evidence


async def test_engine_threads_job_and_linkedin_to_assisted(monkeypatch):
    engine = ApplicationEngine()
    engine._assisted.apply = AsyncMock(
        return_value=ApplicationResult(status="awaiting_user_submission", method="assisted")
    )
    ctx = SimpleNamespace(
        job_match_id=42,
        apply_url=URL,
        confirm_event=None,
        cancel_event=None,
        extras={
            "mode": ApplyMode.ASSISTED,
            "applicant": applicant(),
            "cv_pdf": None,
            "letter_pdf": None,
        },
    )
    await engine._dispatch(ctx)
    kwargs = engine._assisted.apply.await_args.kwargs
    assert kwargs["job_id"] == 42 and kwargs["linkedin_url"] == "https://example.com/ada"


@pytest.mark.parametrize("change", ["value", "field", "submit", "document", "invalid"])
async def test_auto_changed_review_cannot_submit(monkeypatch, tab, tmp_path, change):
    script(monkeypatch)
    confirm, cancel = asyncio.Event(), asyncio.Event()
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF fixture")
    reviews = []

    async def change_page():
        if change == "value":
            await tab.page.fill("#email", "changed@example.com")
        elif change == "field":
            await tab.page.evaluate("replaceFirstName()")
        elif change == "submit":
            await tab.page.evaluate(
                "document.querySelector('#submit').replaceWith("
                "document.querySelector('#submit').cloneNode(true))"
            )
        elif change == "invalid":
            await tab.page.fill("#email", "invalid")
        else:
            cv.write_bytes(b"%PDF changed")
        confirm.set()

    def on_review(job_id, **kwargs):
        reviews.append(kwargs["filled_fields"])
        if len(reviews) == 1:
            asyncio.create_task(change_page())
        else:
            cancel.set()

    # The real loop observes the upload as a separate step.
    script(
        monkeypatch,
        [("WAIT", None)]
        + [
            ("TYPE_TEXT", label)
            for label in ("First Name", "Last Name", "Email", "Phone", "Location")
        ]
        + [("CLICK", "Submit Application")],
    )
    outcome = await apply(
        tab,
        mode="auto",
        job_id=42,
        cv_pdf=cv,
        confirm_event=confirm,
        cancel_event=cancel,
        on_review=on_review,
    )
    assert outcome.status in {"cancelled", "failed", "awaiting_user_submission"}
    assert tab.clicks == []
    if change == "value":
        assert "changed@example.com" in reviews[-1].values()


@pytest.mark.parametrize("destination", ["another_form", "frame_confirmation", "error"])
async def test_auto_post_click_outcomes_are_final(monkeypatch, tab, destination):
    script(monkeypatch)
    confirm = asyncio.Event()

    async def change_destination():
        await tab.page.evaluate(
            """destination => {
            document.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                if (destination === 'frame_confirmation') {
                    document.body.innerHTML = '<iframe srcdoc="<p>Receipt fixture</p>"></iframe>';
                } else document.querySelector('h1').textContent = 'Another form step';
            });
        }""",
            destination,
        )
        confirm.set()

    def on_review(job_id, **kwargs):
        asyncio.create_task(change_destination())

    if destination == "frame_confirmation":

        async def classify(client, text):
            assert "Receipt fixture" in text
            return "application_received", 0.99

        monkeypatch.setattr(runner, "classify_page", classify)
    elif destination == "error":
        monkeypatch.setattr(runner, "verify_page", AsyncMock(side_effect=RuntimeError("lost page")))
    outcome = await apply(tab, mode="auto", job_id=42, confirm_event=confirm, on_review=on_review)
    expected = "confirmed" if destination == "frame_confirmation" else "needs_verification"
    assert outcome.status == expected and tab.clicks == [1]


async def test_transport_error_preserves_loop_result_for_fallback(monkeypatch, tab):
    # Exercise the real client adapter and real loop: no HTTP request is made.
    monkeypatch.setattr(
        runner,
        "SystemOneClient",
        Mock(
            return_value=SimpleNamespace(
                model="scripted",
                post=AsyncMock(side_effect=DecisionError("server down")),
                aclose=AsyncMock(),
            )
        ),
    )
    assert await apply(tab) is None


async def test_error_without_click_history_never_falls_back(monkeypatch, tab):
    monkeypatch.setattr(
        runner, "run_harness", AsyncMock(side_effect=DecisionError("invalid response"))
    )
    outcome = await apply(tab)
    assert outcome.status == "needs_verification"


async def test_writer_without_cv_uses_empty_cv_text(monkeypatch, tab):
    llm = AsyncMock()
    llm.generate_text.return_value = ""
    monkeypatch.setattr(runner, "make_llm_client", lambda: llm)

    async def run(page, **kwargs):
        assert await kwargs["writer"]({"label": "Visa?"}) == ""
        return ready()

    monkeypatch.setattr(runner, "run_harness", run)
    await apply(tab)
    assert "CV:\n\nProfile:" in llm.generate_text.await_args.args[0]


async def test_auto_feature_gate_keeps_tab_open_without_click(monkeypatch, tab):
    script(monkeypatch)
    monkeypatch.setattr(settings, "jobpilot_auto_submit_enabled", False)
    outcome = await apply(tab, mode="auto")
    assert outcome.status == "awaiting_user_submission" and not tab.page.is_closed()
    assert tab.clicks == []


async def test_cross_origin_review_fields_are_read_only(monkeypatch, tab):
    from backend.api.ws import manager

    host = (
        (FIXTURES / "iframe_host.html")
        .read_text()
        .replace('src="iframe_form.html"', 'src="https://embedded.invalid/iframe_form.html"')
    )
    await tab.page.route(URL, lambda route: route.fulfill(body=host, content_type="text/html"))
    await tab.page.route(
        "https://embedded.invalid/iframe_form.html",
        lambda route: route.fulfill(
            body=(FIXTURES / "iframe_form.html").read_text(), content_type="text/html"
        ),
    )
    script(
        monkeypatch,
        [("TYPE_TEXT", "First Name"), ("TYPE_TEXT", "Email"), ("CLICK", "Submit Application")],
    )
    confirm, cancel = asyncio.Event(), asyncio.Event()
    reviews, patches = [], {}

    def on_review(job_id, **kwargs):
        fields = kwargs["filled_fields"]
        reviews.append(dict(fields))
        if len(reviews) == 1:
            key = next(k for k, v in fields.items() if v == "ada@example.com")
            assert key.startswith("1:")
            patches[key] = "should-not-be-typed@example.com"
            confirm.set()
        else:
            cancel.set()

    async with asyncio.timeout(15):
        outcome = await runner.harness_apply(
            mode="auto",
            apply_url=URL,
            job_id=42,
            applicant=applicant(),
            cv_pdf=None,
            letter_pdf=None,
            confirm_event=confirm,
            cancel_event=cancel,
            on_review=on_review,
            on_get_patches=lambda _: patches,
        )
    assert outcome.status == "cancelled" and len(reviews) == 2
    assert "ada@example.com" in reviews[-1].values()
    assert "should-not-be-typed@example.com" not in reviews[-1].values()
    notes = " ".join(call.args[0].note for call in manager.broadcast.await_args_list)
    assert "read-only" in notes and "could not be applied" in notes


@pytest.mark.parametrize(
    "response",
    [
        "NONE",
        "  none\n",
        "",
        '""',
        "''",
        "I don't have the required visa details.",
        "I do not have that information.",
        "I cannot answer from the supplied profile.",
        "Je ne dispose pas de ces informations.",
        "As an AI, I cannot determine eligibility.",
        "  i CANNOT determine eligibility.\n",
        TimeoutError("writer timed out"),
        RuntimeError("writer CLI failed"),
    ],
)
async def test_writer_leaves_unknown_answers_for_owner_and_continues(
    monkeypatch, tab, caplog, response
):
    llm = AsyncMock()
    llm.generate_text.side_effect = [response, "I build compilers."]
    monkeypatch.setattr(runner, "make_llm_client", lambda: llm)
    goto = tab.page.goto

    async def load_form(*args, **kwargs):
        await goto(*args, **kwargs)
        await tab.page.evaluate("""() => {
            document.querySelector('form').insertAdjacentHTML('afterbegin',
                '<label>Visa <textarea id="visa"></textarea></label>' +
                '<label>Motivation <textarea id="motivation"></textarea></label>');
        }""")

    monkeypatch.setattr(tab.page, "goto", load_form)
    script(
        monkeypatch,
        [
            ("TYPE_TEXT", "Visa"),
            ("TYPE_TEXT", "Motivation"),
            ("TYPE_TEXT", "First Name"),
            ("DONE", None),
        ],
    )

    async def pick_slot(client, field, title, slots):
        return ("first_name" if field["label"] == "First Name" else "needs_written_answer"), 0.99

    monkeypatch.setattr(agent, "pick_slot", pick_slot)
    outcome = await apply(tab)
    assert outcome.status == "awaiting_user_submission"
    assert await tab.page.input_value("#visa") == ""
    assert await tab.page.input_value("#motivation") == "I build compilers."
    assert await tab.page.input_value("#first_name") == "Ada"
    assert "Left for you: Visa." in outcome.message
    assert "Generated answers: Motivation." in outcome.message
    if isinstance(response, Exception):
        assert "Visa" in caplog.text and str(response) in caplog.text


@pytest.mark.parametrize("wait_times_out", [False, True])
async def test_auto_waits_for_networkidle_before_verification(monkeypatch, tab, wait_times_out):
    script(monkeypatch)
    events = []
    click, load = runner.act, tab.page.wait_for_load_state

    async def submit(*args, **kwargs):
        await click(*args, **kwargs)
        events.append("click")

    async def wait(state, **kwargs):
        if state != "networkidle":
            return await load(state, **kwargs)
        assert kwargs == {"timeout": 10_000}
        events.append("wait")
        if wait_times_out:
            raise PlaywrightTimeoutError("still loading")

    async def verify(*args, **kwargs):
        events.append("verify")
        return SimpleNamespace(outcome="confirmed", evidence="received")

    monkeypatch.setattr(runner, "act", submit)
    monkeypatch.setattr(tab.page, "wait_for_load_state", wait)
    monkeypatch.setattr(runner, "verify_page", verify)
    confirm = asyncio.Event()
    on_review, _, _ = review(confirm)
    outcome = await apply(tab, mode="auto", job_id=42, confirm_event=confirm, on_review=on_review)
    assert outcome.status == "confirmed", outcome.evidence
    assert events == ["click", "wait", "verify"]
    assert tab.clicks == [1]


async def test_auto_verification_reads_page_text_immediately_before_click(monkeypatch, tab):
    from backend.applier import verification

    script(monkeypatch)
    events, captured = [], {}
    click, observe = runner.act, runner.observe
    read_text = verification.page_text

    async def snapshot(page):
        events.append("snapshot")
        snap = await observe(page)
        snap["text"] = "A shortened harness snapshot"
        return snap

    async def baseline(page):
        events.append("baseline")
        captured["text"] = await read_text(page)
        return captured["text"]

    async def submit(*args, **kwargs):
        events.append("click")
        return await click(*args, **kwargs)

    async def verify(page, *, before_url, before_text):
        events.append("verify")
        assert before_text == captured["text"]
        assert "Apply for Software Engineer" in before_text
        assert before_url.endswith("greenhouse.html")
        return SimpleNamespace(outcome="confirmed", evidence="received")

    monkeypatch.setattr(runner, "observe", snapshot)
    monkeypatch.setattr(verification, "page_text", baseline)
    monkeypatch.setattr(runner, "act", submit)
    monkeypatch.setattr(runner, "verify_page", verify)
    confirm = asyncio.Event()
    on_review, _, _ = review(confirm)
    outcome = await apply(tab, mode="auto", job_id=42, confirm_event=confirm, on_review=on_review)
    assert outcome.status == "confirmed"
    assert events[-3:] == ["baseline", "click", "verify"]


@pytest.mark.parametrize("in_frame", [False, True])
@pytest.mark.parametrize(
    ("markup", "invalid"),
    [
        ('<input required><form><input value="Ada" required></form>', False),
        ('<div aria-invalid="true"></div><form><input value="Ada" required></form>', False),
        ('<form><input required></form>', True),
        ('<form><input type="email" value="invalid"></form>', True),
    ],
)
async def test_invalid_fields_only_checks_controls_in_forms(tab, in_frame, markup, invalid):
    await tab.page.set_content('<iframe srcdoc="<p>Frame</p>"></iframe>' if in_frame else markup)
    if in_frame:
        await tab.page.frame_locator("iframe").locator("p").wait_for()
        await tab.page.frames[1].set_content(markup)
    assert await runner._invalid_fields({"_frame_refs": tab.page.frames}) is invalid


async def test_detached_frame_during_review_is_unreadable_and_not_submitted(monkeypatch, tab):
    from backend.api.ws import manager

    await tab.page.set_content("""
        <form><button id="submit" type="submit">Submit Application</button></form>
        <iframe srcdoc='<form><label>Name <input value="Ada"></label></form>'></iframe>
    """)
    await tab.page.frame_locator("iframe").locator("input").wait_for()
    snap = await runner.observe(tab.page)
    result = ready()
    result.submit_action = next(a for a in snap["actions"] if a["kind"] == "click")
    observe, observations = runner.observe, 0

    async def detach_after_approval(page):
        nonlocal observations
        current = await observe(page)
        observations += 1
        if observations == 3:
            await page.locator("iframe").evaluate("e => e.remove()")
        return current

    monkeypatch.setattr(runner, "observe", detach_after_approval)
    submit = AsyncMock()
    monkeypatch.setattr(runner, "_submit", submit)
    confirm = asyncio.Event()
    on_review, _, _ = review(confirm)
    status, evidence = await runner._review(
        tab.page,
        result,
        job_id=42,
        documents=[],
        confirm_event=confirm,
        cancel_event=None,
        on_review=on_review,
        on_get_patches=None,
    )
    assert status == "awaiting_user_submission"
    assert "no longer readable" in evidence
    submit.assert_not_awaited()
    last_review = manager.broadcast.await_args.args[0]
    assert "no longer readable" in last_review.note
    assert not any(key.startswith("1:") for key in last_review.filled_fields)


async def test_applicant_linkedin_url_has_profile_length_bound():
    assert ApplicantInfo().linkedin_url == ""
    assert ApplicantInfo(linkedin_url="x" * MAX_LEN_LOCATION).linkedin_url == "x" * MAX_LEN_LOCATION
    with pytest.raises(ValidationError) as exc:
        ApplicantInfo(linkedin_url="x" * (MAX_LEN_LOCATION + 1))
    assert exc.value.errors()[0]["loc"] == ("linkedin_url",)
    assert exc.value.errors()[0]["type"] == "string_too_long"
