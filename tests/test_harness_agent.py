"""The bounded harness loop against offline pages and scripted System One answers."""

import asyncio
import json
from copy import deepcopy
from itertools import repeat
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from backend.applier.harness import agent
from backend.applier.harness.decide import Decision, DecisionError, RequestTooLong, build_request
from backend.applier.harness.snapshot import fresh, observe
from backend.config import settings

FIXTURES = Path(__file__).parent / "fixtures/harness"
pytestmark = pytest.mark.asyncio(loop_scope="module")
PROFILE = (
    "Ada Byron Lovelace",
    "ada@example.com",
    "0123456789",
    "Paris",
    "https://example.com/ada",
)


@pytest_asyncio.fixture(scope="module")
async def browser():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture(loop_scope="module")
async def page(browser):
    page = await browser.new_page(viewport={"width": 1120, "height": 780})
    yield page
    await page.close()


@pytest.fixture
def documents(tmp_path):
    cv, letter = tmp_path / "cv.pdf", tmp_path / "letter.pdf"
    cv.write_bytes(b"%PDF-1.4 CV fixture")
    letter.write_bytes(b"%PDF-1.4 Letter fixture")
    return {"cv": cv, "letter": letter}


def step(operation, label=None, **kwargs):
    return {"operation": operation, "label": label, **kwargs}


class ScriptedClient:
    """Return valid wire responses; never create an HTTP client or use the network."""

    model = "scripted"

    def __init__(self, steps, *, slots=None, classifications=(), before=None):
        self.steps = iter(steps)
        self.slots = slots or {
            "First Name": ("first_name", 0.9),
            "Last Name": ("last_name", 0.9),
            "Full Name": ("full_name", 0.9),
            "Email": ("email", 0.9),
            "Phone": ("phone", 0.9),
            "Location": ("location", 0.9),
        }
        self.classifications = iter(classifications)
        self.before = before
        self.requests = []
        self.aclose = AsyncMock()

    async def post(self, body):
        assert settings.DECISION_MODEL_URL == ""
        self.requests.append(deepcopy(body))
        questions = body["questions"]
        choices, probabilities = {}, {}
        if "operation" in questions:
            scripted = next(self.steps)
            choices.update(
                operation=scripted["operation"],
                page_kind=scripted.get("page_kind", "application_form"),
                submit_target="none",
            )
            probabilities["page_kind"] = scripted.get("page_kind_p", 0.9)
            probabilities["submit_target"] = scripted.get("submit_p", 0.9)
            for name, label in (
                (scripted["operation"].lower() + "_target", scripted.get("label")),
                ("submit_target", scripted.get("submit")),
            ):
                if label is not None:
                    choices[name] = next(
                        key
                        for key, value in questions[name]["criteria"].items()
                        if isinstance(value, dict) and value["element"].split("] ", 1)[1] == label
                    )
            if self.before:
                await self.before(body)
        elif "slot" in questions:
            choices["slot"], probabilities["slot"] = self.slots[body["state"]["field_label"]]
        else:
            choices["page_kind"], probabilities["page_kind"] = next(self.classifications)
        answers = {}
        for name, question in questions.items():
            criteria = question["criteria"]
            choice = choices.get(name, next(iter(criteria)))
            p = probabilities.get(name, 0.9) if len(criteria) > 1 else 1.0
            answers[name] = {
                "choice": choice,
                "confidence": 0.9,
                "probabilities": {
                    key: p if key == choice else (1 - p) / (len(criteria) - 1) for key in criteria
                },
            }
        return {"answers": answers}

    @property
    def decisions(self):
        return [body for body in self.requests if "operation" in body["questions"]]


async def load(page, name="greenhouse.html"):
    await page.goto((FIXTURES / name).resolve().as_uri())


async def run(page, client, **kwargs):
    return await agent.run_harness(
        page,
        goal=agent.build_goal("Engineer", "Example", *PROFILE),
        slots=agent.profile_slots(*PROFILE, {}),
        documents=kwargs.pop("documents", {}),
        writer=kwargs.pop("writer", None),
        client=client,
        job_id=42,
        **kwargs,
    )


def fill_labels(body):
    return [
        value["element"].split("] ", 1)[1]
        for value in body["questions"].get("type_text_target", {}).get("criteria", {}).values()
    ]


async def test_profile_slots_only_offer_stored_values():
    slots = agent.profile_slots(*PROFILE, {"country": "France", "Salary": "", "email": "alternate"})
    assert slots["first_name"][1] == "Ada"
    assert slots["last_name"][1] == "Byron Lovelace"
    assert slots["location"][1] == "Paris"
    assert slots["email"][1] == "ada@example.com"
    assert any("country" in label and "France" in label for label, _ in slots.values())
    assert any("email" in label and value == "alternate" for label, value in slots.values())
    assert "years_experience" not in slots and "salary_expectation" not in slots
    assert {key for key, (_, value) in slots.items() if not value} == {
        "needs_written_answer",
        "leave_empty",
    }
    single = agent.profile_slots("Ada", "", "", "", "", {})
    assert set(single) == {"first_name", "full_name", "needs_written_answer", "leave_empty"}


@pytest.mark.parametrize("linkedin", ["", PROFILE[-1]])
async def test_build_goal_contains_profile_and_do_not_submit(linkedin):
    assert agent.build_goal("Engineer", "Example", *PROFILE[:-1], linkedin) == (
        "Fill in this job application for Engineer at Example as Ada Byron Lovelace, "
        "email ada@example.com, phone 0123456789, location Paris"
        + (f", LinkedIn {linkedin}" if linkedin else "")
        + ". Attach the CV when asked. Do not submit the application: "
        "when every field you can fill is filled, choose DONE."
    )


async def test_fills_greenhouse_and_stops_at_submit_point_without_clicking(page, documents):
    await load(page)
    labels = ["First Name", "Last Name", "Email", "Phone", "Location"]
    client = ScriptedClient(
        [step("WAIT")]
        + [step("TYPE_TEXT", label) for label in labels]
        + [step("SELECT", "Work location → Remote"), step("CLICK", "Submit Application")]
    )
    result = await run(page, client, documents=documents)
    assert result.stop == "submit_point" and not result.clicked_submit
    assert result.submit_action["label"] == "Submit Application"
    assert await page.evaluate("window.submitClicks") == 0
    assert [
        await page.locator(f"#{name}").input_value()
        for name in ("first_name", "last_name", "email", "phone", "location")
    ] == ["Ada", "Byron Lovelace", *PROFILE[1:4]]
    assert await page.locator("#work_location").input_value() == "remote"
    assert await page.locator("#resume").evaluate("e => e.files[0].name") == "cv.pdf"
    assert result.uploads == ["cv"] and len(result.typed) == 5
    assert all(key.startswith("0:") for key in result.typed)
    assert {field["label"] for field in result.typed.values()} == set(labels)
    client.aclose.assert_awaited_once()


async def test_easy_apply_next_is_clicked_submit_div_is_not(page):
    await load(page, "easy_apply.html")
    client = ScriptedClient(
        [
            step("TYPE_TEXT", "Full Name"),
            step("TYPE_TEXT", "Email"),
            step("CLICK", "Next"),
            step("SELECT", "Work preference → Remote"),
            step("CLICK", "Submit application"),
        ]
    )
    result = await run(page, client)
    assert await page.locator("#questions").is_visible()
    assert await page.evaluate("window.submitClicks") == 0
    assert result.stop == "submit_point"


@pytest.mark.parametrize("label", ["Apply now", "Explore careers"])
@pytest.mark.parametrize("tag", ["button", "a"])
async def test_job_description_apply_opens_form(page, label, tag):
    await page.set_content(
        f'<{tag} href="#" onclick="this.remove();document.querySelector(\'form\')'
        f'.hidden=false">{label}</{tag}><form hidden>Application form</form>'
    )
    client = ScriptedClient(
        [
            step("CLICK", label, page_kind="job_description"),
            step("DONE"),
        ]
    )
    assert (await run(page, client)).stop == "ready"
    assert await page.locator("form").is_visible()


@pytest.mark.parametrize("label", ["Apply now", "Go"])
@pytest.mark.parametrize("inside", [False, True])
async def test_unknown_control_in_form_context_stops_ready(page, label, inside):
    html = f'<input type="submit" value="{label}" onclick="window.clicked=true">'
    await page.set_content(f"<form>{html}</form>" if inside else html)
    client = ScriptedClient([step("CLICK", label, page_kind_p=0.5)])
    assert (await run(page, client)).stop == "ready"
    assert not await page.evaluate("window.clicked")


@pytest.mark.parametrize(
    "label,submit",
    [
        ("Next and submit", None),
        ("Continue submitting application", None),
        ("Continue and finish", None),
        ("Review et valider", None),
    ],
)
async def test_strict_submit_words_override_advance(page, label, submit):
    await page.set_content(f'<form><button onclick="window.clicked=true">{label}</button></form>')
    result = await run(page, ScriptedClient([step("CLICK", label, submit=submit)]))
    assert result.stop == "submit_point" and not await page.evaluate("window.clicked")


async def test_filechooser_is_answered_with_cv(page, documents):
    await page.set_content("""<button type="button" onclick="
        const input=document.createElement('input');
        input.type='file';input.id='resume';input.hidden=true;document.body.append(input);input.click()">
        Attach</button>""")
    client = ScriptedClient([step("CLICK", "Attach"), step("DONE")])
    result = await run(page, client, documents=documents)
    assert await page.locator("#resume").evaluate("e => e.files[0].name") == "cv.pdf"
    assert result.uploads == ["cv"]
    assert not page._impl_obj.listeners("filechooser")


async def test_attach_after_preupload_is_skipped_and_recorded(page, documents):
    await load(page)
    await page.locator("#attach").evaluate("e => e.onclick = () => window.attachClicked=true")
    client = ScriptedClient([step("WAIT"), step("CLICK", "Attach"), step("DONE")])
    result = await run(page, client, documents=documents)
    assert result.uploads == ["cv"] and not await page.evaluate("window.attachClicked")
    assert client.decisions[-1]["state"]["recent_actions"][-1]["action"] == "Attach"


async def test_uploads_match_letter_and_cv_across_frames_and_shadow_roots(page, documents):
    await page.set_content("""
        <iframe srcdoc='<input type="file" name="cover_letter" hidden>'></iframe>
        <div id="host"></div><script>document.querySelector('#host').attachShadow({mode:'open'})
        .innerHTML='<input id="curriculum" type="file" accept="application/pdf" hidden>';
        </script>""")
    result = await run(page, ScriptedClient([step("WAIT"), step("DONE")]), documents=documents)
    assert set(result.uploads) == {"letter", "cv"}
    assert await page.frames[1].locator("input").evaluate("e => e.files[0].name") == "letter.pdf"
    assert await page.locator("#curriculum").evaluate("e => e.files[0].name") == "cv.pdf"


@pytest.mark.parametrize("slot,p", [("email", 0.4), ("leave_empty", 0.9)])
async def test_low_slot_probability_leaves_field_and_lists_it(page, slot, p):
    await load(page)
    client = ScriptedClient([step("TYPE_TEXT", "Email"), step("DONE")], slots={"Email": (slot, p)})
    result = await run(page, client)
    assert await page.locator("#email").input_value() == ""
    assert result.needs_you == ["Email"] and not result.typed
    assert "Email" not in fill_labels(client.decisions[-1])


async def test_needs_written_answer_uses_writer_and_records_written(page):
    await page.set_content("<label>Motivation<textarea></textarea></label>")
    writer = AsyncMock(return_value="I build reliable software.")
    client = ScriptedClient(
        [step("TYPE_TEXT", "Motivation"), step("DONE")],
        slots={"Motivation": ("needs_written_answer", 0.9)},
    )
    result = await run(page, client, writer=writer)
    assert await page.locator("textarea").input_value() == "I build reliable software."
    assert result.written == ["Motivation"] and result.needs_you == []
    assert next(iter(result.typed.values()))["source"] == "written"
    context = writer.await_args.args[0]
    assert context["label"] == "Motivation" and context["job_id"] == 42
    assert len(context["page_text"]) <= 1500


@pytest.mark.parametrize("available", [True, False])
async def test_writer_empty_string_leaves_field_empty(page, available):
    await page.set_content("<label>Salary<textarea></textarea></label>")
    client = ScriptedClient(
        [step("TYPE_TEXT", "Salary"), step("DONE")], slots={"Salary": ("needs_written_answer", 0.9)}
    )
    result = await run(page, client, writer=AsyncMock(return_value="") if available else None)
    assert await page.locator("textarea").input_value() == ""
    assert result.needs_you == ["Salary"] and not result.written
    assert "Salary" not in fill_labels(client.decisions[-1])


async def test_existing_value_marks_field_done_without_typing(page):
    await load(page)
    await page.locator("#email").fill(PROFILE[1])
    await page.locator("#email").evaluate("e => e.oninput = () => window.typedAgain=true")
    client = ScriptedClient([step("TYPE_TEXT", "Email"), step("DONE")])
    result = await run(page, client)
    assert not await page.evaluate("window.typedAgain")
    assert "Email" not in fill_labels(client.decisions[-1])
    assert not result.needs_you


async def test_leaving_fields_for_owner_still_makes_progress(page):
    await load(page)
    labels = ["First Name", "Last Name", "Email", "Phone"]
    client = ScriptedClient(
        [step("TYPE_TEXT", label) for label in labels] + [step("DONE")],
        slots={label: ("leave_empty", 0.9) for label in labels},
    )
    result = await run(page, client)
    assert result.needs_you == labels and not result.typed
    assert not set(labels).intersection(fill_labels(client.decisions[-1]))


@pytest.mark.parametrize("label", ["Continue to apply", "Review application"])
async def test_advance_requires_the_entire_label_to_be_an_advance_word(page, label):
    await page.set_content(
        "<form onsubmit=\"event.preventDefault();this.textContent='Next step'\">"
        f"<button>{label}</button></form>"
    )
    result = await run(page, ScriptedClient([step("CLICK", label), step("DONE")]))
    assert result.stop == "ready" and result.steps == 0
    assert await page.locator("form").inner_text() == label


async def test_writer_replacing_field_reobserves_without_typing(page):
    await page.set_content("<label>Motivation<textarea></textarea></label>")

    async def writer(_context):
        await page.locator("textarea").evaluate("e => e.replaceWith(e.cloneNode(true))")
        return "Must not reach the replacement"

    client = ScriptedClient(
        [step("TYPE_TEXT", "Motivation"), step("DONE")],
        slots={"Motivation": ("needs_written_answer", 0.9)},
    )
    result = await run(page, client, writer=writer)
    assert not result.typed and not result.written
    assert await page.locator("textarea").input_value() == ""


async def test_login_wall_notifies_and_waits_until_resolved(page, monkeypatch):
    await load(page, "login.html")
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    notify = AsyncMock()
    client = ScriptedClient(
        [step("TYPE_TEXT", "Email", page_kind="login_wall"), step("DONE")],
        classifications=[("login_wall", 0.9), ("login_wall", 0.9), ("application_form", 0.9)],
    )
    assert (await run(page, client, notify=notify)).stop == "ready"
    assert notify.await_count == 1 and notify.await_args.args[0] == "login"
    assert await page.locator("#email").input_value() == ""
    assert sum(set(body["questions"]) == {"page_kind"} for body in client.requests) == 3


async def test_login_wall_timeout_raises_blocked(page, monkeypatch):
    await load(page, "login.html")
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.1, raising=False)
    client = ScriptedClient([step("DONE", page_kind="login_wall")])
    with pytest.raises(agent.HarnessBlocked, match="hand-off") as exc:
        await asyncio.wait_for(run(page, client, notify=AsyncMock()), timeout=2)
    assert exc.value.result.clicked == 0
    client.aclose.assert_awaited_once()


async def test_captcha_uses_existing_handler_then_generic_handoff(page, monkeypatch):
    await page.set_content("<p>Please solve the challenge</p>")
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    handler = AsyncMock(return_value=False)
    monkeypatch.setattr(agent, "check_and_handle_captcha", handler)
    notify = AsyncMock()
    client = ScriptedClient(
        [step("DONE", page_kind="captcha"), step("DONE")],
        classifications=[("application_form", 0.9)],
    )
    await run(page, client, notify=notify)
    handler.assert_awaited_once_with(page, job_id=42, cancel_event=None)
    assert notify.await_args.args[0] == "captcha"


async def test_three_unchanged_steps_raise_stuck(page):
    await page.set_content('<button type="button">Check</button>')
    client = ScriptedClient([step("CLICK", "Check", page_kind="other")] * 3)
    with pytest.raises(agent.HarnessStuck, match="unchanged|progress") as exc:
        await run(page, client)
    assert len(client.decisions) == 3
    assert exc.value.result.clicked == 3


async def test_max_actions_raises_stuck(page):
    await page.set_content("<p>Loading</p>")
    client = ScriptedClient([step("WAIT", page_kind="other")] * 4)
    with pytest.raises(agent.HarnessStuck, match="action"):
        await run(page, client, max_actions=4)
    assert len(client.decisions) == 4


async def test_request_budget_includes_slot_requests(page, monkeypatch):
    await load(page)
    monkeypatch.setattr(agent, "_MAX_REQUESTS", 3)
    client = ScriptedClient([step("TYPE_TEXT", "First Name"), step("TYPE_TEXT", "Last Name")])
    with pytest.raises(agent.HarnessStuck, match="request"):
        await run(page, client)
    assert len(client.requests) == 3
    assert await page.locator("#last_name").input_value() == ""


async def test_already_received_page_returns_already_received(page):
    await load(page, "received.html")
    result = await run(page, ScriptedClient([step("DONE", page_kind="application_received")]))
    assert result.stop == "already_received" and result.steps == 0 and not result.clicked_submit


async def test_confirmation_after_advance_stops_without_more_actions(page):
    await page.set_content(
        "<form onsubmit=\"event.preventDefault();this.textContent='Received'\">"
        "<button>Next</button></form>"
    )
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", "Next"),
                step("BLOCKED", page_kind="application_received"),
            ]
        ),
    )
    assert result.stop == "already_received" and result.steps == 1 and result.clicked_submit


async def test_stale_page_reobserves_without_typing(page):
    await load(page)
    calls = 0

    async def replace_once(_body):
        nonlocal calls
        calls += 1
        if calls == 1:
            await page.evaluate("window.replaceFirstName()")

    client = ScriptedClient([step("TYPE_TEXT", "First Name"), step("DONE")], before=replace_once)
    result = await run(page, client)
    assert await page.locator("#first_name").input_value() == "" and not result.typed
    assert len(client.decisions) == 2


async def test_three_consecutive_stale_decisions_raise_stuck(page):
    await load(page)

    async def add_frame(_body):
        await page.evaluate("document.body.append(document.createElement('iframe'))")

    client = ScriptedClient([step("TYPE_TEXT", "Email")] * 3, before=add_frame)
    with pytest.raises(agent.HarnessStuck, match="stale"):
        await run(page, client)
    assert await page.locator("#email").input_value() == ""
    assert len(client.decisions) == 3


async def test_never_types_into_password(page):
    await load(page, "login.html")
    client = ScriptedClient([step("TYPE_TEXT", "Email"), step("DONE")])
    await run(page, client)
    assert all("Password" not in fill_labels(body) for body in client.decisions)
    assert await page.locator("#password").input_value() == ""


@pytest.mark.parametrize("submit", [None, "Check details", "Submit application"])
async def test_done_records_model_submit_or_single_matching_control(page, submit):
    """submit_target always names some click; a non-submitting pick ("Check details",
    live Greenhouse "Enter manually") falls back to the single submit control."""
    await page.set_content(
        '<button type="button">Check details</button>'
        '<a href="#" onclick="window.clicked=true">Submit application</a>'
    )
    result = await run(page, ScriptedClient([step("DONE", submit=submit)]))
    assert result.stop == "ready"
    assert result.submit_action["label"] == "Submit application"
    assert not await page.evaluate("window.clicked")


async def test_done_with_ambiguous_submit_has_no_target(page):
    await page.set_content("<button>Submit application</button><button>Send application</button>")
    assert (await run(page, ScriptedClient([step("DONE")]))).submit_action is None


async def test_done_outside_application_form_raises_blocked(page):
    await page.set_content("Job unavailable")
    with pytest.raises(agent.HarnessBlocked):
        await run(page, ScriptedClient([step("DONE", page_kind="error")]))


@pytest.mark.parametrize("error", [DecisionError("offline"), RequestTooLong("422")])
async def test_model_failure_closes_client_and_removes_filechooser_handler(page, error):
    await load(page)
    client = ScriptedClient([])
    client.post = AsyncMock(side_effect=error)
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(page, client)
    assert exc.value.result.clicked == 0
    client.aclose.assert_awaited_once()
    assert not page._impl_obj.listeners("filechooser")
    assert await page.locator("#first_name").input_value() == ""


async def test_cancel_interrupts_pending_decision_and_closes_client(page):
    await load(page)
    cancel = asyncio.Event()
    client = ScriptedClient([])

    async def pending(_body):
        cancel.set()
        await asyncio.Event().wait()

    client.post = pending
    with pytest.raises(agent.HarnessBlocked, match="cancel"):
        await asyncio.wait_for(run(page, client, cancel_event=cancel), timeout=1)
    client.aclose.assert_awaited_once()
    assert not page._impl_obj.listeners("filechooser")


@pytest.mark.parametrize(
    "label",
    [
        "Bewerbung einreichen",
        "Je postule",
        "Postulez",
        "Valider",
        "Confirm",
        "Enviar solicitud",
        "Déposer ma candidature",
        "Abschicken",
        "Candidater",
        "Transmettre",
        "Invia",
        "Verzenden",
        "Solliciteren",
    ],
)
async def test_js_final_button_after_typing_is_never_clicked(page, label):
    await page.set_content(
        "<label>Email<input></label>"
        f'<button type="button" onclick="window.submitted=true">{label}</button>'
    )
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Email"),
                step("CLICK", label),
                step("DONE"),
            ]
        ),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "submit_point" and result.submit_action["label"] == label
    assert result.submit_action["submits"] and result.clicked == 0


async def test_icon_only_button_after_typing_stops_ready(page):
    await page.set_content("""<label>Email<input></label>
        <button type="button" onclick="window.submitted=true">
        <svg width="20" height="20"><path d="M0 0L20 10L0 20z"/></svg></button>""")
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Email"),
                step("CLICK", "button"),
                step("DONE"),
            ]
        ),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


@pytest.mark.parametrize(
    "kind,p", [("job_description", 0.9), ("application_form", 0.45), ("other", 0.9)]
)
async def test_apply_after_typing_stays_in_form_context(page, kind, p):
    await page.set_content("""<label>Email<input oninput="this.parentElement.hidden=true"></label>
        <button type="button" onclick="window.submitted=true">Apply</button>""")
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Email"),
                step("CLICK", "Apply", page_kind=kind, page_kind_p=p),
                step("DONE"),
            ]
        ),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


async def test_fill_action_establishes_form_context_before_typing(page):
    await page.set_content(
        '<label>Email<input></label><button onclick="window.clicked=true">Go</button>'
    )
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", "Go", page_kind="job_description"),
                step("DONE"),
            ]
        ),
    )
    assert not await page.evaluate("!!window.clicked")
    assert result.stop == "ready" and result.clicked == 0


async def test_upload_establishes_form_context_when_page_kind_changes(page, documents):
    await page.set_content("""<input type="file" name="resume" hidden>
        <button onclick="window.clicked=true">Apply</button>""")
    result = await run(
        page,
        ScriptedClient(
            [
                step("WAIT"),
                step("CLICK", "Apply", page_kind="job_description"),
                step("DONE"),
            ]
        ),
        documents=documents,
    )
    assert not await page.evaluate("!!window.clicked")
    assert result.uploads == ["cv"] and result.stop == "ready"


@pytest.mark.parametrize("source", ["text", "aria-label", "aria-labelledby"])
async def test_full_accessible_name_blocks_submit_beyond_clip(page, source):
    label = "Review and continue " + "x" * 85 + " then submit application"
    if source == "text":
        button = f"<button>{label}</button>"
    elif source == "aria-label":
        button = f'<button aria-label="{label}">Review</button>'
    else:
        button = (
            f'<span id="name" hidden>{label}</span><button aria-labelledby="name">Review</button>'
        )
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.submitted=true">' + button + "</form>"
    )
    result = await run(page, ScriptedClient([step("CLICK", label[:100]), step("DONE")]))
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "submit_point" and result.clicked == 0
    assert result.submit_action["full_label"] == label


async def test_full_label_is_local_and_never_serialized_in_model_request(page):
    label = "Review " + "x" * 100 + " submit"
    await page.set_content(f'<button aria-label="{label}">Review</button>')
    snap = await observe(page)
    click = next(a for a in snap["actions"] if a["kind"] == "click")
    assert click["full_label"] == label and click["label"] == label[:100]
    body, _, _ = build_request(snap, "goal", [], "scripted")
    serialized = json.dumps(body)
    assert "full_label" not in serialized and label not in serialized


async def test_next_is_clicked_even_when_model_calls_it_final_with_high_probability(page):
    await load(page, "easy_apply.html")
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Full Name"),
                step("TYPE_TEXT", "Email"),
                step("CLICK", "Next", submit="Next", submit_p=0.97),
                step("DONE"),
            ]
        ),
    )
    assert await page.locator("#questions").is_visible()
    assert result.clicked == 1 and result.clicked_submit


@pytest.mark.parametrize(
    "role",
    [
        "checkbox",
        "radio",
        "option",
        "combobox",
        "switch",
        "tab",
        "menuitem",
        "menuitemradio",
        "gridcell",
    ],
)
async def test_non_submitting_control_roles_are_allowed_on_form(page, role):
    await page.set_content(f'''<label>Email<input></label>
        <div role="{role}" tabindex="0" onclick="window.clicked=true">Choice</div>''')
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", "Choice", submit="Choice"),
                step("DONE"),
            ]
        ),
    )
    assert await page.evaluate("window.clicked")
    assert result.clicked == 1 and not result.clicked_submit


@pytest.mark.parametrize("role", ["checkbox", "switch"])
async def test_toggle_is_not_clicked_twice(page, role):
    await page.set_content(f'''<input type="checkbox" role="{role}" aria-label="Consent"
        onclick="window.clicks=(window.clicks||0)+1">''')
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", "Consent"),
                step("CLICK", "Consent"),
                step("DONE"),
            ]
        ),
    )
    assert await page.evaluate("window.clicks") == 1
    assert await page.locator("input").is_checked()
    assert result.stop == "ready" and result.clicked == 1


async def test_open_unfilled_field_is_allowed(page):
    await page.set_content('<label>Email<input onclick="window.opened=true"></label>')
    result = await run(page, ScriptedClient([step("CLICK", "Open Email"), step("DONE")]))
    assert await page.evaluate("window.opened")
    assert result.clicked == 1


@pytest.mark.parametrize("state", ["typed", "excluded", "prefilled", "combobox"])
async def test_open_filled_or_excluded_field_stops_ready(page, state):
    role = ' role="combobox"' if state == "combobox" else ""
    value = ' value="ada@example.com"' if state == "prefilled" else ""
    await page.set_content(
        f'<label>Email<input{role}{value} onclick="window.clicks=(window.clicks||0)+1"></label>'
    )
    steps = [] if state == "prefilled" else [step("TYPE_TEXT", "Email")]
    client = ScriptedClient(
        steps + [step("CLICK", "Open Email"), step("DONE")],
        slots={"Email": ("leave_empty" if state == "excluded" else "email", 0.9)},
    )
    result = await run(page, client)
    assert await page.evaluate("window.clicks||0") == (state in {"typed", "combobox"})
    assert result.clicked == 0 and result.stop == "ready"


@pytest.mark.parametrize(
    "label",
    [
        "Accept cookies",
        "Agree",
        "Got it",
        "Close",
        "Dismiss",
        "Accepter",
        "J'accepte",
        "Fermer",
        "Tout accepter",
        "Alle akzeptieren",
        "Schließen",
    ],
)
async def test_consent_and_dismiss_controls_allowed_on_form(page, label):
    await page.set_content(f"""<label>Email<input></label>
        <button type="button" onclick="window.clicked=true">{label}</button>""")
    result = await run(page, ScriptedClient([step("CLICK", label), step("DONE")]))
    assert await page.evaluate("window.clicked")
    assert result.clicked == 1


@pytest.mark.parametrize("operation", ["DONE", "WAIT", "unchanged"])
async def test_complete_form_stops_ready_with_submit_target(page, operation):
    await page.set_content("""<label>Email<input></label><div role="tab">Details</div>
        <button type="button">Submit application</button>""")
    tail = [step("CLICK", "Details")] * 3 if operation == "unchanged" else [step(operation)]
    client = ScriptedClient([step("TYPE_TEXT", "Email")] + tail)
    result = await run(page, client)
    assert result.stop == "ready" and result.submit_action["label"] == "Submit application"
    assert result.clicked == (3 if operation == "unchanged" else 0)
    assert len(client.decisions) == 1 + len(tail)


async def test_wait_uploads_all_matching_documents_before_ready(page, documents):
    await page.set_content('<input type="file" name="resume" hidden>')
    result = await run(
        page, ScriptedClient([step("WAIT"), step("WAIT"), step("DONE")]), documents=documents
    )
    assert result.stop == "ready" and result.uploads == ["cv"] and result.steps == 1


async def test_incomplete_form_waits_and_unchanged_steps_still_raise(page):
    await page.set_content('<label>Email<input></label><div role="tab">Details</div>')
    client = ScriptedClient([step("WAIT")] + [step("CLICK", "Details")] * 3)
    with pytest.raises(agent.HarnessStuck, match="unchanged") as exc:
        await run(page, client)
    assert exc.value.result.clicked == 3 and not exc.value.result.typed


@pytest.mark.parametrize(
    "label,stop",
    [
        ("Explore careers", "ready"),
        ("Confirm", "submit_point"),
        ("Postuler", "submit_point"),
    ],
)
async def test_outside_form_allows_clicks_except_strict_submit_words(page, label, stop):
    await page.set_content(f'<button type="button" onclick="window.clicked=true">{label}</button>')
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", label, page_kind="other"),
                step("DONE"),
            ]
        ),
    )
    assert bool(await page.evaluate("window.clicked")) == (stop == "ready")
    assert result.stop == stop and result.clicked == (stop == "ready")


async def test_exception_preserves_click_after_typing_and_submit_evidence(page):
    await page.set_content("""<form onsubmit="event.preventDefault()">
        <label>Email<input></label><button>Next</button></form>""")
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(
            page,
            ScriptedClient(
                [
                    step("TYPE_TEXT", "Email"),
                    step("CLICK", "Next"),
                    step("BLOCKED"),
                ]
            ),
        )
    assert exc.value.result.clicked == 1 and exc.value.result.clicked_submit
    assert len(exc.value.result.typed) == 1


@pytest.mark.parametrize("kind", ["login_wall", "captcha"])
async def test_handoff_timeout_is_independent_of_wall_clock(page, monkeypatch, kind):
    await page.set_content("Sign in")
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.4, raising=False)
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0.02)
    monkeypatch.setattr(agent, "check_and_handle_captcha", AsyncMock(return_value=False))
    client = ScriptedClient([step("DONE", page_kind=kind)], classifications=repeat((kind, 0.9)))
    with pytest.raises(agent.HarnessBlocked, match="hand-off") as exc:
        await run(page, client, notify=AsyncMock(), wall_clock_s=0.2)
    assert type(exc.value) is agent.HarnessBlocked
    assert exc.value.result.clicked == 0


async def test_handoff_polls_do_not_consume_request_budget(page, monkeypatch):
    await page.set_content("Sign in")
    monkeypatch.setattr(agent, "_MAX_REQUESTS", 2)
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    client = ScriptedClient(
        [step("DONE", page_kind="login_wall"), step("DONE")],
        classifications=[("login_wall", 0.9)] * 5 + [("application_form", 0.9)],
    )
    result = await run(page, client, notify=AsyncMock())
    assert result.stop == "ready" and len(client.requests) == 8


async def test_request_budget_cannot_cut_unresolved_owner_handoff(page, monkeypatch):
    await page.set_content("Sign in")
    monkeypatch.setattr(agent, "_MAX_REQUESTS", 2)
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.15, raising=False)
    client = ScriptedClient(
        [step("DONE", page_kind="login_wall")],
        classifications=repeat(("login_wall", 0.9)),
    )
    with pytest.raises(agent.HarnessBlocked, match="hand-off"):
        await run(page, client, notify=AsyncMock())


async def test_wall_clock_resumes_with_remaining_budget_after_handoff(page, monkeypatch):
    await page.set_content("Sign in")
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    client = ScriptedClient(
        [step("DONE", page_kind="login_wall"), step("WAIT", page_kind="other")],
        classifications=[("application_form", 0.9)],
    )

    async def notify(*_args):
        await asyncio.sleep(0.3)

    async def delay_active_work(_body):
        if len(client.decisions) == 1:
            await asyncio.sleep(0.2)
        else:
            await asyncio.sleep(0.3)

    client.before = delay_active_work
    with pytest.raises(agent.HarnessStuck, match="wall-clock") as exc:
        await run(page, client, notify=notify, wall_clock_s=0.4)
    assert len(client.decisions) == 2 and exc.value.result.steps == 0


async def test_cancel_interrupts_owner_handoff(page, monkeypatch):
    await page.set_content("Sign in")
    cancel = asyncio.Event()

    async def notify(*_args):
        cancel.set()

    client = ScriptedClient([step("DONE", page_kind="login_wall")])
    with pytest.raises(agent.HarnessBlocked, match="cancel") as exc:
        await asyncio.wait_for(run(page, client, notify=notify, cancel_event=cancel), timeout=1)
    assert exc.value.result.clicked == 0
    client.aclose.assert_awaited_once()


@pytest.mark.parametrize("attribute", ["name", "id", "aria-label"])
async def test_shared_parent_uploads_use_each_inputs_own_identity(page, documents, attribute):
    await page.set_content(f"""<div><p>Resume</p><input type="file" {attribute}="resume" hidden>
        <p>Cover letter</p><input type="file" {attribute}="cover_letter" hidden></div>""")
    result = await run(page, ScriptedClient([step("WAIT"), step("DONE")]), documents=documents)
    assert await page.locator("input").nth(0).evaluate("e => e.files[0]?.name") == "cv.pdf"
    assert await page.locator("input").nth(1).evaluate("e => e.files[0]?.name") == "letter.pdf"
    assert set(result.uploads) == {"cv", "letter"}


@pytest.mark.parametrize(
    "markup,expected",
    [
        ('<input type="file" accept="application/pdf" hidden>', ["cv.pdf"]),
        ('<input type="file" name="portfolio" accept="application/pdf" hidden>', [None]),
        ('<input type="file" accept="application/pdf" hidden>' * 2, [None, None]),
        ('<div>Resume<input type="file" hidden></div>', ["cv.pdf"]),
        ('<div>Resume<input type="file" name="portfolio" hidden></div>', [None]),
    ],
)
async def test_upload_accept_fallback_only_for_single_unlabelled_input(
    page, documents, markup, expected
):
    await page.set_content(markup)
    await run(page, ScriptedClient([step("WAIT"), step("DONE")]), documents=documents)
    assert (
        await page.locator("input").evaluate_all("es => es.map(e => e.files[0]?.name ?? null)")
        == expected
    )


async def test_writer_none_leaves_field_for_owner(page):
    await page.set_content("<label>Motivation<textarea></textarea></label>")
    result = await run(
        page,
        ScriptedClient(
            [step("TYPE_TEXT", "Motivation"), step("DONE")],
            slots={"Motivation": ("needs_written_answer", 0.9)},
        ),
        writer=AsyncMock(return_value=None),
    )
    assert result.needs_you == ["Motivation"] and not result.written
    assert await page.locator("textarea").input_value() == ""


async def test_newlines_in_written_answer_do_not_submit(page):
    await page.set_content("""<form onsubmit="event.preventDefault();window.submitted=true">
        <label>Motivation<input></label><button>Submit</button></form>""")
    await run(
        page,
        ScriptedClient(
            [step("TYPE_TEXT", "Motivation"), step("DONE")],
            slots={"Motivation": ("needs_written_answer", 0.9)},
        ),
        writer=AsyncMock(return_value="one\ntwo\r\n"),
    )
    assert not await page.evaluate("!!window.submitted")


async def test_click_that_changes_nothing_is_not_offered_again(page):
    """Live OpenJev repeated "Open City" on a combobox that opens only on typing."""
    await page.set_content('<label for="c">City</label><input id="c" role="combobox">')
    offered = []

    class Fake:
        model = "jev-latest"

        async def post(self, body):
            raise AssertionError("no network")

        async def aclose(self):
            pass

    async def fake_decide(client, snap, goal, history):
        offered.append([a["label"] for a in snap["actions"] if a["kind"] == "click"])
        click = next(
            (a for a in snap["actions"] if a["kind"] == "click" and a["label"] == "Open City"), None
        )
        if click is None:
            return Decision("DONE", None, "other", 0.9, None, 0.0, 0.9, 0.0, 1)
        return Decision("CLICK", click, "other", 0.9, None, 0.0, 0.9, 0.9, 1)

    original = agent.decide
    agent.decide = fake_decide
    try:
        try:
            await agent.run_harness(
                page, goal="g", slots={}, documents={}, writer=None, client=Fake(), job_id=None
            )
        except agent.HarnessBlocked:
            pass  # how the run ends is not what this test pins
    finally:
        agent.decide = original
    assert "Open City" in offered[0]
    assert "Open City" not in offered[1]


@pytest.mark.parametrize(
    "label",
    [
        "Accept and apply",
        "OK",
        "Agree & apply",
        "Upload and apply",
        "Apply with resume",
        "Apply with my CV",
        "Discover",
        "Apply and continue",
        "Preview and apply",
        "Accept and proceed",
        "Next application",
        "Recovery",
        "Attachment",
        "Resumed",
        "Upload et postuler",
        "Continue et candidater",
    ],
)
async def test_guard_allow_words_cannot_hide_a_final_click(page, label):
    await page.set_content(
        "<label>Email<input></label>"
        f'<button type="button" onclick="window.submitted=true">{label}</button>'
    )
    result = await run(
        page,
        ScriptedClient([step("TYPE_TEXT", "Email"), step("CLICK", label), step("DONE")]),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.clicked == 0
    assert result.stop == ("submit_point" if agent._STRICT_SUBMIT.search(label) else "ready")
    if "apply" in label.lower():
        assert result.submit_action["label"] == label


@pytest.mark.parametrize(
    "role", ["option", "menuitem", "gridcell", "tab", "checkbox", "radio", "switch", "combobox"]
)
@pytest.mark.parametrize("source", ["name", "text"])
async def test_guard_submit_words_override_non_submit_roles(page, role, source):
    aria = ' aria-label="Choice"' if source == "text" else ""
    label = "Choice" if source == "text" else "Apply"
    await page.set_content(
        "<label>Email<input></label>"
        f'<div role="{role}" tabindex="0"{aria} onclick="window.submitted=true">Apply</div>'
    )
    result = await run(
        page,
        ScriptedClient([step("TYPE_TEXT", "Email"), step("CLICK", label), step("DONE")]),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


@pytest.mark.parametrize("label", ["Next", "Agree", "Upload", "Choice"])
@pytest.mark.parametrize("text", ["Submit application", "Apply", "Review " + "x" * 100 + " submit"])
async def test_guard_checks_unclipped_visible_text_even_with_aria_name(page, label, text):
    await page.set_content(
        "<label>Email<input></label>"
        f'<button type="button" aria-label="{label}" onclick="window.submitted=true">'
        f"{text}<span hidden>hidden text</span></button>"
    )
    result = await run(
        page,
        ScriptedClient([step("TYPE_TEXT", "Email"), step("CLICK", label), step("DONE")]),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.clicked == 0
    assert result.stop == ("ready" if text == "Apply" else "submit_point")
    assert result.submit_action["full_text"] == text


async def test_guard_full_text_stays_local_and_invalidates_stale_click(page):
    text = "Review " + "x" * 6100 + " submit"
    await page.set_content(
        f'<button style="width:300px;height:40px;overflow:hidden" aria-label="Next">{text}</button>'
    )
    snap = await observe(page)
    action = next(a for a in snap["actions"] if a["kind"] == "click")
    assert action["full_text"] == text
    body, _, _ = build_request(snap, "goal", [], "scripted")
    assert "full_text" not in json.dumps(body) and text not in json.dumps(body)
    await page.locator("button").evaluate("e => e.textContent += ' changed'")
    assert not await fresh(page, snap, action)


@pytest.mark.parametrize(
    "kind,p", [("other", 0.9), ("application_form", 0.45), ("job_description", 0.9)]
)
@pytest.mark.parametrize("control", ["radio", "checkbox", "switch", "select", "upload"])
async def test_guard_choices_and_uploads_establish_form_context(page, kind, p, control):
    if control == "select":
        markup = "<label>Country<select><option>A</option><option>B</option></select></label>"
    elif control == "upload":
        markup = '<input type="file" hidden>'
    else:
        markup = f'<div role="{control}" tabindex="0">Yes</div>'
    await page.set_content(markup + '<button onclick="window.submitted=true">Apply</button>')
    result = await run(
        page,
        ScriptedClient(
            [
                step("CLICK", "Apply", page_kind=kind, page_kind_p=p),
                step("DONE"),
            ]
        ),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


@pytest.mark.parametrize("control", ["radio", "checkbox", "switch", "select"])
async def test_guard_remembers_choices_after_controls_disappear(page, control):
    if control == "select":
        markup = (
            '<label>Country<select onchange="this.remove()">'
            '<option>A</option><option>B</option></select></label>'
        )
        choice = step("SELECT", "Country → B", page_kind="other")
    else:
        markup = f'<div role="{control}" tabindex="0" onclick="this.remove()">Yes</div>'
        choice = step("CLICK", "Yes", page_kind="other")
    await page.set_content(markup + '<button onclick="window.submitted=true">Apply</button>')
    result = await run(
        page,
        ScriptedClient([choice, step("CLICK", "Apply", page_kind="other"), step("DONE")]),
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == (control != "select")


async def test_guard_iframe_ok_cannot_submit(page):
    await page.set_content(
        "<label>Email<input></label>"
        '<iframe srcdoc="<button onclick=&quot;parent.submitted=true&quot;>OK</button>"></iframe>'
    )
    await page.frames[1].locator("button").wait_for()
    result = await run(
        page, ScriptedClient([step("TYPE_TEXT", "Email"), step("CLICK", "OK"), step("DONE")])
    )
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


async def test_guard_open_confirm_email_is_a_field_click(page):
    await page.set_content('<label>Confirm email<input onclick="window.opened=true"></label>')
    result = await run(page, ScriptedClient([step("CLICK", "Open Confirm email"), step("DONE")]))
    assert await page.evaluate("!!window.opened")
    assert result.stop == "ready" and result.clicked == 1


@pytest.mark.parametrize(
    "field",
    ["<input aria-label='Apply'>", '<div contenteditable="true" role="textbox">Apply</div>'],
)
async def test_guard_open_field_cannot_bypass_apply(page, field):
    await page.set_content(f'<div onclick="window.submitted=true">{field}</div>')
    result = await run(page, ScriptedClient([step("CLICK", "Open Apply"), step("DONE")]))
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "ready" and result.clicked == 0


@pytest.mark.parametrize("label", ["Apply and confirm", "Postuler et confirmer"])
async def test_guard_open_field_with_strict_word_still_checks_broad_submit(page, label):
    await page.set_content(f'<input aria-label="{label}" onclick="window.submitted=true">')
    result = await run(page, ScriptedClient([step("CLICK", f"Open {label}"), step("DONE")]))
    assert not await page.evaluate("!!window.submitted")
    assert result.stop == "submit_point" and result.clicked == 0


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("error_type", [PlaywrightError, RuntimeError, asyncio.CancelledError])
async def test_guard_counts_click_before_act_can_raise(page, monkeypatch, typed, error_type):
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.submitted=true">'
        "<label>Email<input></label><button>Next</button></form>"
    )
    real_act = agent.act
    error = error_type("failed after click")

    async def failing_act(page_, snap, action, text=None):
        await real_act(page_, snap, action, text)
        if action["kind"] == "click":
            raise error

    monkeypatch.setattr(agent, "act", failing_act)
    steps = [step("TYPE_TEXT", "Email")] if typed else []
    client = ScriptedClient(steps + [step("CLICK", "Next")])
    expected = (
        asyncio.CancelledError if error_type is asyncio.CancelledError else agent.HarnessBlocked
    )
    with pytest.raises(expected) as exc:
        await run(page, client)
    assert await page.evaluate("!!window.submitted")
    assert exc.value.result.clicked == 1 and exc.value.result.clicked_submit
    assert bool(exc.value.result.typed) == typed
    if error_type is asyncio.CancelledError:
        assert exc.value is error
    else:
        assert exc.value.__cause__ is error
    client.aclose.assert_awaited_once()
    assert not page._impl_obj.listeners("filechooser")


async def test_guard_decision_error_carries_completed_work(page):
    await page.set_content("<label>Email<input></label>")
    client = ScriptedClient([step("TYPE_TEXT", "Email")])
    real_post = client.post

    async def post(body):
        if "operation" in body["questions"] and client.decisions:
            raise DecisionError("Model unavailable")
        return await real_post(body)

    client.post = post
    with pytest.raises(agent.HarnessBlocked, match="Model unavailable") as exc:
        await run(page, client)
    assert len(exc.value.result.typed) == 1
    assert exc.value.result.clicked == 0
    assert isinstance(exc.value.__cause__, DecisionError)


async def test_guard_cleanup_error_carries_result(page):
    await page.set_content("<label>Email<input></label>")
    client = ScriptedClient([step("TYPE_TEXT", "Email"), step("DONE")])
    client.aclose.side_effect = RuntimeError("close failed")
    with pytest.raises(agent.HarnessBlocked, match="close failed") as exc:
        await run(page, client)
    assert len(exc.value.result.typed) == 1


@pytest.mark.parametrize("available", ["both", "cv"])
async def test_guard_cover_letter_chooser_uses_opening_control(page, documents, available):
    if available == "cv":
        documents = {"cv": documents["cv"]}
    await page.set_content(
        '<label>Resume<input type="file" name="resume"></label>'
        '<input type="file" id="upload2" hidden>'
        '<button type="button" onclick="upload2.click()">Attach cover letter</button>'
    )
    client = ScriptedClient([step("WAIT"), step("CLICK", "Attach cover letter"), step("DONE")])
    if available == "cv":
        with pytest.raises(agent.HarnessBlocked, match="No document") as exc:
            await run(page, client, documents=documents)
        assert exc.value.result.uploads == ["cv"] and exc.value.result.clicked == 1
    else:
        result = await run(page, client, documents=documents)
        assert result.uploads == ["cv", "letter"]
    assert await page.locator("#upload2").evaluate("e => e.files[0]?.name ?? null") == (
        "letter.pdf" if available == "both" else None
    )


async def test_guard_select_change_autosubmits(page):
    await page.set_content(
        '<form onsubmit="event.preventDefault();window.submitted=true">'
        '<label>Country<select onchange="this.form.requestSubmit()">'
        '<option>a</option><option value="b">b</option></select></label></form>'
    )
    await run(page, ScriptedClient([step("SELECT", "Country → b"), step("DONE")]))
    assert not await page.evaluate("!!window.submitted")
