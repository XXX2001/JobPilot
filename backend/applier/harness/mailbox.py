"""Read verification mail through the existing Chrome session, without clicking mail."""

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit
from xml.etree import ElementTree

from playwright.async_api import BrowserContext, Page, Response
from playwright.async_api import Error as PlaywrightError
from tld import get_fld

FEED_URL = "https://mail.google.com/mail/feed/atom"
FEED_URLS = (
    FEED_URL,
    f"{FEED_URL}/%5Esmartlabel_notification",
    f"{FEED_URL}/%5Esmartlabel_promo",
)
ATS_MAIL_DOMAINS = frozenset(
    {
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
    }
)

_ATOM = "{http://purl.org/atom/ns#}"
_CODE_WORD = re.compile(r"\b(?:code|verification|vérification|bestätigung(?:scode)?|otp)\b", re.I)
_CODE = re.compile(r"(?<!\w)(?:[A-Z0-9]{3}[\s-][A-Z0-9]{3}|[0-9]{4,8}|[A-Z0-9]{6})(?!\w)")
_NUMBER = re.compile(r"(?<!\w)\+?[0-9](?:[0-9 .()/-]*[0-9])?(?!\w)")
_VERIFY_LINK = re.compile(r"verify|verification|confirm|activate|valider|activer", re.I)
_EXCLUDED_LINK = re.compile(r"unsubscribe|désabonner|desabonner|delete|supprimer", re.I)
_NON_CODE_LABEL = re.compile(
    r"\b(?:code\s+postal|postal\s+code|(?:req\s+)?id|r[eé]f[eé]rence|"
    r"order)(?:\s+(?:is|est|number|no\.?))?\s*[:#=.-]?\s*$",
    re.I,
)
_DURATION = re.compile(r"\s*(?:minutes?|hours?|jours?|heures?|days?|stunden?)\b", re.I)


@dataclass(frozen=True)
class Verification:
    code: str | None = None
    link: str | None = None


def extract_code(text: str) -> str | None:
    """Choose a code up to 40 characters after a cue, excluding common unrelated numbers."""
    cues = [
        cue
        for cue in _CODE_WORD.finditer(text)
        if not (
            re.match(r"\s+postal\b", text[cue.end() :], re.I)
            or re.search(r"\bpostal\s+$", text[: cue.start()], re.I)
        )
    ]
    phones = [
        number.span()
        for number in _NUMBER.finditer(text)
        if number.group().startswith("+") or len(re.sub(r"\D", "", number.group())) > 8
    ]
    candidates: list[tuple[int, int, str]] = []
    for match in _CODE.finditer(text):
        value = re.sub(r"[\s-]", "", match.group())
        if not any(character.isdigit() for character in value):
            continue
        if re.fullmatch(r"(?:19|20)[0-9]{2}", value):
            continue
        if any(start <= match.start() and match.end() <= end for start, end in phones):
            continue
        if _NON_CODE_LABEL.search(text[: match.start()]) or _DURATION.match(text[match.end() :]):
            continue
        for cue in cues:
            distance = match.start() - cue.end()
            if 0 <= distance <= 40:
                candidates.append((distance, match.start(), value))
    return min(candidates)[2] if candidates else None


def _registrable_domain(host: str) -> str:
    return get_fld(f"https://{host.casefold().rstrip('.')}", fail_silently=True) or ""


def _http_url(link: str) -> SplitResult | None:
    if re.search(r"[\s\\\x00-\x1f\x7f]", link):
        return None
    try:
        url = urlsplit(link)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
        ):
            return None
        _ = url.port  # Reject malformed ports as well as malformed hosts.
        return url
    except ValueError:
        return None


def pick_link(links: list[str], site_host: str, sender_domain: str = "") -> str | None:
    """Choose an HTTPS verification link, binding ATS links to their sender or tenant."""
    site_domain = _registrable_domain(site_host)
    sender_domain = _registrable_domain(sender_domain)
    site_host = site_host.casefold().rstrip(".")
    for link in links:
        url = _http_url(link)
        if url is None or url.scheme != "https":
            continue
        host = (url.hostname or "").casefold().rstrip(".")
        domain = _registrable_domain(host)
        if not domain:
            continue
        if domain in ATS_MAIL_DOMAINS:
            if domain != sender_domain and host != site_host:
                continue
        elif domain != site_domain:
            continue
        wording = unquote(f"{url.path}?{url.query}#{url.fragment}")
        if not _EXCLUDED_LINK.search(wording) and _VERIFY_LINK.search(wording):
            return link
    return None


def _gmail_url(link: str) -> bool:
    url = _http_url(link)
    return url is not None and url.hostname == "mail.google.com"


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("since and mail timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class _Message:
    id: str
    received: datetime
    sender_domain: str

    @property
    def url(self) -> str:
        return f"https://mail.google.com/mail/u/0/?view=pt&search=all&th={self.id}"


def _message_id(entry: ElementTree.Element) -> str | None:
    for link in entry.findall(f"{_ATOM}link"):
        url = _http_url(link.get("href", ""))
        if (
            url is None
            or url.hostname != "mail.google.com"
            or link.get("rel", "alternate") != "alternate"
            or not (url.path == "/mail" or url.path.startswith("/mail/"))
            or url.path.startswith("/mail/feed")
        ):
            continue
        for message_id in parse_qs(url.query).get("message_id", []):
            if re.fullmatch(r"[0-9a-fA-F]{1,32}", message_id):
                return message_id.lower()
    atom_id = entry.findtext(f"{_ATOM}id", "").strip()
    match = re.fullmatch(r"tag:gmail\.google\.com,2004:([0-9]{1,32})", atom_id)
    return format(int(match[1]), "x") if match else None


def _matching_messages(xml: str, since: datetime, site_host: str, company: str) -> list[_Message]:
    cutoff = _utc(since) - timedelta(seconds=60)
    feed = ElementTree.fromstring(xml)
    site_domain = _registrable_domain(site_host)
    company_pattern = (
        re.compile(rf"(?<!\w){re.escape(company.strip())}(?!\w)", re.I) if company.strip() else None
    )
    matches: list[_Message] = []
    for entry in feed.findall(f"{_ATOM}entry"):
        issued = entry.findtext(f"{_ATOM}issued") or entry.findtext(f"{_ATOM}modified")
        if not issued:
            continue
        try:
            received = _utc(datetime.fromisoformat(issued.strip()))
        except ValueError:
            continue
        if received < cutoff:
            continue
        sender = entry.findtext(f"{_ATOM}author/{_ATOM}email", "")
        sender_domain = (
            _registrable_domain(sender.rsplit("@", 1)[-1].strip()) if "@" in sender else ""
        )
        title = entry.findtext(f"{_ATOM}title", "")
        name = entry.findtext(f"{_ATOM}author/{_ATOM}name", "")
        if not (
            (site_domain and site_domain == sender_domain)
            or (company_pattern and (company_pattern.search(title) or company_pattern.search(name)))
        ):
            continue
        message_id = _message_id(entry)
        if message_id:
            matches.append(_Message(message_id, received, sender_domain))
    return sorted(matches, key=lambda message: message.received, reverse=True)


def _newest_message(xml: str, since: datetime, site_host: str, company: str) -> str | None:
    """Return the newest print URL; polling itself tries every matching message."""
    messages = _matching_messages(xml, since, site_host, company)
    return messages[0].url if messages else None


class _SignedOut(Exception):
    pass


async def _gmail_goto(
    page: Page,
    url: str,
    signed_out: asyncio.Event,
    *,
    wait_until: Literal["domcontentloaded", "load"],
) -> Response | None:
    try:
        response = await page.goto(url, wait_until=wait_until)
    except PlaywrightError:
        if signed_out.is_set():
            raise _SignedOut from None
        raise
    current_url = _http_url(page.url)
    if signed_out.is_set() or (current_url and current_url.hostname == "accounts.google.com"):
        raise _SignedOut
    return response if _gmail_url(page.url) else None


async def _read_mail(
    page: Page,
    since: datetime,
    site_host: str,
    company: str,
    candidates: dict[str, _Message],
    signed_out: asyncio.Event,
) -> Verification | None:
    for feed_url in FEED_URLS:
        try:
            response = await _gmail_goto(page, feed_url, signed_out, wait_until="domcontentloaded")
            if response is None or response.status != 200:
                continue
            xml = await response.text()
            if not xml.strip():
                continue
            for message in _matching_messages(xml, since, site_host, company):
                candidates[message.id] = message
        except (ElementTree.ParseError, PlaywrightError):
            continue

    # Gmail removes opened messages from the unread feeds. Retain their IDs for this wait.
    for message in sorted(candidates.values(), key=lambda item: item.received, reverse=True):
        try:
            response = await _gmail_goto(page, message.url, signed_out, wait_until="load")
            if response is None or response.status != 200:
                continue
            text = await page.evaluate("() => document.body?.innerText || ''")
            code = extract_code(text)
            if code:
                return Verification(code=code)
            anchors = await page.locator("a[href]").evaluate_all(
                "anchors => anchors.map(a => ({href: a.href, text: a.innerText}))"
            )
            links = [
                anchor["href"] for anchor in anchors if not _EXCLUDED_LINK.search(anchor["text"])
            ]
            link = pick_link(links, site_host, message.sender_domain)
            if link:
                return Verification(link=link)
        except PlaywrightError:
            continue
    return None


async def wait_for_verification(
    context: BrowserContext,
    *,
    since: datetime,
    site_host: str,
    company: str,
    timeout_s: float = 300,
    poll_s: float = 10,
) -> Verification | None:
    """Poll unread mail in one owned background page; prefer a code to a safe link."""
    since = _utc(since)
    if timeout_s <= 0:
        return None
    page: Page | None = None
    page_task: asyncio.Task[Page] | None = None
    candidates: dict[str, _Message] = {}
    signed_out = asyncio.Event()

    try:
        async with asyncio.timeout(min(timeout_s, 300)):
            # Page creation can finish in Chrome after the caller is cancelled. Keep its
            # task alive so finally can acquire and close exactly the page we created.
            page_task = asyncio.create_task(context.new_page())
            owned_page: Page = await asyncio.shield(page_task)
            page = owned_page
            # Playwright routes only intercept the first request of an HTTP redirect
            # chain. Chrome's Fetch interception also guards every redirected document.
            session = await context.new_cdp_session(owned_page)
            frame_tree = await session.send("Page.getFrameTree")
            main_frame_id = frame_tree["frameTree"]["frame"]["id"]

            async def guard_navigation(event: dict[str, Any]) -> None:
                url = event["request"]["url"]
                if event.get("frameId") == main_frame_id and not _gmail_url(url):
                    parsed = _http_url(url)
                    if parsed and parsed.hostname == "accounts.google.com":
                        signed_out.set()
                    method = "Fetch.failRequest"
                    params = {"requestId": event["requestId"], "errorReason": "Aborted"}
                else:
                    method = "Fetch.continueRequest"
                    params = {"requestId": event["requestId"]}
                with suppress(PlaywrightError):
                    await session.send(method, params)

            session.on("Fetch.requestPaused", guard_navigation)
            await session.send("Fetch.enable", {"patterns": [{"resourceType": "Document"}]})
            while True:
                result = await _read_mail(
                    owned_page, since, site_host, company, candidates, signed_out
                )
                if result is not None:
                    return result
                await asyncio.sleep(max(poll_s, 0.01))
    except (TimeoutError, PlaywrightError, _SignedOut):
        return None
    finally:
        if page is None and page_task is not None:
            with suppress(PlaywrightError):
                page = await asyncio.shield(page_task)
        if page is not None:
            with suppress(PlaywrightError):
                await page.close()
