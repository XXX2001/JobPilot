"""Snapshot and executor contracts against real, offline Chromium pages."""

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from backend.applier.harness.execute import StalePage, act, attach
from backend.applier.harness.snapshot import (
    ADVANCE_PATTERN,
    LABEL_CHARS,
    SUBMIT_PATTERN,
    fresh,
    observe,
)

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module")
async def browser():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        yield browser
        await browser.close()


async def test_browser_fixture_uses_playwright_default_launch(monkeypatch):
    launch = AsyncMock(return_value=AsyncMock())
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(
        chromium=SimpleNamespace(executable_path="/missing/chromium", launch=launch)
    )
    monkeypatch.setitem(globals(), "async_playwright", lambda: manager)
    fixture = browser.__wrapped__()
    try:
        await anext(fixture)
        launch.assert_awaited_once_with(headless=True)
    finally:
        await fixture.aclose()


@pytest_asyncio.fixture(loop_scope="module")
async def page(browser):
    page = await browser.new_page(viewport={"width": 1120, "height": 780})
    yield page
    await page.close()


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


async def load(page, name="greenhouse.html"):
    await page.goto((FIXTURES / name).resolve().as_uri())


def action(snap, kind, label):
    return next(a for a in snap["actions"] if a["kind"] == kind and a["label"] == label)


async def test_observe_numbers_controls_like_jev_ultrafast(page):
    await load(page)
    snap = await observe(page)
    field = action(snap, "fill", "First Name")
    opener = action(snap, "click", "Open First Name")
    assert field["id"] == "e1"
    assert opener["id"] == "e2"
    assert field["node"] == opener["node"]
    assert field["frame"] == 0
    assert field["role"] == "textbox"
    assert field["submits"] is False
    assert action(snap, "click", "Submit Application")["submits"] is True
    assert snap["actions"][-1]["id"] == "wait"
    assert "Apply for Software Engineer" in snap["text"]
    assert snap["fingerprint"] == (await observe(page))["fingerprint"]
    assert await fresh(page, snap)


async def test_hidden_file_input_is_reported_as_upload_not_action(page):
    await load(page)
    snap = await observe(page)
    (upload,) = snap["uploads"]
    assert upload["frame"] == 0
    assert upload["label"] == "Resume / CV"
    assert upload["name"] == "resume"
    assert upload["accept"] == ".pdf,.doc,.docx"
    assert upload["filled"] is False
    assert all(a.get("node") != upload["node"] for a in snap["actions"])
    assert action(snap, "click", "Attach")["submits"] is False


async def test_iframe_fields_get_frame_index_and_unique_ids(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    snap = await observe(page)
    assert action(snap, "fill", "First Name")["frame"] == 1
    assert action(snap, "fill", "Search jobs")["frame"] == 0
    assert len({a["id"] for a in snap["actions"]}) == len(snap["actions"])
    assert len(snap["frames"]) == 2
    assert snap["uploads"][0]["frame"] == 1
    assert "Embedded application form" in snap["text"]
    await page.frames[1].locator("#first_name").fill("Changed")
    assert not await fresh(page, snap)
    assert (await observe(page))["fingerprint"] != snap["fingerprint"]


async def test_shadow_root_fields_are_observed(page):
    await load(page, "shadow.html")
    snap = await observe(page)
    field = action(snap, "fill", "First Name")
    assert "Shadow application form" in snap["text"]
    assert snap["uploads"][0]["name"] == "resume"
    await act(page, snap, field, "Mouad")
    assert await page.locator("#first_name").input_value() == "Mouad"
    snap = await observe(page)
    await act(page, snap, action(snap, "click", "Check details"))
    assert await page.get_by_role("button", name="Checked").count() == 1


async def test_select_offers_at_most_60_options_keeping_current(page):
    await page.set_content('<label>Country <select id="country"></select></label>')
    await page.locator("select").evaluate("""select => {
        for (let i = 0; i < 90; i++) select.add(new Option(`Country ${i}`, `code-${i}`));
        select.value = 'code-89';
    }""")
    snap = await observe(page)
    options = [a for a in snap["actions"] if a["kind"] == "select"]
    assert len(options) == 60
    assert [a["value"] for a in options] == [f"code-{i}" for i in range(60)]
    assert all(a["current_value"] == "Country 89" for a in options)
    assert all("selected" not in a for a in options)


async def test_select_long_question_keeps_yes_no_option_labels(page):
    question = "Question " + "x" * 111
    assert len(question) == 120
    await page.set_content(f"""<label>{question}<select>
        <option selected>Choose</option><option>Yes</option><option>No</option>
        </select></label>""")
    snap = await observe(page)
    for option in ("Yes", "No"):
        offered = next(a for a in snap["actions"] if a.get("value") == option)
        assert offered["label"] == question[: LABEL_CHARS - 3 - len(option)] + " → " + option
        assert len(offered["label"]) == LABEL_CHARS


async def test_select_labels_collapse_whitespace_and_clip_options_separately(page):
    option = "A long option " * 12
    await page.set_content(f"""<label>Work\n  location<select style="width:200px">
        <option selected>Choose</option><option value="long">{option}</option>
        <option value="remote">Fully\n   remote</option></select></label>""")
    snap = await observe(page)
    long = next(a for a in snap["actions"] if a.get("value") == "long")
    assert long["label"] == " → " + option.strip()[: LABEL_CHARS - 3]
    assert len(long["label"]) == LABEL_CHARS
    assert action(snap, "select", "Work location → Fully remote")


@pytest.mark.parametrize("multiple", [False, True])
async def test_select_only_offers_unselected_enabled_options(page, multiple):
    await page.set_content(f"""<label>Country <select {"multiple" if multiple else ""}>
        <option value="current" selected>Current</option>
        <option value="disabled" disabled>Disabled</option>
        <optgroup label="Unavailable" disabled><option value="group">Group</option></optgroup>
        <option value="enabled">Enabled</option></select></label>""")
    if multiple:
        await page.locator('[value="disabled"]').evaluate("e => e.selected = true")
    options = [a for a in (await observe(page))["actions"] if a["kind"] == "select"]
    assert [a["value"] for a in options] == ["enabled"]
    assert options[0]["current_value"] == ("Current, Disabled" if multiple else "Current")
    assert "selected" not in options[0]


@pytest.mark.parametrize("nested,spacer", [(False, 1500), (True, 1500), (True, 650)])
async def test_iframe_field_only_offered_inside_top_viewport(page, fixture_url, nested, spacer):
    source = "iframe_host.html" if nested else "iframe_form.html"
    await page.set_content(f"""<div style="height:{spacer}px">Top of page</div>
        <iframe src="{fixture_url}/{source}" style="width:900px;height:650px"></iframe>""")
    snap = await observe(page)
    assert not any(a["label"] in {"First Name", "Open First Name"} for a in snap["actions"])
    assert len(snap["uploads"]) == 1
    assert snap["uploads"][0]["frame"] == (2 if nested else 1)
    await page.locator("iframe").scroll_into_view_if_needed()
    snap = await observe(page)
    field = action(snap, "fill", "First Name")
    await act(page, snap, field, "Mouad")
    assert await page.frames[field["frame"]].locator("#first_name").input_value() == "Mouad"


async def test_slotted_light_dom_is_in_text_and_accessible_names(page):
    await page.set_content("""<my-card><span>Slotted text here</span>
        <button type="button">Slotted btn</button></my-card>
        <my-button><span>Check details</span></my-button>
        <my-button></my-button>
        <script>
        customElements.define('my-card', class extends HTMLElement {
            constructor() { super(); this.attachShadow({mode:'open'}).innerHTML =
                'Shadow prefix<b>Shadow head</b><slot>Unused card fallback</slot>'; }
        });
        customElements.define('my-button', class extends HTMLElement {
            constructor() { super(); this.attachShadow({mode:'open'}).innerHTML =
                '<button type="button"><slot>Fallback button</slot></button>'; }
        });
        </script>""")
    snap = await observe(page)
    for text in (
        "Shadow prefix",
        "Shadow head",
        "Slotted text here",
        "Slotted btn",
        "Check details",
    ):
        assert snap["text"].count(text) == 1
    assert "Unused card fallback" not in snap["text"]
    assert snap["text"].count("Fallback button") == 1
    assert action(snap, "click", "Slotted btn")
    assert action(snap, "click", "Check details")
    assert action(snap, "click", "Fallback button")


async def test_observe_retries_navigation_before_body_is_available(page, fixture_url):
    script_requested, release_script = asyncio.Event(), asyncio.Event()

    async def delayed_script(route):
        script_requested.set()
        await release_script.wait()
        await route.fulfill(body="", content_type="text/javascript")

    await page.route("**/delay.js", delayed_script)
    await page.route(
        "**/navigation.html",
        lambda route: route.fulfill(
            body='<html><head><script src="/delay.js"></script></head>'
            "<body><label>New page <input></label></body></html>",
            content_type="text/html",
        ),
    )
    await page.goto(f"{fixture_url}/navigation.html", wait_until="commit")
    await asyncio.wait_for(script_requested.wait(), timeout=2)
    assert await page.evaluate("document.body === null")
    release = asyncio.get_running_loop().call_later(0.05, release_script.set)
    try:
        snap = await observe(page)
        assert action(snap, "fill", "New page")
        assert snap["frames"][0] is not None
    finally:
        release.cancel()
        release_script.set()
        await page.wait_for_load_state()


async def test_observe_retries_destroyed_execution_context(page, monkeypatch):
    await load(page)
    evaluate = page.main_frame.evaluate
    reads = 0

    async def interrupted(*args):
        nonlocal reads
        reads += 1
        if reads < 3:
            raise PlaywrightError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        return await evaluate(*args)

    monkeypatch.setattr(page.main_frame, "evaluate", interrupted)
    snap = await observe(page)
    assert action(snap, "fill", "First Name")
    assert reads == 3


@pytest.mark.parametrize("unavailable", [None, PlaywrightError("Execution context was destroyed")])
async def test_observe_raises_stale_after_ten_unavailable_main_reads(
    page, monkeypatch, unavailable
):
    evaluate = AsyncMock(return_value=None, side_effect=unavailable)
    sleep = AsyncMock()
    monkeypatch.setattr(page.main_frame, "evaluate", evaluate)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(StalePage):
        await observe(page)
    assert evaluate.await_count == 10
    assert sleep.await_count == 9
    assert all(call.args == (0.02,) for call in sleep.await_args_list)


async def test_act_fill_replaces_value(page):
    await load(page)
    await page.locator("#first_name").fill("Old name")
    snap = await observe(page)
    await act(page, snap, action(snap, "fill", "First Name"), "Mouad")
    assert await page.locator("#first_name").input_value() == "Mouad"


async def test_act_select_by_value(page):
    await load(page)
    await page.locator("select").evaluate("""select => {
        window.changes = [];
        for (const type of ['input', 'change']) {
            select.addEventListener(type, () => window.changes.push(type));
        }
    }""")
    snap = await observe(page)
    await act(page, snap, action(snap, "select", "Work location → Remote"))
    assert await page.locator("select").input_value() == "remote"
    assert await page.evaluate("window.changes") == ["input", "change"]


async def test_act_click_in_iframe(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    snap = await observe(page)
    await act(page, snap, action(snap, "click", "Check details"))
    assert await page.frames[1].locator("#status").inner_text() == "Checked"


async def test_act_raises_stale_when_node_replaced(page):
    await load(page)
    snap = await observe(page)
    field = action(snap, "fill", "First Name")
    await page.evaluate("window.replaceFirstName()")
    with pytest.raises(StalePage):
        await act(page, snap, field, "Must not be typed")
    assert await page.locator("#first_name").input_value() == ""
    assert action(await observe(page), "fill", "First Name")["node"] != field["node"]


async def test_attach_sets_hidden_input_files(page, tmp_path):
    await load(page)
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture")
    snap = await observe(page)
    await attach(page, snap, snap["uploads"][0], cv)
    assert await page.locator("#resume").evaluate("e => e.files[0].name") == "cv.pdf"
    updated = await observe(page)
    assert updated["uploads"][0]["filled"] is True
    assert updated["fingerprint"] != snap["fingerprint"]


async def test_password_inputs_never_observed(page):
    await load(page, "login.html")
    await page.locator("#password").fill("secret-password")
    snap = await observe(page)
    assert [a["label"] for a in snap["actions"] if a["kind"] == "fill"] == ["Email"]
    assert "secret-password" not in str(snap)
    assert "fixture-token" not in str(snap)


async def test_submit_metadata_covers_advance_buttons_and_non_form_controls(page):
    await page.set_content("""<form><button>Next</button>
        <button type="submit">Continue</button></form>
        <div role="button">Submit application</div><a href="#send">Send application</a>
        <button type="button">Attach</button>""")
    snap = await observe(page)
    for label in ("Next", "Continue", "Submit application", "Send application"):
        assert action(snap, "click", label)["submits"] is True
    assert action(snap, "click", "Attach")["submits"] is False
    assert ADVANCE_PATTERN.search("Étape suivante")
    assert SUBMIT_PATTERN.search("Valider ma candidature")


@pytest.mark.parametrize(
    "control",
    [
        '<input type="submit" value="Go">',
        '<input type="image" alt="Go" style="width:80px;height:30px">',
        "<button>Go</button>",
        '<button type="bogus">Go</button>',
        '<button type="">Go</button>',
    ],
)
async def test_submit_metadata_uses_normalized_control_type(page, control):
    await page.set_content(f"<form>{control}</form>")
    assert action(await observe(page), "click", "Go")["submits"] is True


@pytest.mark.parametrize("input_type", ["submit", "image"])
async def test_submit_inputs_are_flagged_without_form_or_submit_label(page, input_type):
    await page.set_content(
        f'<input type="{input_type}" value="Go" alt="Go" style="width:80px;height:30px">'
    )
    assert action(await observe(page), "click", "Go")["submits"] is True


async def test_submit_button_requires_associated_form(page):
    await page.set_content('<button type="submit">Go</button>')
    assert action(await observe(page), "click", "Go")["submits"] is False
    await page.set_content(
        '<form id="application"></form><button type="bogus" form="application">Go</button>'
    )
    assert action(await observe(page), "click", "Go")["submits"] is True


async def test_fresh_uses_scoped_click_guards_and_full_fill_state(page):
    await page.set_content("""<p id="news">Unrelated news</p><form>
        <label>Name <input></label><button type="button">Check</button></form>""")
    snap = await observe(page)
    await page.locator("#news").evaluate("e => e.textContent = 'Updated news'")
    assert await fresh(page, snap, action(snap, "click", "Check"))
    assert not await fresh(page, snap, action(snap, "fill", "Name"))
    await page.locator("button").evaluate("e => e.textContent = 'Send application'")
    assert not await fresh(page, snap, action(snap, "click", "Check"))


async def test_act_rejects_covered_target(page):
    await load(page, "cookie.html")
    snap = await observe(page)
    with pytest.raises(StalePage):
        await act(page, snap, action(snap, "click", "View application"))
    assert page.url.endswith("cookie.html")


async def test_act_rejects_replaced_frame(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    snap = await observe(page)
    await page.locator("iframe").evaluate("e => e.replaceWith(e.cloneNode(true))")
    await page.frame_locator("iframe").locator("#first_name").wait_for()
    with pytest.raises(StalePage):
        await act(page, snap, action(snap, "fill", "First Name"), "Wrong frame")
    assert await page.frame_locator("iframe").locator("#first_name").input_value() == ""


async def test_attach_rejects_replaced_upload(page, tmp_path):
    await load(page)
    snap = await observe(page)
    await page.locator("#resume").evaluate("e => e.replaceWith(e.cloneNode(true))")
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture")
    with pytest.raises(StalePage):
        await attach(page, snap, snap["uploads"][0], cv)
    assert await page.locator("#resume").evaluate("e => e.files.length") == 0


async def test_act_waits_for_combobox_options(page):
    await load(page, "aria_dropdown.html")
    snap = await observe(page)
    await act(page, snap, action(snap, "fill", "City"), "Par")
    snap = await observe(page)
    await act(page, snap, action(snap, "click", "Paris"))
    assert await page.locator("#city").input_value() == "Paris"


async def test_observe_limits_labels_and_actions_across_frames(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    for frame in page.frames:
        await frame.evaluate("""() => {
            for (let i = 0; i < 140; i++) {
                const button = document.createElement('button');
                button.textContent = 'Long label '.repeat(20);
                button.style = 'position:fixed;top:0;left:0';
                document.body.append(button);
            }
        }""")
    snap = await observe(page)
    controls = [a for a in snap["actions"] if "node" in a]
    assert len(controls) == 250
    assert all(len(a["label"]) <= 100 for a in controls)


async def test_act_rejects_unobserved_action(page):
    await load(page)
    snap = await observe(page)
    forged = {**action(snap, "fill", "First Name"), "node": 999999}
    with pytest.raises(StalePage):
        await act(page, snap, forged, "Wrong target")
    assert await page.locator("#first_name").input_value() == ""


async def test_act_scroll_and_wait(page):
    await page.set_content('<p>Application</p><div style="height:2000px"></div>')
    snap = await observe(page)
    await act(page, snap, action(snap, "scroll", "Scroll down"))
    assert await page.evaluate("scrollY") > 0
    snap = await observe(page)
    await act(page, snap, action(snap, "wait", "Wait for the page to update"))
    assert await fresh(page, snap)


async def test_act_rejects_parent_overlay_at_iframe_target(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    box = await page.frames[1].locator("#check").bounding_box()
    await page.evaluate(
        """box => {
        const cover = document.createElement('div');
        cover.style = `position:fixed;left:${box.x}px;top:${box.y}px;`
            + `width:${box.width}px;height:${box.height}px;background:white;z-index:10`;
        cover.onclick = () => window.coverClicked = true;
        document.body.append(cover);
    }""",
        box,
    )
    snap = await observe(page)
    with pytest.raises(StalePage):
        await act(page, snap, action(snap, "click", "Check details"))
    assert not await page.evaluate("window.coverClicked")


async def test_act_rejects_field_replaced_during_select_all(page):
    await load(page)
    await page.locator("#first_name").evaluate("""field => {
        field.addEventListener('keydown', event => {
            if (event.key.toLowerCase() !== 'a' || !(event.ctrlKey || event.metaKey)) return;
            const replacement = field.cloneNode(true);
            field.replaceWith(replacement);
            replacement.focus();
        });
    }""")
    snap = await observe(page)
    with pytest.raises(StalePage):
        await act(page, snap, action(snap, "fill", "First Name"), "Wrong node")
    assert await page.locator("#first_name").input_value() == ""


async def test_act_scrolls_embedded_form(page, fixture_url):
    await page.goto(f"{fixture_url}/iframe_host.html")
    await page.frames[1].evaluate("document.body.style.height = '2000px'")
    snap = await observe(page)
    scroll = action(snap, "scroll", "Scroll down")
    assert scroll["frame"] == 1
    await act(page, snap, scroll)
    assert await page.frames[1].evaluate("scrollY") > 0
