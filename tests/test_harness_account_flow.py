"""Account preparation uses routed Chromium pages and captures every model request."""

import asyncio
import json
import logging
import traceback
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote, quote_plus

import pytest
import pytest_asyncio
from playwright.async_api import ElementHandle, FileChooser, Frame, Locator, async_playwright
from playwright.async_api import Error as PlaywrightError

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
        "/google": "account_google.html",
        "/google_callback": "account_google_callback.html",
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
        name = (
            {
                "/chooser": "google_chooser.html",
                "/consent": "google_consent.html",
                "/password": "google_password.html",
                "/twofactor": "google_twofactor.html",
                "/captcha": "google_captcha.html",
            }.get(url.path, pages.get(url.path))
            if url.hostname == "accounts.google.com"
            else pages.get(url.path)
        )
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
        ("/review", "Valider", False),
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


@pytest.mark.parametrize(
    "password", [SECRET, "secret / + é?&!", "pass~word1", "pass*word1", "mötdepasse1"]
)
@pytest.mark.parametrize("charset", ["UTF-8", "ISO-8859-1"])
async def test_critic_get_signin_url_is_redacted_in_decision_request(
    page, services, password, charset
):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/get_signin")
    await page.locator("form").evaluate("(f, charset) => f.acceptCharset=charset", charset)
    client = ScriptedClient(signin_steps("Sign in"))
    result = await run(page, client, account_flow=flow(services, password))
    assert "pwd=" in page.url
    request = json.dumps(client.requests, ensure_ascii=False) + repr(result)
    assert "[redacted]" in request
    assert password not in request + repr(result)
    assert quote_plus(password) not in request
    assert page.url.split("pwd=")[1].split("&")[0] not in request


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
@pytest.mark.parametrize("google", [False, True])
async def test_any_non_account_field_disqualifies_everywhere(page, field, container, google):
    if google:
        await google_session(page)
    await page.goto(URL + ("/google" if google else "/signin"))
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
    assert await account_page(page, email=PROFILE[1] if google else "") is None


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
    monkeypatch.setattr(ElementHandle, "check", AsyncMock(side_effect=PlaywrightError(SECRET)))
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


async def google_session(page, *, cookie="SID", url="https://mail.google.com"):
    await page.context.add_cookies([{"name": cookie, "value": "local-session", "url": url}])


@pytest.mark.parametrize(
    "label", ["Sign in with Google", "Continuer avec Google", "Se connecter avec Google"]
)
async def test_google_session_makes_isolated_google_only_page_an_account(
    page, services, label, monkeypatch
):
    monkeypatch.setattr(account_flow, "_GOOGLE_START_TIMEOUT_S", 0.15)
    await google_session(page)
    await page.goto(URL + "/google?noop&label=" + quote(label))
    acct = flow(services)
    # No-op click makes the new account classification observable without authenticating.
    with pytest.raises(account_flow.AccountHandoff):
        await acct.prepare(page)
    assert await page.evaluate("window.googleClicks") == 1


@pytest.mark.parametrize("mode", ["popup", "same"])
async def test_google_chooser_selects_only_exact_profile_and_finishes_site(page, mode):
    await google_session(page)
    await page.goto(URL + "/google?mode=" + mode)
    assert callable(getattr(account_flow, "google_sign_in", None))
    assert await account_flow.google_sign_in(page, PROFILE[1])
    assert page.url == URL + "/application?selected=ADA%40EXAMPLE.COM"
    assert len(page.context.pages) == 1


@pytest.mark.parametrize(
    "profile",
    ["missing@example.com", "xada@example.com", "ada@example.com.evil", "ada+other@example.com"],
)
async def test_google_chooser_never_selects_different_or_substring_account(page, profile):
    await google_session(page)
    await page.goto(URL + "/google?profile=" + quote(profile))
    assert callable(getattr(account_flow, "google_sign_in", None))
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    google = page.context.pages[-1]
    assert google.url.startswith("https://accounts.google.com/chooser")
    assert not await google.evaluate("!!window.broadClicked")
    assert page.url.startswith(URL + "/google")


@pytest.mark.parametrize("screen", ["password", "twofactor", "captcha"])
async def test_google_challenges_return_false_without_typing_or_clicking(page, screen):
    await google_session(page)
    await page.goto(URL + "/google?screen=" + screen)
    assert callable(getattr(account_flow, "google_sign_in", None))
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    google = page.context.pages[-1]
    assert not await google.evaluate("!!window.clicked")
    assert await google.locator("input").evaluate_all("es => es.every(e => !e.value)")


async def test_google_popup_close_without_site_change_is_failure(page, monkeypatch):
    await google_session(page)
    await page.goto(URL + "/google?nochange")
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.8, raising=False)
    assert callable(getattr(account_flow, "google_sign_in", None))
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert len(page.context.pages) == 1
    assert page.url == URL + "/google?nochange"


@pytest.mark.parametrize(
    "session,email",
    [
        ("none", PROFILE[1]),
        ("other_cookie", PROFILE[1]),
        ("other_origin", PROFILE[1]),
        ("SID", ""),
        ("SID", "   "),
    ],
)
async def test_google_requires_profile_email_and_sid_from_its_own_context(
    page, services, session, email
):
    if session != "none":
        await google_session(
            page,
            cookie="HSID" if session == "other_cookie" else "SID",
            url="https://careers.example.com"
            if session == "other_origin"
            else "https://mail.google.com",
        )
    await page.goto(URL + "/google")
    acct = flow(services)
    acct.email = email
    assert await acct.prepare(page) == (None, [], False)
    assert await account_page(page) is None
    assert not await page.evaluate("!!window.googleClicks")
    services[0].find_account.assert_not_awaited()


async def test_google_session_in_another_context_is_not_used(page, browser, services):
    other = await browser.new_context()
    try:
        await other.add_cookies(
            [{"name": "SID", "value": "other-session", "url": "https://mail.google.com"}]
        )
        await page.goto(URL + "/google")
        assert await flow(services).prepare(page) == (None, [], False)
        assert not await page.evaluate("!!window.googleClicks")
    finally:
        await other.close()


@pytest.mark.parametrize("password", [None, SECRET])
@pytest.mark.parametrize("mode", ["popup", "same"])
async def test_google_is_preferred_and_records_only_google_before_application(
    page, services, caplog, mode, password
):
    await google_session(page)
    start = URL + "/google?password&mode=" + mode
    await page.goto(start)
    client = ScriptedClient([step("CLICK", "Postuler")])
    result = await run(page, client, account_flow=flow(services, password))
    assert result.stop == "submit_point" and not result.clicked_submit
    assert len(client.decisions) == 1
    assert "accounts.google.com" not in json.dumps(client.requests)
    services[0].record_account.assert_awaited_once_with(
        HOST, method="google", email=PROFILE[1], status="signed_in", login_url=start
    )
    assert_private(client, result, caplog)


@pytest.mark.parametrize("mode", ["popup", "same"])
async def test_google_failure_falls_back_to_site_password_once(page, services, caplog, mode):
    services[0].find_account.return_value = object()
    await google_session(page)
    start = URL + "/google?password&screen=password&mode=" + mode
    await page.goto(start)
    client = ScriptedClient(signin_steps())
    result = await run(page, client, account_flow=flow(services))
    assert result.stop == "submit_point" and result.clicked_submit
    assert len(client.decisions) == 3
    services[0].record_account.assert_awaited_once_with(
        HOST, method="password", email=PROFILE[1], status="signed_in", login_url=start
    )
    for other in page.context.pages:
        if other.url.startswith("https://accounts.google.com"):
            assert await other.locator("input[type=password]").input_value() == ""
    assert_private(client, result, caplog)


@pytest.mark.parametrize("mode", ["popup", "same"])
async def test_google_failure_hands_off_original_host_without_model_polling_google(
    page, services, monkeypatch, caplog, mode
):
    await google_session(page)
    await page.goto(URL + "/google?screen=twofactor&mode=" + mode)
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.12)
    client, notify = ScriptedClient([]), AsyncMock()
    with pytest.raises(agent.HarnessBlocked) as exc:
        await run(page, client, account_flow=flow(services), notify=notify)
    assert "timed out" in str(exc.value)
    assert exc.value.result.login_host == HOST
    assert notify.await_count == 1
    assert notify.await_args.args[1].startswith("Sign-in needed on " + HOST)
    assert client.requests == []
    services[0].record_account.assert_not_awaited()
    assert_private(client, exc.value.result, caplog, notify.await_args)


@pytest.mark.parametrize("host", ["accounts.google.com", "mail.google.com", "google.fr"])
async def test_google_own_pages_never_reach_decide_or_handoff_classifier(
    page, services, monkeypatch, host
):
    await page.goto("https://" + host + "/consent")
    monkeypatch.setattr(agent, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(agent, "HANDOFF_TIMEOUT_S", 0.08)
    client = ScriptedClient([])
    with pytest.raises(agent.HarnessBlocked):
        await run(page, client, account_flow=flow(services))
    assert client.requests == []


@pytest.mark.parametrize(
    "label", ["Continue", "Continuer", "Autoriser", "Allow", "Weiter", "Zulassen"]
)
async def test_google_consent_clicks_only_approved_whole_labels(page, label):
    await google_session(page)
    await page.goto(URL + "/google?screen=consent&consent=" + label)
    assert await account_flow.google_sign_in(page, PROFILE[1])
    assert page.url.startswith(URL + "/application")


async def test_google_consent_with_profile_menu_does_not_click_the_account_menu(page, monkeypatch):
    await google_session(page)
    html = (
        FIXTURES / "google_consent.html"
    ).read_text() + '<button onclick="window.accountMenuClicked=true">ada@example.com</button>'
    await page.context.route(
        "https://accounts.google.com/consent**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    await page.goto(URL + "/google?screen=consent")
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.8)
    assert await account_flow.google_sign_in(page, PROFILE[1])


@pytest.mark.parametrize("label", ["Continue with another account", "Allow and apply"])
async def test_google_rejects_consent_label_suffixes(page, monkeypatch, label):
    await google_session(page)
    await page.goto(URL + "/google?screen=consent&consent=" + quote(label))
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.3)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert not await page.context.pages[-1].evaluate("!!window.wrongConsent")


async def test_google_gsi_iframe_button_can_authenticate(page, services):
    await google_session(page)
    await page.context.route(
        "https://accounts.google.com/gsi/**",
        lambda route: route.fulfill(
            body="<button onclick=\"window.open('https://accounts.google.com/chooser')\">"
            "Sign in with Google</button>",
            content_type="text/html",
        ),
    )
    await page.goto(URL + "/google")
    await page.locator("#google").evaluate(
        "e => e.outerHTML='<iframe src=\"https://accounts.google.com/gsi/button\"></iframe>'"
    )
    await page.frames[1].wait_for_load_state()
    detected = await account_page(page, email=PROFILE[1])
    assert detected is not None and detected.google_button
    assert (await flow(services).prepare(page))[2]
    assert page.url.startswith(URL + "/application")


@pytest.mark.parametrize("existing", [True, False])
async def test_google_respects_new_account_cap(page, services, existing):
    await google_session(page)
    services[0].find_account.return_value = object() if existing else None
    services[0].new_accounts_today.return_value = 10
    await page.goto(URL + "/google")
    if existing:
        assert (await flow(services).prepare(page))[2]
    else:
        with pytest.raises(account_flow.AccountHandoff, match="10"):
            await flow(services).prepare(page)
        assert not await page.evaluate("!!window.googleClicks")
        services[0].record_account.assert_not_awaited()


async def test_google_timeouts_have_specified_bounds_and_remove_popup_listener(page, monkeypatch):
    assert account_flow._GOOGLE_START_TIMEOUT_S == 15
    assert account_flow._GOOGLE_FINISH_TIMEOUT_S == 30
    await google_session(page)
    await page.goto(URL + "/google?noop")
    before = list(page.context._impl_obj.listeners("page"))
    monkeypatch.setattr(account_flow, "_GOOGLE_START_TIMEOUT_S", 0.1)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert list(page.context._impl_obj.listeners("page")) == before


async def test_google_cancellation_removes_popup_listener(page):
    await google_session(page)
    await page.goto(URL + "/google?noop")
    before = list(page.context._impl_obj.listeners("page"))
    task = asyncio.create_task(account_flow.google_sign_in(page, PROFILE[1]))
    await page.wait_for_function("window.googleClicks === 1")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(page.context._impl_obj.listeners("page")) == before


@pytest.mark.parametrize(
    "markup",
    [
        '<button title="Sign in with Google">Google</button>',
        '<button aria-label="Sign in with Google">Google</button>',
        "<button>Sign in with Google</button>",
        '<input type=button value="Sign in with Google">',
        '<a role=link title="Sign in with Google">Google</a>',
    ],
)
async def test_google_control_text_aria_and_title_share_detection_and_clicking(
    page, services, markup
):
    await google_session(page)
    await page.goto(URL + "/google")
    await page.locator("#google").evaluate(
        "(e, html) => {const click=e.onclick; e.outerHTML=html; "
        "document.body.firstElementChild.onclick=click}",
        markup,
    )
    assert await account_page(page, email=PROFILE[1]) is not None
    assert (await flow(services).prepare(page))[2]
    assert page.url.startswith(URL + "/application")


async def test_google_control_cannot_hide_an_application_action_in_aria_label(page, services):
    await google_session(page)
    await page.goto(URL + "/google")
    await page.locator("#google").evaluate(
        "e => e.setAttribute('aria-label', 'Sign in with Google and apply')"
    )
    assert await flow(services).prepare(page) == (None, [], False)
    assert not await page.evaluate("!!window.googleClicks")


@pytest.mark.parametrize(
    "challenge",
    [
        "<input type=password hidden>",
        "<h1>2-step</h1>",
        "<h1>Verify it's you</h1>",
        "<h1>Validez votre identité</h1>",
        '<iframe src="https://accounts.google.com/recaptcha"></iframe>',
    ],
)
async def test_google_each_challenge_independently_blocks_all_input(page, challenge):
    await google_session(page)
    await page.context.route(
        "https://accounts.google.com/challenge**",
        lambda route: route.fulfill(
            body='<meta charset="utf-8">'
            + challenge
            + '<button onclick="window.clicked=true">Continue</button>',
            content_type="text/html",
        ),
    )
    await page.goto(URL + "/google?screen=challenge")
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    google = page.context.pages[-1]
    assert not await google.evaluate("!!window.clicked")
    assert await google.locator("input").evaluate_all("es => es.every(e => !e.value)")


@pytest.mark.parametrize("email", [PROFILE[1], ""])
async def test_google_without_session_keeps_existing_password_behaviour(page, services, email):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/google?password")
    acct = flow(services)
    acct.email = email
    if email:
        detected, _, _ = await acct.prepare(page)
        assert detected is not None and detected.has_password
        assert await page.locator("input[type=password]").input_value() == SECRET
    else:
        with pytest.raises(account_flow.AccountHandoff):
            await acct.prepare(page)
        assert await page.locator("input[type=password]").input_value() == ""
    assert not await page.evaluate("!!window.googleClicks")


async def test_google_success_after_password_attempt_keeps_google_record(page, services):
    await google_session(page)
    await page.goto(URL + "/google?password")
    acct = flow(services)
    acct.attempted = acct.pending = True
    acct.signup_since = datetime.now(UTC)
    assert (await acct.prepare(page))[2]
    assert await acct.prepare(page) == (None, [], False)
    services[0].record_account.assert_awaited_once()
    assert services[0].record_account.await_args.kwargs["method"] == "google"


@pytest.mark.parametrize("password_before,application_after", [(False, False), (True, True)])
async def test_google_failure_uses_current_isolation_for_password_fallback(
    page, services, monkeypatch, password_before, application_after
):
    services[0].find_account.return_value = object()
    await google_session(page)
    await page.goto(URL + "/google" + ("?password" if password_before else ""))

    async def google_failure(_page, _email):
        await page.set_content(
            (FIXTURES / "account_signin.html").read_text()
            + (APPLICATION if application_after else "")
        )
        return False

    monkeypatch.setattr(account_flow, "google_sign_in", google_failure)
    if application_after:
        with pytest.raises(account_flow.AccountHandoff):
            await flow(services).prepare(page)
        assert await page.locator("input[type=password]").input_value() == ""
    else:
        assert (await flow(services).prepare(page))[0] is not None
        assert await page.locator("input[type=password]").input_value() == SECRET


async def test_google_popup_waits_through_blank_navigation_and_ignores_unrelated_pages(
    page, monkeypatch
):
    await google_session(page)
    await page.goto(URL + "/google")
    await page.locator("#google").evaluate("""e => e.onclick=() => {
        const popup=window.open('about:blank');
        setTimeout(()=>popup.location.href='https://accounts.google.com/chooser',120);
    }""")
    task = asyncio.create_task(account_flow.google_sign_in(page, PROFILE[1]))
    other = await page.context.new_page()
    try:
        await other.goto("https://accounts.google.com/password")
        assert await task
        assert await other.locator("input[type=password]").input_value() == ""
    finally:
        await other.close()


async def test_google_does_not_infer_success_from_sid_disappearing(page, monkeypatch):
    await google_session(page)
    await page.goto(URL + "/google?nochange")

    async def clear_session(route):
        await page.context.clear_cookies()
        await route.fulfill(
            body=(FIXTURES / "account_google_callback.html").read_text(), content_type="text/html"
        )

    await page.context.route("**/google_callback**", clear_session)
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.8)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert page.url == URL + "/google?nochange"


async def test_google_unapproved_consent_does_not_click_a_profile_menu(page, monkeypatch):
    await google_session(page)
    html = (
        FIXTURES / "google_consent.html"
    ).read_text() + '<button onclick="window.accountMenuClicked=true">ada@example.com</button>'
    await page.context.route(
        "https://accounts.google.com/consent**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    await page.goto(URL + "/google?screen=consent&consent=Proceed")
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.4)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert not await page.context.pages[-1].evaluate("!!window.accountMenuClicked")


async def test_google_unreadable_site_frame_cannot_imply_success(page, monkeypatch):
    await google_session(page)
    await page.goto(URL + "/google?nochange")
    await page.context.route(
        "**/google_callback**",
        lambda route: route.fulfill(
            body="<p>Google returned to the site</p>", content_type="text/html"
        ),
    )
    original = Frame.evaluate

    async def unreadable(frame, expression, arg=None):
        if (
            frame.page == page
            and expression == account_flow.ACCOUNT_PAGE_JS
            and len(page.context.pages) > 1
        ):
            raise PlaywrightError("frame inspection failed")
        return await original(frame, expression, arg)

    monkeypatch.setattr(Frame, "evaluate", unreadable)
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.5)
    assert not await account_flow.google_sign_in(page, PROFILE[1])


@pytest.mark.parametrize("history", ["upload", "typing"])
async def test_google_does_not_reclassify_an_application_after_prior_work(
    page, services, tmp_path, history
):
    await google_session(page)
    await page.goto(URL + "/application")
    documents = {}
    if history == "upload":
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF-1.4")
        documents = {"cv": cv}
        markup = "<input type=file name=resume onchange=\"location.href='/google'\">"
        steps = [step("WAIT")]
    else:
        markup = "<label>Motivation<input oninput=\"location.href='/google'\"></label>"
        steps = [step("TYPE_TEXT", "Motivation")]
    await page.set_content(markup)
    client = ScriptedClient(steps + [step("DONE")], slots={"Motivation": ("full_name", 0.9)})
    notify = AsyncMock()
    result = await run(
        page, client, account_flow=flow(services), documents=documents, notify=notify
    )
    assert result.stop == "ready"
    assert not await page.evaluate("!!window.googleClicks")
    services[0].find_account.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.parametrize("control", ["button", "title", "gsi"])
async def test_google_failed_control_is_not_offered_to_model_again(page, services, control):
    services[0].find_account.return_value = object()
    await google_session(page)
    await page.goto(URL + "/google?password&screen=password")
    if control == "title":
        await page.locator("#google").evaluate(
            "e => {e.title=e.textContent; e.textContent='Google'}"
        )
    elif control == "gsi":
        await page.context.route(
            "https://accounts.google.com/gsi/**",
            lambda route: route.fulfill(
                body="<button onclick=\"window.open('https://accounts.google.com/password')\">"
                "Sign in with Google</button>",
                content_type="text/html",
            ),
        )
        await page.locator("#google").evaluate(
            "e => e.outerHTML='<iframe src=\"https://accounts.google.com/gsi/button\"></iframe>'"
        )
        await page.frames[1].wait_for_load_state()

    async def before(body):
        labels = [
            value["element"]
            for value in body["questions"]["click_target"]["criteria"].values()
            if isinstance(value, dict)
        ]
        assert all("google" not in label.lower() for label in labels)

    result = await run(
        page, ScriptedClient(signin_steps(), before=before), account_flow=flow(services)
    )
    assert result.stop == "submit_point" and result.clicked_submit


@pytest.mark.parametrize("screen", ["password", "twofactor", "captcha", "consent"])
async def test_google_loading_site_does_not_hide_an_unfinished_popup(page, monkeypatch, screen):
    await google_session(page)
    if screen == "consent":
        await page.context.route(
            "https://accounts.google.com/consent**",
            lambda route: route.fulfill(
                body='<h1>Sign in to Example</h1><button onclick="window.clicked=true">'
                "Continue</button>",
                content_type="text/html",
            ),
        )
    await page.goto(URL + "/google?screen=" + screen)
    await page.locator("#google").evaluate("""e => {
        const click=e.onclick;
        e.onclick=() => {click(); document.body.innerHTML='<p>Signing in...</p>'};
    }""")
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.8)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    google = page.context.pages[-1]
    assert google.url.startswith("https://accounts.google.com/")
    assert await google.locator("input").evaluate_all("es => es.every(e => !e.value)")
    assert await google.evaluate("!!window.clicked") is (screen == "consent")


@pytest.mark.parametrize(
    "challenge,button",
    [
        ("<h1>Validation en deux étapes</h1>", "Continuer"),
        ("<h1>Bestätigung in zwei Schritten</h1>", "Weiter"),
        ('<input autocomplete="one-time-code">', "Continue"),
    ],
)
async def test_google_translated_twofactor_and_otp_fields_stop_before_any_action(
    page, monkeypatch, challenge, button
):
    await google_session(page)
    await page.context.route(
        "https://accounts.google.com/verification**",
        lambda route: route.fulfill(
            body='<meta charset="utf-8">'
            + challenge
            + '<button onclick="window.clicked=true">'
            + button
            + "</button>",
            content_type="text/html",
        ),
    )
    await page.goto(URL + "/google?screen=verification")
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.5)
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    google = page.context.pages[-1]
    assert not await google.evaluate("!!window.clicked")
    assert await google.locator("input").evaluate_all("es => es.every(e => !e.value)")


async def test_critic4b_hidden_csrf_keeps_real_signin_working(page, services):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.locator("form").evaluate(
        "f => f.insertAdjacentHTML('afterbegin', '<input type=hidden name=_csrf value=x>')"
    )
    assert await account_page(page) is not None
    result = await run(page, ScriptedClient(signin_steps()), account_flow=flow(services))
    assert result.clicked == 1 and result.clicked_submit
    assert page.url.endswith("/application")


async def test_critic4b_contact_step_then_spa_account_never_submits(page, services):
    await page.goto(URL + "/contact_spa")
    acct = flow(services)
    client = ScriptedClient([
        step("TYPE_TEXT", "First Name"), step("TYPE_TEXT", "Email"),
        step("TYPE_TEXT", "Phone"), step("CLICK", "Yes"), step("CLICK", "Next"),
        step("CLICK", "Create account"), step("DONE"), step("DONE"),
    ])
    result = await run(page, client, account_flow=acct)
    assert acct.application_started and await acct.detect(page) is None
    assert result.clicked == 2 and not result.clicked_submit
    assert not await page.evaluate("!!window.submitted")
    assert await page.locator("input[type=password]").input_value() == ""
    services[0].find_account.assert_not_awaited()


@pytest.mark.parametrize("label", [
    "Se connecter et postuler", "Register &amp; Apply", "Sign in to submit",
    "Créer mon compte et envoyer ma candidature",
])
async def test_critic4b_mixed_account_application_labels_never_submit(page, services, label):
    await page.goto(URL + "/signin")
    await page.set_content(
        "<form onsubmit='event.preventDefault(); window.submitted=true'>"
        "<label>Email<input type=email></label><label>Password<input type=password></label>"
        f"<button>{label}</button></form>"
    )
    try:
        result = await run(
            page, ScriptedClient([step("CLICK", label.replace("&amp;", "&")), step("DONE")]),
            account_flow=flow(services),
        )
    except agent.HarnessBlocked as exc:
        result = exc.result
    assert not result.clicked_submit
    assert not await page.evaluate("!!window.submitted")


async def test_critic4b_account_aria_cannot_hide_submit_text(page, services):
    await page.goto(URL + "/signin")
    await page.set_content(
        "<form onsubmit='event.preventDefault(); window.submitted=true'>"
        "<label>Email<input type=email></label><label>Password<input type=password></label>"
        "<button aria-label='Sign in'>Submit application</button></form>"
    )
    assert await account_page(page) is None
    result = await run(
        page, ScriptedClient([step("CLICK", "Sign in"), step("DONE")]),
        account_flow=flow(services),
    )
    assert not result.clicked_submit and not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("field", [
    '<x-letter name=letter></x-letter>', '<output name=letter>Cover letter</output>',
    '<object name=letter></object>', '<fieldset><legend>Application</legend></fieldset>',
    '<input type=radio role=checkbox name=answer>',
    '<input type=radio role=combobox name=country>',
])
@pytest.mark.parametrize("external", [False, True])
async def test_critic4b_every_form_element_must_be_an_account_field(
    page, services, field, external
):
    await page.goto(URL + "/signin")
    await page.set_content(
        "<script>customElements.define('x-letter', class extends HTMLElement {"
        "static formAssociated = true; constructor(){super(); this.i=this.attachInternals();"
        "this.i.setFormValue('my cover letter');}});</script>"
        "<form id=account onsubmit='event.preventDefault(); window.submitted=true'>"
        "<label>Email<input type=email></label><label>Password<input type=password></label>"
        + ("" if external else field) + "<button>Sign in</button></form>"
        + (field.replace(" name=", " form=account name=")
           .replace("<fieldset>", "<fieldset form=account>") if external else "")
    )
    assert await page.evaluate("document.forms[0].elements.length") == 4
    assert await account_page(page) is None
    result = await run(
        page, ScriptedClient([step("CLICK", "Sign in"), step("DONE")]),
        account_flow=flow(services),
    )
    assert result.clicked == 0 and not await page.evaluate("!!window.submitted")


async def test_critic4b_review_step_with_create_account_label_never_submits(page, services):
    await page.goto(URL + "/review")
    await page.locator("button").evaluate("b => b.textContent='Créer mon compte'")
    assert await account_page(page) is None
    result = await run(
        page, ScriptedClient([
            step("TYPE_TEXT", "Email"), step("CLICK", "Créer mon compte"),
            step("DONE"), step("DONE"),
        ]), account_flow=flow(services),
    )
    assert result.clicked == 0 and not result.clicked_submit
    assert not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("text", [
    "Create a password to save your application", "Submit your application",
    "Your application will be saved", "Send your application", "Finalize application",
    "Créer un compte pour soumettre votre candidature", "Enregistrer ma candidature",
    "Envoyer votre candidature", "Compléter votre candidature",
    "Votre candidature sera enregistrée", "Bewerbung abschließen", "Bewerbung absenden",
    "Review your application", "Récapitulatif de votre candidature",
    "Récapitulatif de ma candidature", "Zusammenfassung Ihrer Bewerbung",
])
@pytest.mark.parametrize("container", ["form", "fieldset", "dialog open", "section"])
async def test_application_text_in_account_control_container_disqualifies(page, text, container):
    await page.goto(URL + "/signin")
    await page.set_content(
        f"<{container}><p>{text}</p><label>Password<input type=password></label>"
        f"<button>Sign in</button></{container.split()[0]}>"
    )
    assert await account_page(page) is None


@pytest.mark.parametrize("inside", [False, True])
async def test_application_text_only_counts_inside_the_signin_form(page, services, inside):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    # Even a surrounding section (including the sign-up link) is outside the form.
    await page.locator("body").evaluate("e => e.innerHTML='<section>'+e.innerHTML+'</section>'")
    await page.locator("form" if inside else "section").evaluate(
        "e => e.insertAdjacentHTML('afterbegin',"
        "'<p>Créer un compte pour soumettre votre candidature</p>')"
    )
    assert (await account_page(page) is not None) is (not inside)
    result = await run(
        page, ScriptedClient([
            step("TYPE_TEXT", "Email"), step("CLICK", "Connexion"), step("DONE"),
        ]), account_flow=flow(services),
    )
    assert result.clicked_submit is (not inside)
    assert page.url.endswith("/signin" if inside else "/application")


@pytest.mark.parametrize(
    "kind", ["email", "phone", "select", "radio", "checkbox", "switch", "option"]
)
@pytest.mark.parametrize("destination", ["signin", "google"])
async def test_all_application_inputs_latch_before_account_spa_swap(
    page, services, kind, destination
):
    await google_session(page)
    await page.goto(URL + "/application")
    markup, action = {
        "email": ('<label>Email<input type=email></label>', step("TYPE_TEXT", "Email")),
        "phone": ('<label>Phone<input type=tel></label>', step("TYPE_TEXT", "Phone")),
        "select": ('<label>Country<select><option>France</option><option>Germany</option>'
                   '</select></label>', step("SELECT", "Country → Germany")),
        "radio": ('<label><input type=radio>Yes</label>', step("CLICK", "Yes")),
        "checkbox": ('<label><input type=checkbox>Yes</label>', step("CLICK", "Yes")),
        "switch": ('<button role=switch aria-checked=false>Yes</button>', step("CLICK", "Yes")),
        "option": ('<div role=option tabindex=0>Yes</div>', step("CLICK", "Yes")),
    }[kind]
    await page.set_content(markup + '<button type=button id=next>Next</button>')
    target = (FIXTURES / f"account_{destination}.html").read_text()
    await page.locator("#next").evaluate(
        "(e, html) => e.onclick=() => {document.body.innerHTML=html}", target
    )
    acct = flow(services)
    checks = []

    async def before(_body):
        checks.append(acct.application_started)

    result = await run(
        page, ScriptedClient([
            action, step("CLICK", "Next"),
            step("CLICK", "Connexion" if destination == "signin" else "Sign in with Google"),
            step("DONE"),
        ], before=before), account_flow=acct,
    )
    assert checks[:3] == [False, True, True]
    assert acct.application_started and await acct.detect(page) is None
    assert not acct.google_attempted and not result.clicked_submit
    assert result.clicked == (1 if kind in {"email", "phone", "select"} else 2)
    services[0].find_account.assert_not_awaited()


@pytest.mark.parametrize("encoded", ["pass%7Eword1", "%70ass~word1", "pass%7eword1"])
async def test_clean_decodes_form_urlencoded_text_before_matching(services, encoded):
    assert flow(services, "pass~word1").clean("Echo: " + encoded) == "Echo: [redacted]"


@pytest.mark.parametrize("encoded", ["m%C3%B6tdepasse1", "m%F6tdepasse1", "%6d%f6tdepasse1"])
async def test_clean_decodes_utf8_and_latin1_text(services, encoded):
    assert flow(services, "mötdepasse1").clean("Echo: " + encoded) == "Echo: [redacted]"


async def test_runner_post_submit_classifier_receives_redacted_text(page, services, monkeypatch):
    from backend.applier.harness import runner

    password = "pass~word1"
    await page.goto(URL + "/application")
    await page.set_content(
        '<button onclick="document.body.textContent=\'Echo: pass%7Eword1\'">Submit</button>'
    )
    snap = await agent.observe(page)
    action = next(a for a in snap["actions"] if a["kind"] == "click")
    result = agent.HarnessResult("ready", action, {}, [], [], [], 0)
    confirm = asyncio.Event()

    async def publish(*args, **kwargs):
        confirm.set()

    monkeypatch.setattr(runner, "_publish_review", publish)
    monkeypatch.setattr(runner, "verify_page", AsyncMock(return_value=SimpleNamespace(
        outcome="unknown", evidence="No receipt",
    )))
    monkeypatch.setattr(runner, "_DecisionClient", lambda: SimpleNamespace(aclose=AsyncMock()))
    classify = AsyncMock(return_value=("application_received", 0.99))
    monkeypatch.setattr(runner, "classify_page", classify)
    status, _ = await runner._review(
        page, result, job_id=42, documents=[], confirm_event=confirm, cancel_event=None,
        on_review=None, on_get_patches=None, clean=flow(services, password).clean,
    )
    assert status == "confirmed"
    assert classify.await_args.args[1] == "Echo: [redacted]"


async def test_verify_code_form_allows_hidden_csrf(page, services):
    await page.goto(URL + "/verify")
    await page.locator("form").evaluate(
        "f => f.insertAdjacentHTML('afterbegin', '<input type=hidden name=_csrf value=x>')"
    )
    acct = flow(services)
    acct.signup_since = datetime.now(UTC)
    services[1].wait_for_verification.return_value = mailbox.Verification(code="482913")
    result = await run(
        page, ScriptedClient([step("CLICK", "Vérifier"), step("CLICK", "Postuler")]),
        account_flow=acct,
    )
    assert result.clicked == 1 and result.clicked_submit
    assert not acct.application_started
    assert page.url.endswith("/application")


async def test_clicked_control_uses_its_form_owner_instead_of_ancestor(page, services):
    services[0].find_account.return_value = object()
    await page.goto(URL + "/signin")
    await page.set_content(
        '<form><label>Password<input type=password></label>'
        '<button form=review>Créer mon compte</button></form>'
        '<form id=review onsubmit="event.preventDefault(); window.submitted=true">'
        '<p>Save your application</p></form>'
    )
    assert await account_page(page) is not None
    result = await run(
        page, ScriptedClient([step("CLICK", "Créer mon compte"), step("DONE")]),
        account_flow=flow(services),
    )
    assert result.clicked == 0 and not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("container", ["fieldset", "dialog open"])
async def test_formless_account_container_rejects_other_form_elements(page, container):
    await page.goto(URL + "/signin")
    await page.set_content(
        f'<{container}><label>Password<input type=password></label>'
        '<output>Cover letter</output><button>Sign in</button>'
        f'</{container.split()[0]}>'
    )
    assert await account_page(page) is None


@pytest.mark.parametrize("chooser", [False, True])
async def test_upload_latches_before_dispatch_and_spa_swap(
    page, services, tmp_path, monkeypatch, chooser
):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    documents = {"cv": cv}
    await page.goto(URL + "/application")
    markup = '<input type=file name=resume onchange="location.href=\'/signin\'">'
    if chooser:
        await page.set_content('<button type=button>Upload CV</button>')
        await page.locator("button").evaluate(
            "(b, html) => b.onclick=() => {b.insertAdjacentHTML('afterend',html);"
            "document.querySelector('input').click()}", markup,
        )
    else:
        await page.set_content(markup)
    acct = flow(services)
    latched = []
    original = FileChooser.set_files if chooser else agent.attach

    async def upload(*args, **kwargs):
        latched.append(acct.application_started)
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        FileChooser if chooser else agent, "set_files" if chooser else "attach", upload
    )
    result = await run(
        page, ScriptedClient([
            step("CLICK", "Upload CV") if chooser else step("WAIT"),
            step("CLICK", "Connexion"), step("DONE"),
        ]), account_flow=acct, documents=documents,
    )
    assert latched == [True]
    assert acct.application_started and await acct.detect(page) is None
    assert result.clicked == int(chooser) and not result.clicked_submit
    assert await page.locator("input[type=password]").input_value() == ""


# critic-acc5: restored drafts must close the account exception before any write.
@pytest.mark.parametrize("destination", ["password", "google"])
async def test_critic5_prefilled_draft_then_account_shape_never_submits(
    page, services, destination
):
    await google_session(page)
    await page.goto(URL + "/signin")
    await page.set_content("""<div id=app><h1>Apply: contact details</h1>
        <label>First Name<input name=first value=Ada></label>
        <label>Email<input type=email value=ada@example.com></label>
        <label>Phone<input type=tel value=0123456789></label>
        <fieldset><legend>Authorized to work?</legend>
        <label><input type=radio name=auth value=y checked>Yes</label>
        <label><input type=radio name=auth value=n>No</label></fieldset>
        <button type=button id=next>Next</button></div>""")
    button = "Create account" if destination == "password" else "Continue with Google"
    target = (
        '<h1>Review</h1><p>Create a password to submit your application</p>'
        '<form onsubmit="event.preventDefault(); window.submitted=true">'
        '<label>Password<input type=password></label>'
        f'<button onclick="window.submitted=true">{button}</button></form>'
    )
    await page.locator("#next").evaluate(
        "(e, html) => e.onclick=() => {document.querySelector('#app').innerHTML=html}", target
    )
    acct = flow(services)
    result = await run(page, ScriptedClient([
        step("TYPE_TEXT", "First Name"), step("CLICK", "Next"),
        step("CLICK", button), step("DONE"), step("DONE"),
    ]), account_flow=acct)
    assert acct.application_seen and not acct.application_started
    assert await acct.detect(page) is None
    assert not acct.google_attempted and not result.clicked_submit
    assert result.clicked == 1 and not await page.evaluate("!!window.submitted")
    services[0].find_account.assert_not_awaited()


@pytest.mark.parametrize("field", [
    '<label>Email<input type=email value=ada@example.com></label>',
    '<label>Country<select><option selected>France</option>'
    '<option>Germany</option></select></label>',
    '<form><input type=radio checked></form>',
    '<form><input type=checkbox checked></form>',
    '<form><button role=switch aria-checked=true>Yes</button></form>',
    '<input type=file hidden>', '<textarea hidden>Draft</textarea>',
    '<div contenteditable hidden>Draft</div>',
])
async def test_application_observation_latches_without_writes(page, services, field):
    await page.goto(URL + "/application")
    await page.set_content(field)
    acct = flow(services)
    await run(page, ScriptedClient([step("DONE"), step("DONE")]), account_flow=acct)
    assert acct.application_seen and not acct.application_started
    await page.set_content('<input type=password><button>Create account</button>')
    assert await acct.detect(page) is None


@pytest.mark.parametrize("field", [
    '<label>Search jobs<input type=search></label>',
    '<form role=search><label>City<input></label></form>',
    '<form action="/search"><select><option>A</option><option>B</option></select></form>',
    '<button role=switch aria-checked=true>Dark mode</button>',
])
async def test_site_search_and_nonform_toggles_do_not_close_account_path(page, services, field):
    await page.goto(URL + "/application")
    await page.set_content(field)
    acct = flow(services)
    await run(page, ScriptedClient([step("DONE"), step("DONE")]), account_flow=acct)
    assert not acct.application_seen and not acct.application_started
    await page.set_content('<input type=password><button>Create account</button>')
    assert await acct.detect(page) is not None


@pytest.mark.parametrize("phrase", [
    "Créez votre compte pour valider votre candidature",
    "Pour finaliser votre dossier, créez votre compte",
    "Create an account to complete your profile and apply",
    "Create your account to complete your application",
    "Konto erstellen und fortfahren, um Ihre Bewerbung abzuschicken",
    "Confirm your application by creating an account",
    "Transmettre ma candidature", "Submit your application",
    "Completer votre candidature", "Compléter votre candidature",
    "Bewerbung einreichen", "Bewerbung absenden", "Postuler à cette candidature",
    "Apply with your profil", "Apply with your profile",
])
async def test_critic5_application_text_gaps(page, phrase):
    await page.goto(URL + "/signin")
    await page.set_content(
        f'<form><p>{phrase}</p><label>Email<input type=email></label>'
        '<label>Password<input type=password></label><button>Create account</button></form>'
    )
    assert await account_page(page) is None


@pytest.mark.parametrize("phrase", [
    "Créer votre compte", "Create your account to get started", "Konto erstellen",
    "Complete your profile",  # Profile alone is not an application noun.
])
async def test_plain_signup_text_still_allows_account_page(page, phrase):
    await page.goto(URL + "/signin")
    await page.set_content(
        f'<form><p>{phrase}</p><input type=password><button>Create account</button></form>'
    )
    assert await account_page(page) is not None


@pytest.mark.parametrize("container", ["div", "main", "body"])
async def test_critic5_formless_application_text_never_submits(page, services, container):
    await page.goto(URL + "/signin")
    await page.set_content(
        f'<{container}><h1>Last step</h1><p>Create a password to submit your application</p>'
        '<label>Password<input type=password></label>'
        f'<button onclick="window.submitted=true">Create account</button></{container}>'
    )
    # At body scope unrelated paragraphs are intentionally ignored; headings count.
    if container == "body":
        await page.locator("h1").evaluate("e => e.textContent='Submit your application'")
    assert await account_page(page) is None
    result = await run(page, ScriptedClient([
        step("CLICK", "Create account"), step("DONE"), step("DONE"),
    ]), account_flow=flow(services))
    assert result.clicked == 0 and not await page.evaluate("!!window.submitted")


async def test_formless_body_text_uses_field_children_and_all_headings(page):
    await page.goto(URL + "/signin")
    await page.set_content(
        '<aside><p>Submit your application</p></aside>'
        '<div><label>Password<input type=password></label></div>'
        '<div><button>Create account</button></div>'
    )
    assert await account_page(page) is not None
    await page.locator("aside").evaluate("e => e.innerHTML='<h3>Submit your application</h3>'")
    assert await account_page(page) is None


@pytest.mark.parametrize("password,encoded", [
    ("pass€word1", "pass%80word1"), ("passπword1", "pass%26%23960%3Bword1"),
    ("pass–word1", "pass%96word1"), ("passπword1", "pass&#960;word1"),
    ("pass€πword1", "pass%80%26%23960%3bword1"),
])
async def test_critic5_clean_legacy_charsets(password, encoded):
    acct = AccountFlow(password=password, email=PROFILE[1], company="Example")
    assert acct.clean(f"https://x.example/signin?pwd={encoded}") == (
        "https://x.example/signin?pwd=[redacted]"
    )


@pytest.mark.parametrize("password,encoded", [
    ("pass€word1", "pass%80word1"), ("passπword1", "pass%26%23960%3Bword1"),
    ("pass–word1", "pass%96word1"),
])
async def test_critic5_chromium_legacy_get_form_redacts_password(page, services, password, encoded):
    async def legacy(route):
        await route.fulfill(
            body=(
                '<meta charset="iso-8859-1"><form action="/legacy"><input name=email>'
                '<input type=password name=pwd><button>Sign in</button></form>'
            ).encode("latin-1"),
            content_type="text/html; charset=iso-8859-1",
        )

    await page.context.route("**/legacy**", legacy)
    await page.goto(URL + "/legacy")
    await page.locator("input[type=password]").fill(password)
    await page.locator("button").click()
    await page.wait_for_load_state()
    assert encoded in page.url
    assert flow(services, password).clean(page.url) == URL + "/legacy?email=&pwd=[redacted]"


@pytest.mark.parametrize("delay", [0, 7, 14])
@pytest.mark.parametrize("popup", [False, True])
async def test_critic5_google_redirect_race_never_clicks_site_continue(
    page, monkeypatch, delay, popup
):
    await google_session(page)
    monkeypatch.setattr(account_flow, "_GOOGLE_START_TIMEOUT_S", 0.3)
    monkeypatch.setattr(account_flow, "_GOOGLE_FINISH_TIMEOUT_S", 0.8)
    opener = "window.open" if popup else "location.assign"
    await page.context.route("**/*", lambda route: route.fulfill(
        body=(
            '<p>One moment</p><iframe src="https://accounts.google.com/f1"></iframe>'
            '<iframe src="https://accounts.google.com/f2"></iframe>'
            f'<script>setTimeout(() => location.href="{URL}/app", {delay})</script>'
            if route.request.url.startswith("https://accounts.google.com/o") else
            '<p>frame</p>' if route.request.url.startswith("https://accounts.google.com/") else
            '<h1>Your application</h1><p>Everything is ready.</p>'
            '<button onclick="window.submitted=true">Continue</button>'
            if route.request.url.endswith("/app") else
            f'<button onclick="{opener}(\'https://accounts.google.com/o\')">'
            'Continue with Google</button>'
        ), content_type="text/html",
    ))
    await page.goto(URL + "/signin")
    await account_flow.google_sign_in(page, PROFILE[1])
    for current in page.context.pages:
        assert not await current.evaluate("!!window.submitted")


@pytest.mark.parametrize("chooser", [False, True])
async def test_google_step_rejects_handles_from_site_origin(page, chooser):
    await page.goto(URL + "/application")
    await page.set_content(
        ('<h1>Choose an account</h1>' if chooser else '')
        + '<button onclick="window.submitted=true">'
        + (PROFILE[1] if chooser else "Continue") + '</button>'
    )
    assert not await account_flow._google_step(page, PROFILE[1], set())
    assert not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("scope", [
    "Drive", "Contacts", "Calendar", "Gmail", "mail", "Google Drive", "contacts",
    "agenda", "calendrier", "fichiers", "files", "Kalender", "Kontakte",
    '<label><input type=checkbox checked>Name</label>',
    '<div role=checkbox aria-checked=true>Email address</div>',
])
@pytest.mark.parametrize("popup", [False, True])
async def test_google_consent_extra_scopes_hand_off(page, scope, popup):
    await google_session(page)
    await page.context.route("https://accounts.google.com/consent**", lambda route: route.fulfill(
        body='<h1>Sign in to Example</h1><p>' + scope + '</p>'
        + '<button onclick="window.approved=true">Allow</button>', content_type="text/html",
    ))
    await page.goto(URL + "/google?screen=consent&mode=" + ("popup" if popup else "same"))
    assert not await account_flow.google_sign_in(page, PROFILE[1])
    assert not await page.context.pages[-1].evaluate("!!window.approved")


async def test_google_basic_identity_consent_can_be_approved(page):
    await google_session(page)
    await page.context.route("https://accounts.google.com/consent**", lambda route: route.fulfill(
        body='<p>Name, email address, language preference, profile picture</p>'
        + (FIXTURES / "google_consent.html").read_text(), content_type="text/html",
    ))
    await page.goto(URL + "/google?screen=consent")
    assert await account_flow.google_sign_in(page, PROFILE[1])


@pytest.mark.parametrize("text", [
    "Use another account ada@example.com", "Utiliser un autre compte ada@example.com",
    "Anderes Konto ada@example.com", "Not ada@example.com", "Pas ada@example.com",
])
async def test_google_chooser_skips_other_account_rows(page, text):
    await page.goto("https://accounts.google.com/chooser")
    await page.set_content(
        f'<h1>Choose an account</h1><button onclick="window.wrong=true">{text}</button>'
    )
    assert not await account_flow._google_step(page, PROFILE[1], set())
    assert not await page.evaluate("!!window.wrong")


@pytest.mark.parametrize("chooser", [False, True])
async def test_google_navigation_after_origin_check_cannot_retarget_click(
    page, monkeypatch, chooser
):
    await page.goto("https://accounts.google.com/chooser" if chooser else
                    "https://accounts.google.com/consent")
    await page.set_content(
        ('<h1>Choose an account</h1>' if chooser else '')
        + '<button>' + (PROFILE[1] if chooser else 'Continue') + '</button>'
    )
    checked = False
    evaluate = ElementHandle.evaluate

    async def redirect_after_check(handle, expression, arg=None):
        nonlocal checked
        result = await evaluate(handle, expression, arg)
        if expression == account_flow._GOOGLE_ORIGIN_JS and result and not checked:
            checked = True
            await page.goto(URL + "/application")
            await page.set_content(
                '<button onclick="window.submitted=true">'
                + (PROFILE[1] if chooser else 'Continue') + '</button>'
            )
        return result

    monkeypatch.setattr(ElementHandle, "evaluate", redirect_after_check)
    with pytest.raises(PlaywrightError):
        await account_flow._google_step(page, PROFILE[1], set())
    assert checked and not await page.evaluate("!!window.submitted")


@pytest.mark.parametrize("operation", ["google_button", "switch_form", "terms"])
async def test_account_controls_never_retarget_after_handle_validation(
    page, services, monkeypatch, operation
):
    await page.goto(URL + "/signin")
    await page.set_content({
        "google_button": '<button id=target>Continue with Google</button>',
        "switch_form": '<input type=password><button type=button id=target>Create account</button>',
        "terms": '<label><input type=checkbox id=target>I agree to the terms</label>',
    }[operation])
    marker = {
        "google_button": "googleControl(e) && accountContainer(e)",
        "switch_form": "state.label === expected",
        "terms": "label(e) === expected",
    }[operation]
    evaluate = ElementHandle.evaluate
    replaced = False

    async def replace_after_check(handle, expression, arg=None):
        nonlocal replaced
        result = await evaluate(handle, expression, arg)
        if marker in expression and result and not replaced:
            replaced = True
            await evaluate(handle, """e => {
                const replacement=e.cloneNode(true);
                replacement.onclick=() => {window.uncheckedClick=true};
                e.replaceWith(replacement);
            }""")
        return result

    monkeypatch.setattr(ElementHandle, "evaluate", replace_after_check)
    with pytest.raises((PlaywrightError, account_flow.AccountError)):
        if operation == "google_button":
            button = await account_flow._google_button(page)
            assert button is not None
            try:
                await button.click(timeout=1500)
            finally:
                await button.dispose()
        elif operation == "switch_form":
            acct = await account_page(page)
            assert acct is not None
            current = flow(services)
            current.mode = "signup"
            await current._switch_form(page, acct)
        else:
            await account_flow.tick_required_terms(page)
    assert replaced and not await page.evaluate("!!window.uncheckedClick")


@pytest.mark.parametrize("scope", [
    '<ul><li>Date of birth</li></ul>', '<p>Read your payment history</p>',
    '<div data-scope="photos">Photos</div>', '<p>See your name and home address</p>',
])
async def test_google_consent_unknown_permissions_are_not_identity(page, scope):
    await page.goto("https://accounts.google.com/consent")
    await page.set_content(scope + '<button onclick="window.approved=true">Continue</button>')
    assert not await account_flow._google_step(page, PROFILE[1], set())
    assert not await page.evaluate("!!window.approved")


async def test_google_identity_scope_list_and_gmail_address_are_allowed(page):
    await page.goto("https://accounts.google.com/consent")
    await page.set_content(
        '<h1>Sign in to Example</h1><p>ada@gmail.com</p><ul>'
        '<li>See your name and email address</li>'
        '<li>Language preference</li><li>Profile picture</li></ul>'
        '<button onclick="window.approved=true">Continue</button>'
    )
    assert await account_flow._google_step(page, "ada@gmail.com", set())
    assert await page.evaluate("!!window.approved")
