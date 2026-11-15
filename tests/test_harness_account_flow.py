"""Account preparation uses routed Chromium pages and captures every model request."""

import json
import logging
import traceback
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote, quote_plus

import pytest
import pytest_asyncio
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, async_playwright

from backend.applier.harness import account_flow, accounts, agent, mailbox
from backend.applier.harness.account_flow import AccountFlow, account_page, fill_passwords
from backend.scraping.site_prompts import SITE_CONFIGS
from tests.test_harness_agent import FIXTURES, PROFILE, ScriptedClient, run, step

pytestmark = pytest.mark.asyncio(loop_scope="module")
SECRET = "account-only-Secret-8472!"
HOST = "careers.example.com"
URL = f"https://{HOST}"
APPLICATION = "<label>Motivation<textarea></textarea></label><button>Postuler</button>"


@pytest_asyncio.fixture(scope="module")
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture(loop_scope="module")
async def page(browser):
    context = await browser.new_context()
    pages = {
        "/signin": "account_signin.html",
        "/signup": "account_signup.html",
        "/verify": "account_verify_code.html",
        "/combined": "account_and_apply.html",
    }

    pages.update(
        {
            "/" + path.stem.removeprefix("account_probe_"): path.name
            for path in FIXTURES.glob("account_probe_*.html")
        }
    )

    async def route(request):
        from urllib.parse import urlsplit

        url = urlsplit(request.request.url)
        name = pages.get(url.path)
        await request.fulfill(
            body=(FIXTURES / name).read_text()
            if name
            else ("<p>gsi</p>" if url.hostname == "accounts.google.com" else APPLICATION),
            content_type="text/html",
        )

    await context.route("**/*", route)
    page = await context.new_page()
    yield page
    await context.close()


@pytest.fixture
def services(monkeypatch):
    registry = SimpleNamespace(
        site_key=accounts.site_key,
        find_account=AsyncMock(return_value=None),
        new_accounts_today=AsyncMock(return_value=0),
        record_account=AsyncMock(),
    )
    mail = SimpleNamespace(wait_for_verification=AsyncMock(return_value=None))
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.03)
    return registry, mail


def flow(services, password=SECRET):
    registry, mail = services
    return AccountFlow(
        password=password, email=PROFILE[1], company="Example", registry=registry, mailbox=mail
    )


def signin_steps(button="Connexion"):
    return [
        step("TYPE_TEXT", "Email", page_kind="login_wall"),
        step("CLICK", button, page_kind="login_wall"),
        step("CLICK", "Postuler"),
    ]


def assert_private(client, result, caplog, *other):
    assert SECRET not in json.dumps(client.requests)
    assert SECRET not in repr(result)
    assert SECRET not in caplog.text
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    assert SECRET not in repr(other)


async def test_registry_signin_fills_password_only_in_password_input(page, services, caplog):
    registry, _ = services
    registry.find_account.return_value = object()
    await page.goto(URL + "/signin")
    seen = []

    async def before(_body):
        seen.append(await page.locator("input[type=password]").input_value())

    client = ScriptedClient(
        signin_steps(), before=lambda body: before(body) if len(seen) < 2 else AsyncMock()()
    )
    result = await run(page, client, account_flow=flow(services))
    assert seen == [SECRET, SECRET]
    assert result.stop == "submit_point" and result.clicked_submit
    assert "account form" in client.decisions[0]["questions"]["operation"]["instructions"]["goal"]
    registry.record_account.assert_awaited_once_with(
        HOST, method="password", email=PROFILE[1], status="signed_in", login_url=URL + "/signin"
    )
    assert_private(client, result, caplog)


@pytest.mark.parametrize("start", ["/signin", "/signup"])
async def test_signup_switches_first_and_ticks_only_terms(page, services, caplog, start):
    await page.goto(URL + start)
    observed = []

    async def before(_body):
        if page.url.endswith("/signup"):
            observed.append(
                await page.locator("input[type=password]").evaluate_all(
                    "es => es.map(e => e.value)"
                )
            )
            assert await page.locator("#terms").is_checked()
            assert not await page.locator("#newsletter").is_checked()

    client = ScriptedClient(signin_steps("Créer un compte"), before=before)
    result = await run(page, client, account_flow=flow(services))
    assert observed and all(values == [SECRET, SECRET] for values in observed)
    assert result.chosen == ["J'accepte les conditions et la confidentialité"]
    assert services[0].record_account.await_args.kwargs["status"] == "created"
    assert_private(client, result, caplog)


async def test_combined_account_application_keeps_submit_guard(page, services):
    await page.goto(URL + "/combined")
    assert await account_page(page) is None
    result = await run(
        page, ScriptedClient([step("CLICK", "Postuler")]), account_flow=flow(services)
    )
    assert result.stop == "submit_point" and result.clicked == 0
    assert await page.locator("input[type=password]").input_value() == ""
    assert not await page.evaluate("!!window.submitted")
    services[0].find_account.assert_not_awaited()


@pytest.mark.parametrize(
    "extra", ["<textarea></textarea>", "<textarea hidden></textarea>", '<input type="file">']
)
async def test_application_fields_disqualify_across_frames(page, extra):
    await page.goto(URL + "/signin")
    await page.locator("body").evaluate(
        "(e, extra) => e.insertAdjacentHTML('beforeend', extra)",
        f'<iframe srcdoc="{extra.replace(chr(34), chr(39))}"></iframe>',
    )
    await page.frames[1].wait_for_load_state()
    assert await account_page(page) is None


async def test_only_visible_passwords_in_same_origin_frames_are_filled(page):
    await page.goto(URL + "/signin")
    await page.locator("body").evaluate("""e => e.insertAdjacentHTML('beforeend',
      `<input type=password hidden><input type=text id=plain>
       <iframe srcdoc='<input type=password><input type=password value=existing>'></iframe>
       <iframe src='https://elsewhere.example/signin'></iframe>`)""")
    await page.frames[-1].wait_for_load_state()
    assert await fill_passwords(page, SECRET) == 2
    assert await page.locator("#plain").input_value() == ""
    assert await page.locator("input[hidden]").input_value() == ""
    assert await page.frames[1].locator("input").nth(1).input_value() == "existing"
    assert await page.frames[2].locator("input[type=password]").input_value() == ""


async def test_password_fill_exception_does_not_expose_value(page, monkeypatch, caplog):
    await page.goto(URL + "/signin")
    monkeypatch.setattr(
        Locator, "fill", AsyncMock(side_effect=PlaywrightError("fill timeout with value " + SECRET))
    )
    with pytest.raises(
        account_flow.AccountError, match="password field could not be filled"
    ) as exc:
        await fill_passwords(page, SECRET)
    rendered = "".join(traceback.format_exception(exc.value))
    logging.getLogger(__name__).warning("Account preparation failed: %s", exc.value)
    assert SECRET not in rendered + caplog.text + repr(exc.value)


@pytest.mark.parametrize(
    "label", ["Connexion", "Anmelden", "Me connecter", "S’identifier", "Login", "Einloggen"]
)
async def test_account_clicks_override_strict_submit_only_on_account_page(page, services, label):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("button").evaluate("(e, label) => e.textContent=label", label)
    result = await run(page, ScriptedClient(signin_steps(label)), account_flow=flow(services))
    assert page.url.endswith("/application") and result.stop == "submit_point"


@pytest.mark.parametrize(
    "label",
    [
        "newsletter",
        "marketing",
        "offres",
        "alertes",
        "partenaires",
        "promotional",
        "job alert",
        "I agree to promotional terms",
    ],
)
async def test_model_cannot_tick_optins_even_with_terms_words(page, services, label):
    await page.goto(URL + "/signup")
    await page.locator("#newsletter").evaluate(
        "(e, label) => e.parentElement.lastChild.textContent=label", label
    )
    client = ScriptedClient(
        [
            step("TYPE_TEXT", "Email"),
            step("CLICK", label),
            step("CLICK", "Créer un compte"),
            step("CLICK", "Postuler"),
        ]
    )
    checked = []

    async def before(_body):
        if page.url.endswith("/signup"):
            checked.append(await page.locator("#newsletter").is_checked())

    client.before = before
    result = await run(page, client, account_flow=flow(services))
    assert not any(checked) and label not in result.chosen
    assert page.url.endswith("/application")


@pytest.mark.parametrize(
    "host",
    [
        "google.com",
        "accounts.google.com",
        "mail.google.com",
        "www.linkedin.com",
        "login.owner.test",
    ],
)
async def test_owner_login_hosts_never_get_account_behaviour(page, services, monkeypatch, host):
    monkeypatch.setitem(
        SITE_CONFIGS, "owner-test", {"url": "https://owner.test", "requires_login": True}
    )
    await page.goto(f"https://{host}/signin")
    notify = AsyncMock()
    with pytest.raises(agent.HarnessBlocked):
        await run(
            page,
            ScriptedClient([step("CLICK", "Connexion", page_kind="login_wall")]),
            account_flow=flow(services),
            notify=notify,
        )
    assert await page.locator("input[type=password]").input_value() == ""
    services[0].find_account.assert_not_awaited()
    services[0].record_account.assert_not_awaited()
    assert notify.await_args.args[0] == "login"


async def test_invalid_site_key_hands_off(page, services):
    await page.goto(URL + "/signin")
    services[0].site_key = lambda _url: (_ for _ in ()).throw(ValueError("Invalid job-site URL"))
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(page, ScriptedClient([]), account_flow=flow(services))
    assert exc.value.result.login_host == HOST
    services[0].record_account.assert_not_awaited()


@pytest.mark.parametrize("existing", [False, True])
async def test_daily_cap_blocks_only_new_accounts(page, services, existing):
    services[0].new_accounts_today.return_value = 10
    services[0].find_account.return_value = object() if existing else None
    await page.goto(URL + "/signin")
    client = ScriptedClient(signin_steps() if existing else [])
    if existing:
        assert (await run(page, client, account_flow=flow(services))).stop == "submit_point"
    else:
        notify = AsyncMock()
        with pytest.raises(agent.HarnessBlocked):
            await run(page, client, account_flow=flow(services), notify=notify)
        assert "10" in notify.await_args.args[1]
        assert await page.locator("input[type=password]").input_value() == ""


@pytest.mark.parametrize("existing", [False, True])
async def test_error_switches_mode_once_then_records_failed(page, services, existing):
    services[0].find_account.return_value = object() if existing else None

    async def error_route(route):
        name = (
            "account_signin.html"
            if route.request.url.endswith("/signin")
            else "account_signup.html"
        )
        html = (FIXTURES / name).read_text()
        start = html.index('onsubmit="')
        end = html.index('"', start + len('onsubmit="'))
        html = (
            html[:start] + 'onsubmit="event.preventDefault(); '
            "document.getElementById('error').textContent='Incorrect / already exists'" + html[end:]
        )
        await route.fulfill(body=html + '<p id="error"></p>', content_type="text/html")

    await page.context.route("**/signin", error_route)
    await page.context.route("**/signup", error_route)
    await page.goto(URL + ("/signin" if existing else "/signup"))
    buttons = ["Connexion", "Créer un compte"] if existing else ["Créer un compte", "Connexion"]
    client = ScriptedClient([s for button in buttons for s in signin_steps(button)[:2]])
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(page, client, account_flow=flow(services))
    assert services[0].record_account.await_args.kwargs["status"] == "failed"
    assert len(client.decisions) == 4 and exc.value.result.login_host == HOST


@pytest.mark.parametrize("verification", ["code", "link", "none"])
async def test_verification_uses_signup_click_time_and_reobserves(
    page, services, verification, caplog
):
    registry, mail = services
    await page.goto(URL + "/signup")
    await page.locator("form").evaluate(
        "e => e.onsubmit = event => {event.preventDefault(); location.href='/verify'}"
    )
    if verification == "code":
        mail.wait_for_verification.return_value = mailbox.Verification(code="482913")
    else:
        await page.context.route(
            "**/verify",
            lambda route: route.fulfill(
                body="<p>Check your email to activate your account</p>", content_type="text/html"
            ),
        )
        if verification == "link":
            mail.wait_for_verification.return_value = mailbox.Verification(link=URL + "/activate")
    clicks = []
    new_pages = []
    page.context.on("page", lambda new: new_pages.append(new))

    async def before(body):
        if len(client.decisions) == 2:
            clicks.append(datetime.now(UTC))
        if page.url.endswith("/verify") and verification == "code":
            assert await page.locator("#code").input_value() == "482913"

    client = ScriptedClient(
        signin_steps("Créer un compte")[:2]
        + (
            [step("CLICK", "Vérifier", page_kind="login_wall"), step("CLICK", "Postuler")]
            if verification == "code"
            else []
        ),
        before=before,
    )
    notify = AsyncMock()
    if verification == "link":
        original = page.bring_to_front
        reload = page.reload
        activated = False

        async def activate(route):
            nonlocal activated
            activated = True
            await route.fulfill(body="Account activated", content_type="text/html")

        await page.context.route("**/activate", activate)
        await page.context.route(
            "**/verify",
            lambda route: route.fulfill(
                body=APPLICATION if activated else "<p>Check your email</p>",
                content_type="text/html",
            ),
        )

        async def front():
            assert new_pages and new_pages[-1] is not page
            await original()

        page.bring_to_front = AsyncMock(side_effect=front)
        page.reload = AsyncMock(side_effect=reload)
        client.steps = iter(signin_steps("Créer un compte")[:2] + [step("CLICK", "Postuler")])
    if verification == "none":
        with pytest.raises(agent.HarnessBlocked) as exc:
            await run(page, client, account_flow=flow(services), notify=notify)
        result = exc.value.result
        assert "check your email" in notify.await_args.args[1].lower()
    else:
        result = await run(page, client, account_flow=flow(services), notify=notify)
        assert registry.record_account.await_args.kwargs["status"] == "verified"
        if verification == "link":
            page.reload.assert_awaited_once_with(wait_until="domcontentloaded")
    call = mail.wait_for_verification.await_args
    assert call.args == (page.context,)
    assert call.kwargs["site_host"] == HOST and call.kwargs["company"] == "Example"
    assert clicks[0] <= call.kwargs["since"] <= datetime.now(UTC)
    assert call.kwargs["since"].utcoffset().total_seconds() == 0
    assert_private(client, result, caplog, notify.await_args_list)


async def test_code_or_email_prompt_without_signup_is_not_an_account_page(page, services):
    await page.goto(URL + "/verify")
    assert await account_page(page) is None
    assert await account_page(page, after_signup=True) is not None
    services[1].wait_for_verification.assert_not_awaited()


@pytest.mark.parametrize(
    "markup,detected",
    [
        ("<input type=password hidden>", False),
        ("<button>Sign in with Google</button>", False),
        ("<button>Sign in with Google</button><textarea></textarea>", False),
        ("<input type=password><input type=file hidden>", False),
    ],
)
async def test_detection_requires_visible_account_controls(page, markup, detected):
    await page.goto(URL + "/application")
    await page.set_content(markup)
    assert (await account_page(page) is not None) is detected


async def test_unset_password_hands_off_without_model_or_registry(page, services, caplog):
    await page.goto(URL + "/signin")
    client, notify = ScriptedClient([]), AsyncMock()
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(page, client, account_flow=flow(services, password=None), notify=notify)
    assert "Sign-in needed on " + HOST in notify.await_args.args[1]
    services[0].find_account.assert_not_awaited()
    assert await page.locator("input[type=password]").input_value() == ""
    assert_private(client, exc.value.result, caplog, notify.await_args_list)


async def test_page_echo_cannot_leak_into_requests_or_result(page, services, caplog):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("input[type=password]").evaluate("""e => e.oninput = () => {
        document.title=e.value;
        let echo=document.querySelector('#echo');
        if (!echo) {echo=document.createElement('p'); echo.id='echo'; document.body.append(echo)}
        echo.textContent=e.value;
    }""")
    client = ScriptedClient(signin_steps())
    result = await run(page, client, account_flow=flow(services))
    assert "[redacted]" in json.dumps(client.requests)
    assert_private(client, result, caplog)


async def test_tenth_account_can_finish_verification(page, services):
    registry, mail = services
    registry.new_accounts_today.side_effect = lambda: (
        10 if registry.record_account.await_count else 9
    )
    mail.wait_for_verification.return_value = mailbox.Verification(code="482913")
    await page.goto(URL + "/signup")
    await page.locator("form").evaluate(
        "e => e.onsubmit=event => {event.preventDefault();location.href='/verify'}"
    )
    client = ScriptedClient(
        signin_steps("Créer un compte")[:2]
        + [step("CLICK", "Vérifier", page_kind="login_wall"), step("CLICK", "Postuler")]
    )
    result = await run(page, client, account_flow=flow(services))
    assert result.stop == "submit_point"
    assert registry.record_account.await_args.kwargs["status"] == "verified"


async def test_new_account_without_signup_route_is_handed_off(page, services):
    await page.goto(URL + "/signin")
    await page.locator("a").evaluate("e => e.remove()")
    notify = AsyncMock()
    with pytest.raises(agent.HarnessBlocked):
        await run(page, ScriptedClient([]), account_flow=flow(services), notify=notify)
    notify.assert_awaited_once()
    services[0].record_account.assert_not_awaited()


async def test_successful_retry_is_recorded_with_final_mode(page, services):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("form").evaluate("""e => e.onsubmit=event => {
        event.preventDefault(); let p=document.createElement('p');
        p.textContent='Password incorrect'; document.body.append(p);
    }""")
    client = ScriptedClient(signin_steps()[:2] + signin_steps("Créer un compte"))
    result = await run(page, client, account_flow=flow(services))
    assert result.stop == "submit_point"
    assert services[0].record_account.await_args.kwargs["status"] == "created"


async def test_registry_integration_preserves_tenant_and_last_used(page, services, caplog):
    from backend.utils.time import naive_utc_now

    services = (accounts, services[1])
    account = flow(services)
    url = "https://career2.successfactors.eu/signin?company=Example"
    before = naive_utc_now()
    await accounts.record_account(
        accounts.site_key(url), method="password", email=PROFILE[1], status="created", login_url=url
    )
    await page.goto(url)
    client = ScriptedClient(signin_steps())
    result = await run(page, client, account_flow=account)
    row = await accounts.find_account(accounts.site_key(url))
    assert row.status == "signed_in" and row.email == PROFILE[1]
    assert row.last_used_at >= before and row.login_url == url
    assert_private(client, result, caplog, row)


async def test_account_error_without_form_does_not_record_success(page, services):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("form").evaluate("""e => e.onsubmit=event => {
        event.preventDefault(); document.body.innerHTML='<p>Incorrect credentials</p>';
    }""")
    notify = AsyncMock()
    with pytest.raises(agent.HarnessBlocked):
        await run(
            page, ScriptedClient(signin_steps()[:2]), account_flow=flow(services), notify=notify
        )
    assert services[0].record_account.await_args.kwargs["status"] == "failed"
    notify.assert_awaited_once()


async def test_plain_code_field_is_recognised_only_after_signup(page):
    await page.goto(URL + "/application")
    await page.set_content('<label>Code<input name="code"></label>')
    assert await account_page(page) is None
    assert (await account_page(page, after_signup=True)).code_field


async def test_optin_accessible_name_cannot_hide_its_visible_label(page, services):
    await page.goto(URL + "/signup")
    await page.locator("#newsletter").evaluate("e => e.setAttribute('aria-label', 'Choice')")
    checked = []

    async def before(_body):
        if page.url.endswith("/signup"):
            checked.append(await page.locator("#newsletter").is_checked())

    client = ScriptedClient(
        [
            step("TYPE_TEXT", "Email"),
            step("CLICK", "Choice"),
            step("CLICK", "Créer un compte"),
            step("CLICK", "Postuler"),
        ],
        before=before,
    )
    result = await run(page, client, account_flow=flow(services))
    assert not any(checked) and "Choice" not in result.chosen


async def test_captcha_handoff_resumes_account_path_after_owner_solves_it(
    page, services, monkeypatch
):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("input[type=email]").evaluate("e => e.remove()")
    monkeypatch.setattr(agent, "check_and_handle_captcha", AsyncMock(return_value=False))
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0)
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.5)
    client = ScriptedClient(
        [
            step("DONE", page_kind="captcha"),
            step("CLICK", "Connexion", page_kind="login_wall"),
            step("CLICK", "Postuler"),
        ],
        classifications=[("login_wall", 0.9)],
    )
    notify = AsyncMock()
    result = await run(page, client, account_flow=flow(services), notify=notify)
    assert result.stop == "submit_point"
    assert notify.await_args.args[0] == "captcha"
    assert services[0].record_account.await_args.kwargs["status"] == "signed_in"


@pytest.mark.parametrize(
    "path,button,is_account",
    [
        ("/hidden_file", "Submit", False),
        ("/review", "Valider", True),
        ("/editable", "Submit", False),
    ],
)
async def test_critic_pseudo_account_pages_never_submit(page, services, path, button, is_account):
    services[0].find_account.return_value = object()
    await page.goto(URL + path)
    assert (await account_page(page) is not None) is is_account
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Email"),
                step("CLICK", button),
                step("DONE"),
            ]
        ),
        account_flow=flow(services),
    )
    assert result.stop == "submit_point"
    assert not result.clicked_submit and result.clicked == 0
    assert not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("google", ["iframe", "button"])
async def test_critic_google_alone_does_not_hand_off(page, services, google):
    await page.goto(URL + "/onetap")
    if google == "iframe":
        await page.frames[1].wait_for_load_state()
    else:
        await page.locator("iframe").evaluate(
            "e => e.outerHTML='<button>Sign in with Google</button>'"
        )
    assert await account_page(page) is None
    notify = AsyncMock()
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "First Name"),
                step("DONE"),
            ]
        ),
        account_flow=flow(services),
        notify=notify,
    )
    assert result.stop == "ready"
    notify.assert_not_awaited()
    services[0].find_account.assert_not_awaited()


async def test_critic_german_account_and_application_prompts_are_not_errors(page, services):
    await page.goto(URL + "/german_signup")
    result = await run(
        page,
        ScriptedClient(
            [
                step("TYPE_TEXT", "Email"),
                step("CLICK", "Registrieren"),
                step("CLICK", "Bewerben"),
            ]
        ),
        account_flow=flow(services),
    )
    assert result.stop == "submit_point" and result.clicked_submit
    services[0].record_account.assert_awaited_once()
    assert services[0].record_account.await_args.kwargs["status"] == "created"


@pytest.mark.parametrize("password", [SECRET, "secret / + é?&!"])
async def test_critic_get_signin_url_is_redacted_in_decision_request(page, services, password):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/get_signin")
    client = ScriptedClient(signin_steps("Sign in"))
    result = await run(page, client, account_flow=flow(services, password))
    assert "pwd=" in page.url
    request = json.dumps(client.decisions[-1])
    assert "[redacted]" in request
    assert password not in request + repr(result)
    assert quote_plus(password) not in request


@pytest.mark.parametrize("encoding", [quote, quote_plus, lambda s: quote(s, safe="")])
@pytest.mark.parametrize("case", ["upper", "lower", "mixed"])
async def test_clean_handles_percent_encoding_case_without_changing_password_case(
    services, encoding, case
):
    import re

    password = "Secret / + é?&!"
    encoded = encoding(password)
    if case == "lower":
        encoded = re.sub(r"%[0-9A-F]{2}", lambda m: m[0].lower(), encoded)
    elif case == "mixed":
        encoded = re.sub(r"%([0-9A-F])([0-9A-F])", lambda m: "%" + m[1].lower() + m[2], encoded)
    value = {"url": "https://example.com/?pwd=" + encoded, "raw": password}
    assert flow(services, password).clean(value) == {
        "url": "https://example.com/?pwd=[redacted]",
        "raw": "[redacted]",
    }


async def test_critic_three_marketing_terms_are_never_checked(page):
    await page.goto(URL + "/terms")
    assert await account_flow.tick_required_terms(page) == []
    assert await page.locator("input[type=checkbox]:checked").count() == 0


@pytest.mark.parametrize(
    "label",
    [
        "I agree to receive emails about new jobs",
        "J'accepte de recevoir des communications de nos recruteurs",
        "Ich stimme zu, Neuigkeiten per E-Mail zu erhalten",
        "I agree to e-mails",
        "I agree to actualités",
        "I agree to news",
        "I agree to updates",
        "I agree to informiert",
        "I agree to talent community",
        "I agree to talent pool",
        "I agree to vivier",
        "I agree to offres similaires",
        "I agree to future opportunities",
    ],
)
async def test_extended_marketing_labels_block_code_and_model(page, services, label):
    await test_model_cannot_tick_optins_even_with_terms_words(page, services, label)


@pytest.mark.parametrize(
    "host",
    [
        "www.welcometothejungle.com",
        "login.live.com",
        "login.yahoo.com",
        "google.fr",
        "accounts.google.co.uk",
        "gmail.com",
        "outlook.com",
        "outlook.fr",
        "login.microsoft.com",
        "login.microsoftonline.com",
        "yahoo.co.jp",
        "proton.me",
        "mail.protonmail.com",
        "protonmail.ch",
        "apple.com",
        "www.icloud.com",
    ],
)
async def test_critic_wttj_and_identity_providers_excluded(page, services, monkeypatch, host):
    assert not account_flow.account_allowed(f"https://{host}/signin")
    await test_owner_login_hosts_never_get_account_behaviour(page, services, monkeypatch, host)


@pytest.mark.parametrize(
    "label",
    [
        "Connexion",
        "Se connecter",
        "Me connecter",
        "S'identifier",
        "Sign in",
        "Log in",
        "Login",
        "Anmelden",
        "Einloggen",
        "Créer un compte",
        "Créer mon compte",
        "S'inscrire",
        "Inscription",
        "Create account",
        "Create an account",
        "Register",
        "Sign up",
        "Registrieren",
        "Konto erstellen",
    ],
)
async def test_only_explicit_whole_account_labels_are_relaxed(page, services, label):
    signup = label in {
        "Créer un compte",
        "Créer mon compte",
        "S'inscrire",
        "Inscription",
        "Create account",
        "Create an account",
        "Register",
        "Sign up",
        "Registrieren",
        "Konto erstellen",
    }
    services[0].find_account.return_value = None if signup else object()
    for suffix in ("", " and apply"):
        await page.goto(URL + ("/signup" if signup else "/signin"))
        await page.locator("button").evaluate("(e,s) => e.textContent=s", label + suffix)
        result = await run(
            page,
            ScriptedClient(signin_steps(label + suffix)),
            account_flow=flow(services),
        )
        assert result.clicked_submit is (not suffix)
        assert result.clicked == (0 if suffix else 1)


@pytest.mark.parametrize(
    "label", ["Submit", "Valider", "Continue", "Continuer", "Suivant", "Next", "Weiter"]
)
async def test_generic_controls_keep_the_existing_guard(page, services, label):
    assert not account_flow.SIGNIN_WORDS.fullmatch(label)
    assert not account_flow.SIGNUP_WORDS.fullmatch(label)
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("button").evaluate("(e,s) => e.textContent=s", label)
    result = await run(page, ScriptedClient(signin_steps(label)), account_flow=flow(services))
    if label in {"Submit", "Valider"}:
        assert page.url.endswith("/signin") and result.clicked == 0
        assert result.login_host == HOST
    else:
        # Existing advance controls retain their existing behaviour, without an override.
        assert result.clicked_submit


@pytest.mark.parametrize("label", ["Vérifier", "Verify", "Confirm", "Bestätigen"])
@pytest.mark.parametrize("extra", ["", "<label>Email<input type=email></label>"])
async def test_verify_words_relaxed_only_for_a_sole_code_input(page, services, label, extra):
    await page.goto(URL + "/verify")
    await page.locator("button").evaluate("(e,s) => e.textContent=s", label)
    await page.locator("form").evaluate("(e,s) => e.insertAdjacentHTML('beforeend',s)", extra)
    await page.locator("form").evaluate(
        "e => e.onsubmit=event => {event.preventDefault();location.href='/application'}"
    )
    acct = flow(services)
    acct.signup_since = datetime.now(UTC)
    acct.verification_read = True
    await page.locator("#code").fill("482913")
    client = ScriptedClient([step("CLICK", label), step("DONE")])
    assert (await account_page(page, after_signup=True) is not None) is (not extra)
    result = await run(page, client, account_flow=acct)
    assert result.clicked_submit is (not extra)
    assert page.url.endswith("/verify" if extra else "/application")


@pytest.mark.parametrize(
    "field",
    [
        "<input type=file hidden>",
        "<textarea hidden></textarea>",
        "<div contenteditable=false></div>",
        "<label>Salary<input></label>",
        "<label>Experience<select><option>5</option></select></label>",
        "<input type=date>",
        "<input type=radio>",
        "<div role=textbox>Motivation</div>",
    ],
)
@pytest.mark.parametrize("container", ["main", "shadow", "hidden_frame", "cross_origin_frame"])
async def test_any_non_account_field_disqualifies_everywhere(page, field, container):
    await page.goto(URL + "/signin")
    if container == "main":
        await page.locator("body").evaluate("(e,s) => e.insertAdjacentHTML('beforeend',s)", field)
    elif container == "shadow":
        await page.evaluate(
            "s => {const e=document.createElement('div'); document.body.append(e);"
            "e.attachShadow({mode:'open'}).innerHTML=s}",
            field,
        )
    else:
        await page.context.route(
            "https://other.example/**",
            lambda r: r.fulfill(
                body=field,
                content_type="text/html",
            ),
        )
        await page.evaluate(
            "hidden => {const e=document.createElement('iframe');"
            "e.src='https://other.example/'; e.hidden=hidden; document.body.append(e)}",
            container == "hidden_frame",
        )
        await page.frames[1].wait_for_load_state()
    assert await account_page(page) is None


async def test_allowed_profile_fields_do_not_disqualify_account_page(page):
    await page.goto(URL + "/signup")
    await page.locator("form").evaluate("""e => e.insertAdjacentHTML('beforeend',
        '<label>Username<input></label><label>First Name<input></label>' +
        '<label>Last Name<input></label>' +
        '<label>Full Name<input></label><label>Phone<input type=tel></label>' +
        '<label>Country<select><option>France</option></select></label>')""")
    assert await account_page(page) is not None


@pytest.mark.parametrize("editable", ["true", "false"])
async def test_even_a_sole_code_input_cannot_override_contenteditable(page, editable):
    await page.goto(URL + "/verify")
    await page.locator("#code").evaluate(
        "(e,value) => e.setAttribute('contenteditable', value)",
        editable,
    )
    assert await account_page(page, after_signup=True) is None


@pytest.mark.parametrize("history", ["upload", "non_account", "account"])
async def test_run_remembers_application_work_before_later_account_shape(
    page, services, tmp_path, history
):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    if history == "upload":
        markup = "<input type=file name=resume onchange=\"location.href='/signin'\">"
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF-1.4")
        documents = {"cv": cv}
        steps = [step("WAIT")]
    else:
        label = "Email" if history == "account" else "Motivation"
        markup = f"<label>{label}<input oninput=\"location.href='/signin'\"></label>"
        documents = {}
        steps = [step("TYPE_TEXT", label)]
    await page.set_content(markup)
    client = ScriptedClient(
        steps + [step("CLICK", "Connexion"), step("DONE")],
        slots={"Motivation": ("full_name", 0.9), "Email": ("email", 0.9)},
    )
    if history == "account":
        # The new form still requires its email, so finish the account step with DONE/hand-off.
        with pytest.raises(agent.HarnessBlocked) as exc:
            await run(page, client, account_flow=flow(services), documents=documents)
        assert exc.value.result.clicked_submit
        services[0].find_account.assert_awaited()
    else:
        result = await run(page, client, account_flow=flow(services), documents=documents)
        assert result.clicked == 0 and not result.clicked_submit
        services[0].find_account.assert_not_awaited()
        assert await page.locator("input[type=password]").input_value() == ""


@pytest.mark.parametrize("attributes", ["", 'aria-labelledby="missing"'])
async def test_unlabelled_checkbox_is_never_checked_by_code_or_model(page, services, attributes):
    await page.goto(URL + "/signup")
    await page.locator("#newsletter").evaluate(
        "(e,s) => e.parentElement.outerHTML='<input id=unknown type=checkbox '+s+'>'",
        attributes,
    )
    assert await account_flow.tick_required_terms(page) == [
        "J'accepte les conditions et la confidentialité"
    ]
    from backend.applier.harness.decide import Decision

    async def decide(client, snap, goal, history):
        action = next(
            (
                a
                for a in snap["actions"]
                if a["kind"] == "click" and a["role"] == "checkbox" and a.get("checked") != "true"
            ),
            None,
        )
        return Decision(
            "CLICK" if action else "DONE", action, "login_wall", 0.9, None, 0, 0.9, 0.9, 0
        )

    # Use the observed node directly: an empty checkbox has no ScriptedClient label.
    from unittest.mock import patch

    with patch.object(agent, "decide", decide), pytest.raises(agent.HarnessBlocked):
        await run(page, ScriptedClient([]), account_flow=flow(services))
    assert not await page.locator("#unknown").is_checked()


async def test_checkbox_playwright_error_is_wrapped_without_secrets(page, monkeypatch):
    await page.goto(URL + "/signup")
    monkeypatch.setattr(Locator, "check", AsyncMock(side_effect=PlaywrightError(SECRET)))
    with pytest.raises(account_flow.AccountError, match="terms could not be checked") as exc:
        await account_flow.tick_required_terms(page)
    assert SECRET not in "".join(traceback.format_exception(exc.value))


async def test_noop_form_switch_is_clicked_only_once(page, services):
    await page.goto(URL + "/signin")
    await page.locator("a").evaluate("""e => e.onclick=event => {
        event.preventDefault();window.switches=(window.switches||0)+1;
    }""")
    with pytest.raises(agent.HarnessBlocked):
        await run(page, ScriptedClient([]), account_flow=flow(services))
    assert await page.evaluate("window.switches") == 1


async def test_unchanged_error_words_elsewhere_do_not_fail_signin(page, services):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("body").evaluate("""e => {
        e.insertAdjacentHTML('beforeend','<footer>Incorrect address? Contact support.</footer>');
        document.querySelector('form').onsubmit=event => {event.preventDefault();
            document.querySelector('form').outerHTML='<textarea>Motivation</textarea><button>Postuler</button>'};
    }""")
    result = await run(page, ScriptedClient(signin_steps()), account_flow=flow(services))
    assert result.stop == "submit_point"
    assert services[0].record_account.await_args.kwargs["status"] == "signed_in"
