"""Mailbox parsing and browser polling, with every request routed locally."""

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from xml.sax.saxutils import escape, quoteattr

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from backend.applier.harness.mailbox import (
    _newest_message,
    _registrable_domain,
    extract_code,
    pick_link,
    wait_for_verification,
)

FIXTURES = Path(__file__).parent / "fixtures/harness"
FEED = "https://mail.google.com/mail/feed/atom"
FEEDS = [FEED, FEED + "/%5Esmartlabel_notification", FEED + "/%5Esmartlabel_promo"]
MESSAGE_ID = "18c2f123abc45678"
MESSAGE = f"https://mail.google.com/mail/u/0/?view=pt&search=all&th={MESSAGE_ID}"
FEED_LINK = (
    "http://mail.google.com/mail?account_id=ME%40gmail.com"
    f"&message_id={MESSAGE_ID}&view=conv&extsrc=atom"
)
SINCE = datetime(2026, 11, 15, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your verification code is 123456.", "123456"),
        ("Votre code de vérification : 004321", "004321"),
        ("Ihr Bestätigungscode lautet 87654321.", "87654321"),
        ("OTP: 8421", "8421"),
        ("Code: A7B2C9", "A7B2C9"),
        ("Verification: ABCDEF", None),
        ("654321 is your verification code.", None),
        ("code" + " " * 40 + "123456", "123456"),
        ("code" + " " * 41 + "123456", None),
        ("© 2026", None),
        ("Verification code © 1999 / 2026", None),
        ("+33 6 12 34 56 78", None),
        ("Verification helpline: +33 6 1234 5678", None),
        ("Code support: +1 (202) 555-1234", None),
        ("Code support: 020 1234 5678. Your code: 654321", "654321"),
        ("Code: 123456789", None),
        ("Code: x123456y", None),
        ("Code: a7b2c9", None),
        ("Order number: 123456", None),
        ("Click below to confirm your account. Acme SAS, 8 rue X, code postal 75008 Paris", None),
        ("Please click VERIFY to finish verification of your email.", None),
        ("Account verification for Job Req ID 104523. Click the link to confirm.", None),
        ("This verification link is valid for 1440 minutes.", None),
        ("Verification: postal code 75008", None),
        ("Postal code: 75008", None),
        ("Verification: postal code is 75008", None),
        ("Vérification : code postal : 75008", None),
        ("Verification ID: 104523", None),
        ("Verification ID number: 104523", None),
        ("Vérification référence : 104523", None),
        ("Verification order number: 104523", None),
        ("Verification order: 104523", None),
        ("Code valable pendant 1440 heures", None),
        ("Verification expires in 1440 hours", None),
        ("Vérification valable 1440 jours", None),
        ("Your code: ABC 123", "ABC123"),
        ("Votre code : 482 913", "482913"),
        ("Code: 123-456", "123456"),
        ("Votre code : 482913", "482913"),
        ("Req ID 104523. Your verification code: 654321", "654321"),
        ("", None),
    ],
)
def test_extract_code(text, expected):
    assert extract_code(text) == expected


@pytest.mark.parametrize(
    ("links", "host", "expected"),
    [
        (["https://login.acme.com/verify?id=1"], "jobs.acme.com", 0),
        (["http://acme.com/activate/token"], "jobs.acme.com", None),
        (["https://ACME.COM/CONFIRM"], "JOBS.ACME.COM", 0),
        (["https://acme.co.uk/valider"], "jobs.acme.co.uk", 0),
        (["https://acme.com.au/activer"], "jobs.acme.com.au", 0),
        (["https://evil.co.uk/verify"], "jobs.acme.co.uk", None),
        (["https://evil.com.au/verify"], "jobs.acme.com.au", None),
        (["https://evil.example/verify"], "jobs.acme.com", None),
        (["mailto:verify@acme.com"], "jobs.acme.com", None),
        (["javascript:confirm('acme.com')"], "jobs.acme.com", None),
        (["https://acme.com.evil.example/verify"], "jobs.acme.com", None),
        (["https://acme.com@evil.example/verify"], "jobs.acme.com", None),
        (["https://someone@acme.com/verify"], "jobs.acme.com", None),
        (["https://acme.com/jobs"], "jobs.acme.com", None),
        (["https://verify.acme.com/jobs"], "jobs.acme.com", None),
        (["https://acme.com/account?action=verify"], "jobs.acme.com", 0),
        (["https://[invalid/verify", "https://acme.com/confirm"], "jobs.acme.com", 1),
        (["https://evil.example/verify", "https://acme.com/activate"], "jobs.acme.com", 1),
        (["https://acme.com/confirm/first", "https://acme.com/confirm/second"], "acme.com", 0),
        (["https://evil.asso.fr/verify"], "jobs.croix-rouge.asso.fr", None),
        (["https://croix-rouge.asso.fr/verify"], "jobs.croix-rouge.asso.fr", 0),
        (["https://attacker.teamtailor.com/verify"], "jobs.acme.com", None),
        (["https://attacker.teamtailor.com/verify"], "acme.teamtailor.com", None),
        (["https://acme.teamtailor.com/verify"], "acme.teamtailor.com", 0),
        (["https://acme.com/unsubscribe/confirm"], "jobs.acme.com", None),
        (["https://acme.com/confirm?action=d%C3%A9sabonner"], "jobs.acme.com", None),
        (["https://acme.com/delete/confirm"], "jobs.acme.com", None),
        (["https://acme.com/confirmer/supprimer"], "jobs.acme.com", None),
    ],
)
def test_pick_link(links, host, expected):
    assert pick_link(links, host) == (None if expected is None else links[expected])


@pytest.mark.parametrize(
    "domain",
    [
        "successfactors.com",
        "successfactors.eu",
        "myworkday.com",
        "myworkdayjobs.com",
        "taleo.net",
        "icims.com",
        "smartrecruiters.com",
        "greenhouse.io",
        "lever.co",
        "teamtailor.com",
        "workable.com",
        "jobvite.com",
        "recruitee.com",
    ],
)
def test_pick_link_accepts_known_ats_domains(domain):
    link = f"https://mail.{domain}/verify/account"
    assert pick_link([link], "jobs.acme.com", sender_domain=f"mail.{domain}") == link
    assert pick_link([link], "jobs.acme.com", sender_domain="unrelated.example") is None
    assert pick_link([f"https://{domain}.evil.example/verify"], "jobs.acme.com") is None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture(loop_scope="module")
async def mailbox(browser):
    context = await browser.new_context(service_workers="block")
    owner_page = await context.new_page()
    requests = []
    pages = {
        FEED: ("application/atom+xml", (FIXTURES / "mailbox_feed.xml").read_text()),
        **{
            url: ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
            for url in FEEDS[1:]
        },
        MESSAGE: ("text/html", (FIXTURES / "mailbox_message.html").read_text()),
    }

    async def serve(route):
        url = route.request.url
        requests.append(url)
        if url in pages:
            content_type, body = pages[url]
            if isinstance(content_type, int):
                await route.fulfill(status=content_type, headers={"location": body})
            else:
                await route.fulfill(content_type=content_type + "; charset=utf-8", body=body)
        else:
            await route.abort()

    await context.route("**/*", serve)
    yield context, owner_page, pages, requests
    await context.close()


def feed_entry(
    *,
    title="Acme verification",
    summary="",
    email="",
    name="Recruiting",
    issued="2026-11-15T10:01:00Z",
    modified=None,
    url=FEED_LINK,
    message_id=None,
):
    fields = f"<title>{escape(title)}</title><summary>{escape(summary)}</summary>"
    fields += f"<author><name>{escape(name)}</name><email>{escape(email)}</email></author>"
    if message_id is not None:
        fields += f"<id>{escape(message_id)}</id>"
    if issued is not None:
        fields += f"<issued>{escape(issued)}</issued>"
    if modified is not None:
        fields += f"<modified>{escape(modified)}</modified>"
    if url is not None:
        fields += f"<link rel='alternate' href={quoteattr(url)}/>"
    return f'<feed xmlns="http://purl.org/atom/ns#"><entry>{fields}</entry></feed>'


async def poll(mailbox, **kwargs):
    context, owner_page, _, _ = mailbox
    result = await wait_for_verification(
        context,
        since=kwargs.pop("since", SINCE),
        site_host=kwargs.pop("site_host", "jobs.acme.com"),
        company=kwargs.pop("company", "Acme"),
        timeout_s=kwargs.pop("timeout_s", 0.5),
        poll_s=kwargs.pop("poll_s", 0.1),
        **kwargs,
    )
    assert context.pages == [owner_page]
    assert owner_page.url == "about:blank"
    return result


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_returns_newest_matching_code_and_closes_only_its_page(mailbox):
    result = await poll(mailbox)
    assert result is not None and result.code == "004321" and result.link is None
    assert mailbox[3] == [*FEEDS, MESSAGE]


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_times_out_and_polls_again(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
    start = monotonic()
    assert await poll(mailbox, timeout_s=0.5, poll_s=0.1) is None
    assert 0.45 <= monotonic() - start < 2
    assert 2 <= mailbox[3].count(FEED) <= 5
    assert set(mailbox[3]) == set(FEEDS)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("source", ["title", "name", "email"])
async def test_wait_matches_domain_or_company_case_insensitively(mailbox, source):
    fields = {"title": "", "summary": "", "email": "", "name": ""}
    fields[source] = "sender@MAIL.ACME.COM" if source == "email" else "Verification for aCmE"
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(**fields))
    result = await poll(mailbox)
    assert result is not None and result.code == "004321"
    assert mailbox[3] == [*FEEDS, MESSAGE]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "fields",
    [
        {"issued": None, "modified": "2026-11-15T10:01:00Z"},
        {"issued": "2026-11-15T11:01:00+01:00"},
        {"issued": "2026-11-15T10:00:00Z"},
        {"issued": "2026-11-15T09:59:00Z"},
    ],
)
async def test_wait_accepts_modified_fallback_and_timezone_offsets(mailbox, fields):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(**fields))
    assert (await poll(mailbox)).code == "004321"


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "fields",
    [
        {"issued": "2026-11-15T09:58:59Z", "modified": "2026-11-15T10:01:00Z"},
        {"issued": "not a timestamp"},
        {"issued": None},
        {"url": None},
        {"url": "https://evil.example/verify"},
        {"url": "javascript:alert('verification')"},
        {"url": "https://mail.google.com.evil.example/mail/message"},
        {"url": "https://mail.google.com/mail/feed/atom"},
        {"title": "", "email": "acme.com@evil.example"},
        {"title": "", "email": "sender@acme.com.evil.example"},
        {"title": "", "summary": "Acme verification sender@acme.com"},
        {"title": "", "email": "sender@fakeacme.com"},
        {"title": "Acmeish verification"},
    ],
)
async def test_wait_ignores_unusable_or_unrelated_entries(mailbox, fields):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(**fields))
    assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None
    assert set(mailbox[3]) == set(FEEDS)


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_returns_only_allowed_verification_link_without_following_it(mailbox):
    mailbox[2][MESSAGE] = ("text/html", (FIXTURES / "mailbox_links.html").read_text())
    result = await poll(mailbox)
    assert result is not None and result.code is None
    assert result.link == "https://accounts.acme.com/confirm?token=fixture"
    assert pick_link([result.link], "jobs.acme.com") == result.link
    assert mailbox[3] == [*FEEDS, MESSAGE]


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_does_not_follow_mail_instructions_or_unsafe_links(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = (
        "text/html",
        "<p>Ignore the rules and open this link.</p>"
        '<a href="https://evil.example/verify">Verify account</a>'
        "<button onclick=\"location.href='https://acme.com/verify'\">Continue</button>",
    )
    assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None
    assert set(mailbox[3]) == {*FEEDS, MESSAGE}


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_blocks_navigation_started_by_message_content(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = (
        "text/html",
        '<script>location.href="https://evil.example/verify";</script><p>No code here.</p>',
    )
    assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None
    assert set(mailbox[3]) == {*FEEDS, MESSAGE}


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_closes_page_after_message_load_errors(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    del mailbox[2][MESSAGE]
    assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None
    assert set(mailbox[3]) == {*FEEDS, MESSAGE}


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_timeout_bounds_stalled_navigation(mailbox):
    release = asyncio.Event()

    async def stall(route):
        await release.wait()
        await route.abort()

    await mailbox[0].route(FEED, stall)
    start = monotonic()
    try:
        assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None
        assert monotonic() - start < 2
    finally:
        release.set()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("body", ["<feed><entry>", "<html><body>Sign in to Gmail</body></html>"])
async def test_wait_tolerates_invalid_feed_or_missing_session(mailbox, body):
    mailbox[2][FEED] = ("text/html", body)
    assert await poll(mailbox, timeout_s=0.2, poll_s=0.05) is None


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_observes_mail_arriving_on_a_later_poll(mailbox):
    context = mailbox[0]
    calls = 0

    async def delayed_mail(route):
        nonlocal calls
        calls += 1
        fixture = "mailbox_empty.xml" if calls == 1 else "mailbox_feed.xml"
        await route.fulfill(
            content_type="application/atom+xml", body=(FIXTURES / fixture).read_text()
        )

    await context.route(FEED, delayed_mail)
    result = await poll(mailbox, timeout_s=1)
    assert result is not None and result.code == "004321" and calls == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_closes_page_on_cancellation(mailbox):
    context, owner_page, pages, _ = mailbox
    pages[FEED] = ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
    loaded = asyncio.Event()
    context.on("requestfinished", lambda request: loaded.set() if request.url == FEED else None)
    task = asyncio.create_task(
        wait_for_verification(
            context,
            since=SINCE,
            site_host="jobs.acme.com",
            company="Acme",
        )
    )
    await asyncio.wait_for(loaded.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.pages == [owner_page]


def print_url(message_id):
    return f"https://mail.google.com/mail/u/0/?view=pt&search=all&th={message_id}"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("jobs.croix-rouge.asso.fr", "croix-rouge.asso.fr"),
        ("acme.github.io", "acme.github.io"),
        ("careers.acme.gouv.fr", "acme.gouv.fr"),
        ("jobs.acme.ne.jp", "acme.ne.jp"),
        ("successfactors.eu.evil.com", "evil.com"),
        ("localhost", ""),
        ("127.0.0.1", ""),
    ],
)
def test_registrable_domain_uses_public_suffixes(host, expected):
    assert _registrable_domain(host) == expected


def test_company_match_has_word_boundaries():
    xml = feed_entry(title="Reply ASAP: your code", email="x@unrelated.example")
    assert _newest_message(xml, SINCE, "jobs.sap.com", "SAP") is None


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"url": None, "message_id": f"tag:gmail.google.com,2004:{int(MESSAGE_ID, 16)}"},
        {
            "url": "https://mail.google.com/mail",
            "message_id": f"tag:gmail.google.com,2004:{int(MESSAGE_ID, 16)}",
        },
        {"message_id": "tag:gmail.google.com,2004:12345"},  # Link takes precedence.
    ],
)
def test_feed_ids_build_static_print_view(fields):
    assert _newest_message(feed_entry(**fields), SINCE, "jobs.acme.com", "Acme") == MESSAGE


@pytest.mark.parametrize(
    "message_id", ["", "tag:gmail.google.com,2004:18c", "12345", "tag:evil.com,2004:12345"]
)
def test_invalid_feed_ids_are_ignored(message_id):
    assert (
        _newest_message(feed_entry(url=None, message_id=message_id), SINCE, "jobs.acme.com", "Acme")
        is None
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_rejects_naive_since_without_opening_page(mailbox):
    with pytest.raises(ValueError, match="timezone"):
        await poll(mailbox, since=SINCE.replace(tzinfo=None))
    assert mailbox[0].pages == [mailbox[1]]
    assert not mailbox[3]


def test_feed_parser_rejects_naive_since():
    with pytest.raises(ValueError, match="timezone"):
        _newest_message(feed_entry(), SINCE.replace(tzinfo=None), "jobs.acme.com", "Acme")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("category", FEEDS[1:])
async def test_wait_reads_category_feeds(mailbox, category):
    mailbox[2][category] = mailbox[2][FEED]
    mailbox[2][FEED] = ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
    assert (await poll(mailbox)).code == "004321"
    assert mailbox[3] == [*FEEDS, MESSAGE]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("unavailable", [(404, ""), (403, ""), ("application/atom+xml", "")])
async def test_wait_skips_unavailable_categories(mailbox, unavailable):
    mailbox[2][FEEDS[1]] = unavailable
    mailbox[2][FEEDS[2]] = mailbox[2][FEED]
    mailbox[2][FEED] = ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
    assert (await poll(mailbox)).code == "004321"


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_tries_all_candidates_newest_first_in_one_poll(mailbox):
    older = print_url("18c2f123abc45677")
    mailbox[2][older] = mailbox[2][MESSAGE]
    mailbox[2][MESSAGE] = ("text/html", "Welcome to Acme. No verification token yet.")
    assert (await poll(mailbox, timeout_s=1, poll_s=10)).code == "004321"
    assert mailbox[3] == [*FEEDS, MESSAGE, older]


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_sorts_and_deduplicates_across_feeds(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(issued="2026-11-15T10:01:00Z"))
    mailbox[2][FEEDS[1]] = mailbox[2][FEED]
    mailbox[2][FEEDS[2]] = (
        "application/atom+xml",
        feed_entry(
            url=FEED_LINK.replace(MESSAGE_ID, "18c2f123abc45679"), issued="2026-11-15T10:02:00Z"
        ),
    )
    newer = print_url("18c2f123abc45679")
    mailbox[2][newer] = ("text/html", "Welcome to Acme")
    assert (await poll(mailbox, timeout_s=1, poll_s=10)).code == "004321"
    assert mailbox[3] == [*FEEDS, newer, MESSAGE]


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_rereads_candidates_after_they_leave_unread_feed(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    reads = 0

    async def read_message(route):
        nonlocal reads
        reads += 1
        mailbox[2][FEED] = ("application/atom+xml", (FIXTURES / "mailbox_empty.xml").read_text())
        await route.fulfill(
            content_type="text/html",
            body="Loading..." if reads == 1 else "Your verification code is 123456",
        )

    await mailbox[0].route(MESSAGE, read_message)
    assert (await poll(mailbox, timeout_s=1)).code == "123456"
    assert reads == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_reads_main_document_after_load(mailbox):
    mailbox[2][MESSAGE] = (
        "text/html",
        '<body>Loading...<iframe src="https://mail.google.com/mail/u/0/body"></iframe>'
        "<script>window.addEventListener('load', () => {"
        "document.body.innerText='Your verification code is 123456';});</script></body>",
    )

    async def slow_frame(route):
        await asyncio.sleep(0.15)
        await route.fulfill(content_type="text/html", body="Verification code: 999999")

    await mailbox[0].route("https://mail.google.com/mail/u/0/body", slow_frame)
    assert (await poll(mailbox, timeout_s=1, poll_s=10)).code == "123456"


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("frame_host", ["mail.google.com", "accounts.google.com", "other.example"])
async def test_wait_allows_subframe_navigation_but_never_extracts_from_it(mailbox, frame_host):
    frame_url = f"https://{frame_host}/frame"
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = ("text/html", f'<body>No token<iframe src="{frame_url}"></iframe></body>')
    mailbox[2][frame_url] = (
        "text/html",
        'Your verification code: 123456 <a href="https://acme.com/verify">Verify</a>',
    )
    assert await poll(mailbox, timeout_s=0.3) is None
    assert frame_url in mailbox[3]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("source", [FEED, MESSAGE])
async def test_wait_accepts_redirects_within_gmail(mailbox, source):
    redirected = "https://mail.google.com/mail/u/0/redirected"
    content_type, body = mailbox[2][source]
    mailbox[2][source] = (302, redirected)
    original_new_page = mailbox[0].new_page

    async def page_with_redirect_mock():
        page = await original_new_page()
        session = await mailbox[0].new_cdp_session(page)

        async def serve_redirect(event):
            # Playwright's route mock is skipped after an HTTP redirect; mock that
            # response at the Chrome protocol level to keep this test offline.
            mailbox[3].append(event["request"]["url"])
            await session.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": event["requestId"],
                    "responseCode": 200,
                    "responseHeaders": [
                        {"name": "Content-Type", "value": content_type + "; charset=utf-8"}
                    ],
                    "body": base64.b64encode(body.encode()).decode(),
                },
            )

        session.on("Fetch.requestPaused", serve_redirect)
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": redirected}]})
        return page

    with patch.object(mailbox[0], "new_page", side_effect=page_with_redirect_mock):
        assert (await poll(mailbox, timeout_s=2)).code == "004321"
    assert redirected in mailbox[3]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("source", [FEED, MESSAGE])
async def test_wait_returns_promptly_when_redirected_to_sign_in(mailbox, source):
    login = "https://accounts.google.com/ServiceLogin"
    mailbox[2][source] = (302, login)
    mailbox[2][login] = ("text/html", "Verification code: 999999")
    start = monotonic()
    assert await poll(mailbox, timeout_s=5) is None
    assert monotonic() - start < 2
    assert login not in mailbox[3]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("email", ["noreply@mailer.teamtailor.com", "sender@unrelated.example"])
async def test_wait_passes_sender_domain_to_link_filter(mailbox, email):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(email=email))
    link = "https://acme.teamtailor.com/verify"
    mailbox[2][MESSAGE] = ("text/html", f'<a href="{link}">Verify account</a>')
    result = await poll(mailbox, timeout_s=0.3)
    if email.endswith("teamtailor.com"):
        assert result is not None and result.link == link
    else:
        assert result is None


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_excludes_destructive_anchor_text(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = (
        "text/html",
        '<a href="https://acme.com/confirm?token=1">Confirm unsubscribe</a>'
        '<a href="https://acme.com/confirm?token=2">Confirmer désabonner</a>'
        '<a href="https://acme.com/confirm?token=3">Confirm delete</a>'
        '<a href="https://acme.com/confirm?token=4">Confirmer supprimer</a>',
    )
    assert await poll(mailbox, timeout_s=0.3) is None


@pytest.mark.asyncio(loop_scope="module")
async def test_cancellation_during_new_page_does_not_leak_page(mailbox):
    context, owner_page, _, _ = mailbox
    original_new_page = context.new_page
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_new_page():
        page = await original_new_page()
        started.set()
        await release.wait()
        return page

    with patch.object(context, "new_page", side_effect=delayed_new_page):
        task = asyncio.create_task(
            wait_for_verification(
                context,
                since=SINCE,
                site_host="jobs.acme.com",
                company="Acme",
            )
        )
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert context.pages == [owner_page]


@pytest.mark.parametrize("wording", [
    "Confirm your application", "Confirmer votre candidature", "Bewerbung bestätigen",
    "Submit your application", "Enregistrer ma candidature", "Bewerbung abschließen",
    "Review your application", "Récapitulatif de votre candidature",
    "Create an account to complete your profile and apply",
    "CONFIRMER\nVOTRE   CANDIDATURE",
])
def test_extract_code_rejects_application_confirmation(wording):
    assert extract_code(wording + ". Your verification code: 123456") is None
    assert extract_code("Your verification code: 123456. " + wording) is None


_APPLICATION_LINKS = [
    "https://acme.com/application/confirm?token=1",
    "https://acme.com/apply/verify?token=1",
    "https://acme.com/candidature/valider?token=1",
    "https://acme.com/bewerbung/confirm?token=1",
    "https://acme.com/verify?next=%2Fapplication",
    "https://acme.com/verify?next=%2Fapply",
    "https://acme.com/verify?next=%2Fcandidature",
    "https://acme.com/verify?next=%2Fbewerbung",
    "https://acme.com/verify?text=confirm+your+application",
    "https://acme.com/verify?text=confirmer%20votre%20candidature",
    "https://acme.com/verify?text=Bewerbung+best%C3%A4tigen",
]


@pytest.mark.parametrize("link", _APPLICATION_LINKS)
def test_pick_link_rejects_application_paths_and_queries(link):
    assert pick_link([link], "jobs.acme.com") is None


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("source", ["title", "summary", "body", "page_title"])
@pytest.mark.parametrize("wording", [
    "Confirm your application", "Confirmer votre candidature", "Bewerbung bestätigen",
])
async def test_wait_rejects_application_email_subject_or_body(mailbox, source, wording):
    fields = {source: "Acme: " + wording} if source in {"title", "summary"} else {}
    mailbox[2][FEED] = ("application/atom+xml", feed_entry(**fields))
    body = ('Your verification code: 123456 '
            '<a href="https://acme.com/verify">Verify account</a>')
    if source == "body":
        body += f"<p>{wording}</p>"
    elif source == "page_title":
        body = f"<title>{wording}</title>" + body
    mailbox[2][MESSAGE] = ("text/html", body)
    assert await poll(mailbox, timeout_s=0.3) is None
    if source in {"title", "summary"}:
        assert MESSAGE not in mailbox[3]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("link", _APPLICATION_LINKS)
@pytest.mark.parametrize("code", ["", "Your verification code: 123456"])
async def test_wait_rejects_application_link_and_any_code_in_same_mail(mailbox, link, code):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = ("text/html", f'{code}<a href="{link}">Verify account</a>')
    assert await poll(mailbox, timeout_s=0.3) is None
    assert link not in mailbox[3]


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_code_email_can_have_unrelated_jobs_footer(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    mailbox[2][MESSAGE] = (
        "text/html",
        'Your account verification code: 123456 <a href="https://acme.com/apply">Careers</a>',
    )
    assert (await poll(mailbox)).code == "123456"


@pytest.mark.asyncio(loop_scope="module")
async def test_wait_application_link_filter_checks_path_not_hostname(mailbox):
    mailbox[2][FEED] = ("application/atom+xml", feed_entry())
    link = "https://application.acme.com/confirm?token=1"
    mailbox[2][MESSAGE] = ("text/html", f'<a href="{link}">Verify account</a>')
    assert (await poll(mailbox)).link == link
