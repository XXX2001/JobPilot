"""Code-owned account preparation; no secret enters the decision model."""

import asyncio
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote, quote_plus, unquote_plus, urlsplit

from playwright.async_api import ElementHandle, Frame, Page
from playwright.async_api import Error as PlaywrightError

from backend.scraping.site_prompts import SITE_CONFIGS

from . import accounts, mailbox

MAX_NEW_ACCOUNTS_PER_DAY = 10
_SIGNIN = (
    r"connexion|se connecter|me connecter|s['’]identifier|sign in|log in|login|anmelden|einloggen"
)
_SIGNUP = (
    r"créer (?:un|mon) compte|s['’]inscrire|inscription|create (?:an )?account|"
    r"sign up|register|registrieren|konto erstellen"
)
SIGNIN_WORDS = re.compile(rf"^(?:{_SIGNIN})$", re.I)
SIGNUP_WORDS = re.compile(rf"^(?:{_SIGNUP})$", re.I)
VERIFY_WORDS = re.compile(r"^(?:vérifier|verify|confirm|bestätigen)$", re.I)
GOOGLE_BUTTON_WORDS = re.compile(
    r"^(?:sign in with Google|continue with Google|continuer avec Google|"
    r"se connecter avec Google|mit Google anmelden|weiter mit Google)$",
    re.I,
)
_GOOGLE_CONSENT_WORDS = re.compile(
    r"^(?:Continue|Continuer|Autoriser|Allow|Weiter|Zulassen)$", re.I
)
_GOOGLE_CHALLENGE = re.compile(
    r"2[- ]?(?:step|factor)|two[- ](?:step|factor)|verify it['’]s you|"
    r"validez votre identit[eé]|validation en deux [eé]tapes|"
    r"best[aä]tigung in zwei schritten|captcha|not a robot|je ne suis pas un robot",
    re.I,
)
_GOOGLE_EXTRA_SCOPES = re.compile(
    r"\b(?:drive|contacts?|calendar|gmail|agenda|calendrier|fichiers?|files?|"
    r"kalender|kontakte)\b|(?<!e-)\bmail\b",
    re.I,
)
_GOOGLE_IDENTITY_SCOPE = re.compile(
    r"\b(?:full name|name|e-?mail address|language preference|profile picture|"
    r"nom|adresse e-?mail|pr[eé]f[eé]rence linguistique|photo de profil|"
    r"e-mail-adresse|spracheinstellung|profilbild)\b", re.I,
)
_GOOGLE_SCOPE_VERB = re.compile(
    r"\b(?:see|view|read|manage|edit|delete|download|upload|"
    r"consulter|lire|g[eé]rer|modifier|supprimer|anzeigen|lesen|verwalten|l[oö]schen)\b|"
    r"^access\b", re.I,
)
_GOOGLE_SCOPE_CONNECTORS = re.compile(
    r"\b(?:and|your|the|et|votre|vos|und|ihre?|ihren)\b|[\W_]+", re.I,
)
_GOOGLE_OTHER_ACCOUNT = re.compile(
    r"use another account|utiliser un autre compte|anderes konto|\b(?:not|pas)\s+",
    re.I,
)
_EMAIL_IN_TEXT = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+", re.I)
_GOOGLE_START_TIMEOUT_S = 15
_GOOGLE_FINISH_TIMEOUT_S = 30
_SIGNIN_ONLY = SIGNIN_WORDS
_SIGNUP_ONLY = SIGNUP_WORDS
OPT_IN_WORDS = re.compile(
    r"newsletter|marketing|offres|alertes|partenaires|promotional|job\s*alerts?|"
    r"recevoir|receive|e-?mails|communications|actualités|news|updates|Neuigkeiten|"
    r"informiert|talent community|talent pool|vivier|future opportunities",
    re.I,
)
_TERMS = re.compile(
    r"terms|conditions|privacy|confidentialité|CGU|données personnelles|j['’]accepte|"
    r"I agree|Datenschutz|Nutzungsbedingungen|AGB|ich stimme",
    re.I,
)
_ERROR = re.compile(
    r"incorrect|invalide|invalid|introuvable|n['’]existe pas|already exists|existe déjà|"
    r"bereits (?:registriert|vorhanden)|(?:konto|account).*existiert bereits|"
    r"ungültig|falsch|nicht gefunden",
    re.I,
)

# No values, HTML, or password-bearing attributes are returned. Include open shadow roots.
_DOM = r"""
    const query = (root, selector) => [...root.querySelectorAll('*')].flatMap(e =>
        [...(e.matches(selector) ? [e] : []),
         ...(e.shadowRoot ? query(e.shadowRoot, selector) : [])]);
    const visible = e => e.getClientRects().length &&
        e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) &&
        !e.closest('[hidden], [inert], [aria-hidden="true"]');
    const labelText = e => {
        const copy = e.cloneNode(true);
        copy.querySelectorAll('input, select, textarea').forEach(control => control.remove());
        return copy.textContent;
    };
    const label = e => [...new Set([e.getAttribute('aria-label'),
        (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id =>
            e.getRootNode().getElementById?.(id)?.textContent || '').join(' '),
        [...(e.labels || [])].map(labelText).join(' '),
        e.matches('input, select, textarea') ? '' : e.innerText,
        ['submit','button'].includes(e.type) ? e.value : '', e.title]
        .filter(Boolean))].join(' ').replace(/\s+/g, ' ').trim();
    const code = e => {
        const hint = [label(e), e.name, e.id, e.placeholder].join(' ');
        return visible(e) && ['text','tel','number'].includes(e.type) &&
            (e.autocomplete === 'one-time-code' ||
             (!/postal|postcode|zip/i.test(hint) &&
              /\b(code|otp|bestätigungscode)\b|verification[ _-]?code/i.test(hint)));
    };
    const accountField = e => {
        if (e.matches('textarea, [contenteditable], input[type=file]')) return false;
        if (e.matches('input[type=password], input[type=email], input[type=hidden], ' +
                      'input[type=checkbox], [role=checkbox]:not(input):not(select)'))
            return true;
        if (!e.matches('input:not([type]), input[type=text], input[type=tel], ' +
                       'select, [role=combobox]:not(input):not(select)'))
            return false;
        const normalize = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLowerCase().replace(/[^a-z]/g, '');
        const allowed = new RegExp('^(email|emailaddress|courriel|adressee?mail|' +
            'username|user|identifiant|benutzername|firstname|givenname|prenom|lastname|' +
            'familyname|surname|nom|fullname|name|nomcomplet|vorname|nachname|phone|' +
            'phonenumber|telephone|tel|telefon|telefonnummer|country|countryname|pays|land)$');
        const named = label(e);
        return named ? allowed.test(normalize(named)) :
            [e.autocomplete, e.name, e.id, e.placeholder].some(s => allowed.test(normalize(s)));
    };
    const fields = root => query(root, 'input, select, textarea, [contenteditable], ' +
        '[role=textbox], [role=combobox], [role=checkbox], [role=radio], [role=switch], ' +
        '[role=slider], [role=spinbutton]')
        .filter(e => !e.matches('input[type=submit], input[type=button], input[type=reset]'));
    const accountElement = (e, allowCode) =>
        e.matches('button, input[type=submit], input[type=button], input[type=reset]') ||
        (e.matches('input, select') && (accountField(e) || (allowCode && code(e))));
    const applicationText = text => {
        const verb = '(sav|enregistr|submit|soumettr|envoy|send|valid|confirm|complet|' +
            'finali|transmet|abschließ|abschliess|ab(?:zu)?schick|ab(?:zu)?send|' +
            'ein(?:zu)?reich|apply|postul|bewerb)';
        const noun = '(application|candidature|dossier|bewerbung)';
        return new RegExp(verb + '.{0,60}' + noun + '|' + noun + '.{0,60}' + verb +
            '|apply.{0,60}profile?|profile?.{0,60}apply|' +
            'review your application|recapitulatif de (votre|ma) candidature|' +
            'Zusammenfassung Ihrer Bewerbung', 'i').test(
                text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' '));
    };
    const contains = (parent, child) => {
        while (child) {
            if (parent === child) return true;
            child = child.parentElement || child.getRootNode().host;
        }
        return false;
    };
    const accountText = e => {
        let container = e.form || e.closest('form, fieldset, dialog, section');
        if (container) return container.textContent || '';
        const targets = [e, ...query(document, 'input[type=password]')];
        container = e.parentElement || e.getRootNode().host;
        while (container && !targets.every(target => contains(container, target)))
            container = container.parentElement || container.getRootNode().host;
        if (container === document.body) {
            const parts = [...container.children].filter(child =>
                targets.some(target => contains(child, target)));
            parts.push(...query(document, 'h1, h2, h3, [role=heading]'));
            return [...new Set(parts)].map(part => part.textContent || '').join(' ');
        }
        return container?.textContent || '';
    };
    const accountContainer = (e, allowCode = false) => {
        // form= can associate controls outside the form's descendant tree.
        const container = e.form || e.closest('form, fieldset, dialog');
        if (container && (
            [...(container.elements || query(container, '*').filter(c =>
                c.matches('input, select, textarea, button, fieldset, output, object') ||
                c.constructor.formAssociated))].some(c => !accountElement(c, allowCode)) ||
            fields(container).some(c => !accountField(c) && !(allowCode && code(c)))
        )) return false;
        return !applicationText(accountText(e));
    };
    const googleControl = e => {
        const names = [e.getAttribute('aria-label'), e.innerText, e.title,
            ['submit','button'].includes(e.type) ? e.value : '',
            (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id =>
                e.getRootNode().getElementById?.(id)?.textContent || '').join(' ')]
            .filter(Boolean).map(s => s.replace(/\s+/g, ' ').trim());
        return names.some(s =>
            /__GOOGLE_BUTTON_WORDS__/i.test(s)) &&
            !names.some(s => /\bapply\b|postul|candidat|bewerben|submit/i.test(s));
    };
""".replace("__GOOGLE_BUTTON_WORDS__", GOOGLE_BUTTON_WORDS.pattern)
ACCOUNT_PAGE_JS = (
    "() => {"
    + _DOM
    + r"""
    const passwords = query(document, 'input[type=password]').filter(visible);
    const controls = query(document, 'a, button, input[type=submit], input[type=button], ' +
        '[role=button], [role=link]')
        .filter(visible);
    const codes = query(document, 'input').filter(code);
    return {
        has_password: passwords.length > 0, password_inputs: passwords.length,
        google_button: controls.some(e => googleControl(e) && accountContainer(e)) ||
            query(document, 'iframe').some(e => visible(e) && accountContainer(e) &&
                /^https:\/\/accounts\.google\.com\/gsi\//i.test(e.src)),
        signup_link: controls.map(label).some(t =>
            /cr[ée]er un compte|s['’]inscrire|create (an )?account/i.test(t) ||
            /sign.?up|register|registrieren/i.test(t)),
        code_field: codes.length > 0,
        check_email_text: [
            /check (your )?(e-?mail|inbox)/i,
            /v[ée]rifiez (votre |vos )?(e-?mail|messagerie)/i,
            /consultez (votre |vos )?(e-?mail|messagerie)/i,
            /prüfen sie.*(e-mail|postfach)/i
        ].some(pattern => pattern.test(document.body?.innerText || '')),
        input_count: fields(document).filter(e => !e.matches('input[type=hidden]')).length,
        code_count: codes.length,
        application: query(document, 'input[type=file], textarea, [contenteditable]').length > 0 ||
            fields(document).some(e => !accountField(e) && !code(e)) ||
            query(document, 'form').some(f =>
                [...f.elements].some(e => !accountElement(e, true))) ||
            [...passwords, ...codes, ...controls.filter(e =>
                !e.form && !e.closest('form, fieldset, dialog, section') &&
                /__ACCOUNT_WORDS__/i.test(label(e)))].some(e => !accountContainer(e, true))
    };
}"""
).replace("__ACCOUNT_WORDS__", rf"^(?:{_SIGNIN}|{_SIGNUP})$")
_LABEL_JS = "e => {" + _DOM + "return label(e); }"
_CODE_JS = "e => {" + _DOM + "return code(e); }"
_ACCOUNT_CONTROL_JS = "(e, allowCode) => {" + _DOM + "return accountContainer(e, allowCode); }"
_GOOGLE_CONTROL_JS = "e => {" + _DOM + "return googleControl(e); }"
_GOOGLE_ORIGIN_JS = (
    'e => e.isConnected && e.ownerDocument.location.origin === "https://accounts.google.com"'
)
_SWITCH_STATE_JS = r"""e => {
    return {label: (e.getAttribute('aria-label') || e.innerText || e.value || '').trim(),
        submits: !!e.form && e.type === 'submit'};
}"""
_APPLICATION_SEEN_JS = "nodes => {" + _DOM + r"""
    if (query(document, 'input[type=file], textarea, [contenteditable]').length) return true;
    if (query(document, 'input[type=radio], input[type=checkbox], [role=radio], ' +
        '[role=checkbox], [role=switch]').some(e => e.form || e.closest('form'))) return true;
    const search = e => e.type === 'search' || e.getAttribute('role') === 'searchbox' ||
        e.closest('[role=search], form[action*="search" i]') ||
        /\b(search|rechercher?|recherche par|suche|suchen|keywords?|mot-clé|mots-clés)\b/i
            .test(label(e));
    return nodes.some(node => {
        const e = window.__jobpilotHarness?.nodes.get(node);
        return e?.isConnected && !search(e);
    });
}"""


class AccountError(RuntimeError):
    """A safe account error which never contains a browser exception's inputs."""


class AccountHandoff(AccountError):
    """The owner must finish this account step."""


@dataclass(frozen=True)
class AccountPage:
    has_password: bool
    password_inputs: int
    google_button: bool
    signup_link: bool
    code_field: bool
    check_email_text: bool
    code_only: bool = False


def site_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def account_allowed(url: str) -> bool:
    host = site_host(url)
    # Indeed currently has requires_login=False (manual apply), but the spec excludes it too.
    excluded = {
        "linkedin.com",
        "indeed.com",
        "welcometothejungle.com",
        "gmail.com",
        "live.com",
        "microsoft.com",
        "microsoftonline.com",
        "proton.me",
        "apple.com",
        "icloud.com",
    }
    for config in SITE_CONFIGS.values():
        if config.get("requires_login"):
            for key in ("base_url", "login_url", "url"):
                owner_host = site_host(config.get(key, "")).removeprefix("www.")
                if owner_host:
                    excluded.add(owner_host)
    provider = re.search(r"(?:^|\.)(?:google|outlook|yahoo|protonmail)\.[^.]+(?:\.[^.]+)?$", host)
    return (
        bool(host)
        and not provider
        and not any(host == h or host.endswith("." + h) for h in excluded)
    )


def _origin(frame: Frame) -> tuple:
    while frame.parent_frame is not None and frame.url in {"about:blank", "about:srcdoc"}:
        frame = frame.parent_frame
    parsed = urlsplit(frame.url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


async def _visible_frame(frame: Frame) -> bool:
    while frame.parent_frame is not None:
        element = await frame.frame_element()
        try:
            if not await element.is_visible():
                return False
        finally:
            await element.dispose()
        frame = frame.parent_frame
    return True


async def _writable_frames(page: Page) -> list[Frame]:
    return [
        frame
        for frame in page.frames
        if _origin(frame) == _origin(page.main_frame) and await _visible_frame(frame)
    ]


async def _account_page(
    page: Page, *, after_signup: bool = False, google: bool = False, strict: bool = False
) -> AccountPage | None:
    """Reject non-account fields in every frame, including hidden frames and shadow roots."""
    states = []
    input_count = code_count = 0
    try:
        for frame in page.frames:
            state = await frame.evaluate(ACCOUNT_PAGE_JS)
            if state.pop("application"):
                return None
            input_count += state.pop("input_count")
            code_count += state.pop("code_count")
            if _origin(frame) == _origin(page.main_frame) and await _visible_frame(frame):
                states.append(state)
    except PlaywrightError:
        if strict:
            raise
        return None  # An unreadable frame cannot justify relaxing the submit guard.
    if not states:
        return None
    merged: dict[str, Any] = {name: any(s[name] for s in states) for name in states[0]}
    merged["password_inputs"] = sum(s["password_inputs"] for s in states)
    merged["code_only"] = input_count == code_count == 1
    if code_count and not merged["code_only"]:
        return None
    acct = AccountPage(**merged)
    return (
        acct
        if (
            acct.has_password
            or (google and acct.google_button)
            or (after_signup and (acct.code_field or acct.check_email_text))
        )
        else None
    )


def google_host(url: str) -> bool:
    return bool(re.search(r"(?:^|\.)google\.[^.]+(?:\.[^.]+)?$", site_host(url)))


async def _google_session(page: Page, email: str) -> bool:
    return bool(email.strip()) and any(
        cookie.get("name") == "SID" and cookie.get("value")
        for cookie in await page.context.cookies("https://mail.google.com")
    )


async def account_page(
    page: Page, *, after_signup: bool = False, email: str = ""
) -> AccountPage | None:
    return await _account_page(
        page, after_signup=after_signup, google=await _google_session(page, email)
    )


async def _google_button(page: Page) -> ElementHandle | None:
    origin = await page.evaluate("location.origin")
    for frame in await _writable_frames(page):
        for control in await frame.locator(
            "button, a, input[type=submit], input[type=button], [role=button], [role=link]"
        ).element_handles():
            if await control.evaluate(
                "(e, origin) => {" + _DOM +
                "return e.isConnected && e.ownerDocument.defaultView.origin === origin && "
                "visible(e) && googleControl(e) && accountContainer(e); }", origin,
            ):
                return control
            await control.dispose()
    for frame in page.frames:
        if frame.url.startswith("https://accounts.google.com/gsi/") and await _visible_frame(frame):
            for control in await frame.get_by_role("button").element_handles():
                if await control.is_visible() and await control.evaluate(_GOOGLE_ORIGIN_JS):
                    return control
                await control.dispose()
    return None


def _basic_google_scope(text: str) -> bool:
    """A listed permission must describe only the four identity attributes."""
    if not _GOOGLE_IDENTITY_SCOPE.search(text):
        return False
    remainder = _GOOGLE_IDENTITY_SCOPE.sub("", text)
    remainder = _GOOGLE_SCOPE_VERB.sub("", remainder)
    return not _GOOGLE_SCOPE_CONNECTORS.sub("", remainder)


async def _google_step(page: Page, email: str, clicked: set[tuple[str, str]]) -> bool:
    """False means human input is required; never enter text on Google's pages."""
    text = ""
    scope_checkboxes = False
    scope_rows: list[str] = []
    for frame in page.frames:
        if await frame.locator(
            'input[type=password], input[autocomplete~="one-time-code" i]'
        ).count():
            return False
        if await frame.locator("iframe[src*=captcha], [class*=captcha], [id*=captcha]").count():
            return False
        text += "\n" + await frame.locator("body").inner_text(timeout=1000)
        scope_checkboxes |= bool(
            await frame.locator('input[type=checkbox], [role=checkbox]').count()
        )
        scope_rows.extend(
            await frame.locator('li, [role=listitem], [data-scope]').all_inner_texts()
        )
    if _GOOGLE_CHALLENGE.search(text):
        return False
    identity = (page.url, text)
    if identity in clicked:
        return True
    chooser = bool(
        re.search(
            r"choose (?:an? )?account|choisir un compte|s[ée]lectionnez un compte|konto auswählen",
            text,
            re.I,
        )
    )
    if not chooser:
        # An account's Gmail address is identity, not a request for mailbox access.
        consent = _EMAIL_IN_TEXT.sub("", text)
        for line in consent.splitlines():
            if match := _GOOGLE_SCOPE_VERB.search(line):
                scope_rows.append(line[match.start():])
        if (
            scope_checkboxes or _GOOGLE_EXTRA_SCOPES.search(consent)
            or any(not _basic_google_scope(row) for row in scope_rows)
        ):
            return False
        for control in await page.get_by_role(
            "button", name=_GOOGLE_CONSENT_WORDS
        ).element_handles():
            try:
                if await control.is_visible():
                    # The page can navigate between any two awaits. Click only this
                    # checked node; a locator could resolve to the site's Continue.
                    if not await control.evaluate(_GOOGLE_ORIGIN_JS):
                        return False
                    await control.click(timeout=1500)
                    clicked.add(identity)
                    return True
            finally:
                await control.dispose()
        return True
    controls = await page.locator(
        "button, a, [role=button], [role=link], [data-identifier], [data-email], li"
    ).element_handles()
    # Innermost controls first; a chooser container with multiple emails is never eligible.
    try:
        for control in reversed(controls):
            if not await control.is_visible():
                continue
            row = await control.inner_text()
            if _GOOGLE_OTHER_ACCOUNT.search(row):
                continue
            emails = _EMAIL_IN_TEXT.findall(row)
            if len(emails) == 1 and emails[0].casefold() == email.strip().casefold():
                if not await control.evaluate(_GOOGLE_ORIGIN_JS):
                    return False
                await control.click(timeout=1500)
                clicked.add(identity)
                return True
        return False
    finally:
        for control in controls:
            await control.dispose()


async def google_sign_in(page: Page, email: str) -> bool:
    """Complete an existing Google session using only exact account and consent clicks."""
    if not account_allowed(page.url) or not await _google_session(page, email):
        return False
    acct = await _account_page(page, google=True)
    if acct is None or not acct.google_button:
        return False
    original_host = site_host(page.url)
    opened: list[Page] = []
    context = page.context

    def track_popup(other: Page) -> None:
        opened.append(other)

    context.on("page", track_popup)
    loop = asyncio.get_running_loop()
    start_deadline = loop.time() + _GOOGLE_START_TIMEOUT_S
    auth: Page | None = None
    clicked: set[tuple[str, str]] = set()
    try:
        async with asyncio.timeout(_GOOGLE_FINISH_TIMEOUT_S):
            button = await _google_button(page)
            if button is None:
                return False
            try:
                await button.click(timeout=1500)
            finally:
                await button.dispose()
            while not page.is_closed():
                if auth is None:
                    candidates = [page] + [
                        other
                        for other in opened
                        if not other.is_closed() and await other.opener() == page
                    ]
                    auth = next(
                        (
                            other
                            for other in candidates
                            if other.url.startswith("https://accounts.google.com/")
                        ),
                        None,
                    )
                    if auth is None and loop.time() >= start_deadline:
                        return False
                try:
                    if auth is not None:
                        if not auth.is_closed() and auth.url.startswith(
                            "https://accounts.google.com/"
                        ):
                            if not await _google_step(auth, email, clicked):
                                return False
                        # Sites may replace their form with a loading panel before OAuth ends.
                        if (
                            (auth.is_closed() or site_host(auth.url) == original_host)
                            and site_host(page.url) == original_host
                            and await page.evaluate(
                                "() => document.readyState !== 'loading' && "
                                "!!document.body?.innerText.trim()"
                            )
                            and await _account_page(page, google=True, strict=True) is None
                        ):
                            return True
                except PlaywrightError:
                    # A chooser/consent navigation may invalidate its former document.
                    pass
                await asyncio.sleep(0.05)
    except (PlaywrightError, TimeoutError):
        return False
    finally:
        context.remove_listener("page", track_popup)
    return False


async def fill_passwords(page: Page, password: str) -> int:
    """Only Playwright fills receive the secret, restricted to visible password inputs."""
    count = 0
    if not account_allowed(page.url):
        return count
    try:
        for frame in await _writable_frames(page):
            for locator in await frame.locator('input[type="password"]:visible').all():
                if not await locator.input_value():
                    await locator.fill(password, timeout=1500)
                    count += 1
    except PlaywrightError:
        # Playwright's fill timeout includes the value. Never retain it in a displayed chain.
        raise AccountError("password field could not be filled") from None
    return count


async def tick_required_terms(page: Page) -> list[str]:
    chosen = []
    if not account_allowed(page.url):
        return chosen
    try:
        origin = await page.evaluate("location.origin")
        for frame in await _writable_frames(page):
            for box in await frame.locator(
                "input[type=checkbox]:visible, [role=checkbox]:visible"
            ).element_handles():
                try:
                    label = await box.evaluate(_LABEL_JS)
                    if label and _TERMS.search(label) and not OPT_IN_WORDS.search(label):
                        if await box.evaluate(
                            "(e, [expected, origin]) => {" + _DOM +
                            "return e.isConnected && "
                            "e.ownerDocument.defaultView.origin === origin && visible(e) && "
                            "e.matches('input[type=checkbox], [role=checkbox]') && "
                            "!e.checked && e.getAttribute('aria-checked') !== 'true' && "
                            "label(e) === expected && accountContainer(e); }", [label, origin],
                        ):
                            await box.check(timeout=1500)
                            chosen.append(label)
                finally:
                    await box.dispose()
    except PlaywrightError:
        raise AccountError("account terms could not be checked") from None
    return chosen


async def opt_in_click(snap: dict, action: dict) -> bool:
    """An aria name must not hide an associated newsletter/marketing label."""
    frame = snap["_frame_refs"][action["frame"]]
    label = await frame.evaluate(
        "node => { const e = window.__jobpilotHarness?.nodes.get(node); "
        f"return e?.isConnected ? ({_LABEL_JS})(e) : ''; }}",
        action["node"],
    )
    return bool(OPT_IN_WORDS.search(label)) or (
        action.get("role") == "checkbox" and not label.strip()
    )


async def account_control(snap: dict, action: dict, *, code_only: bool = False) -> bool:
    """Validate the clicked control's actual form, including external form elements."""
    frame = snap["_frame_refs"][action["frame"]]
    return await frame.evaluate(
        "([node, allowCode]) => { const e = window.__jobpilotHarness?.nodes.get(node); "
        f"return !!e?.isConnected && ({_ACCOUNT_CONTROL_JS})(e, allowCode); }}",
        [action["node"], code_only],
    )


async def google_click(snap: dict, action: dict) -> bool:
    """Recognize the same control after failure so the model cannot reopen OAuth."""
    frame = snap["_frame_refs"][action["frame"]]
    if frame.url.startswith("https://accounts.google.com/gsi/"):
        return True
    return await frame.evaluate(
        "node => { const e = window.__jobpilotHarness?.nodes.get(node); "
        f"return !!e?.isConnected && ({_GOOGLE_CONTROL_JS})(e); }}",
        action["node"],
    )


async def _page_text(page: Page) -> str:
    return "\n".join(
        [await frame.locator("body").inner_text() for frame in await _writable_frames(page)]
    )


@dataclass
class AccountFlow:
    password: str | None = field(repr=False)
    email: str
    company: str
    registry: Any = field(default=accounts, repr=False)
    mailbox: Any = field(default=mailbox, repr=False)
    key: str = field(default="", init=False)
    login_url: str = field(default="", init=False)
    mode: str = field(default="", init=False)
    switched: bool = field(default=False, init=False)
    pending: bool = field(default=False, init=False)
    attempted: bool = field(default=False, init=False)
    signup_since: datetime | None = field(default=None, init=False)
    verification_read: bool = field(default=False, init=False)
    verified: bool = field(default=False, init=False)
    failed: bool = field(default=False, init=False)
    application_started: bool = field(default=False, init=False)
    application_seen: bool = field(default=False, init=False)
    google_attempted: bool = field(default=False, init=False)
    google_failed: bool = field(default=False, init=False)
    before_text: str = field(default="", init=False, repr=False)
    form_switches: set[tuple[str, str, str]] = field(default_factory=set, init=False, repr=False)

    def clean(self, value: Any) -> Any:
        """Sanitize outward data too, in case a site echoes a password outside its input."""
        if isinstance(value, str):
            if not self.password:
                return value
            # GET forms may encode even unreserved characters and may use legacy charsets.
            # Decode only when a secret is present, preserving unrelated outward data.
            for encoding in ("utf-8", "latin-1", "cp1252"):
                # Chromium uses decimal HTML entities for characters a legacy
                # form charset cannot represent, then percent-encodes the entities.
                for decoded in (value, unquote_plus(value, encoding=encoding)):
                    for candidate in (decoded, html.unescape(decoded)):
                        if self.password in candidate:
                            value = candidate.replace(self.password, "[redacted]")
            for encoded in {
                quote(self.password),
                quote(self.password, safe=""),
                quote_plus(self.password),
                quote_plus(self.password).replace("~", "%7E"),
            }:
                pattern = "".join(
                    "%" + "".join(f"[{c.lower()}{c.upper()}]" for c in part[1:])
                    if re.fullmatch(r"%[0-9A-Fa-f]{2}", part)
                    else re.escape(part)
                    for part in re.split(r"(%[0-9A-Fa-f]{2})", encoded)
                )
                value = re.sub(pattern, "[redacted]", value)
            return value.replace(self.password, "[redacted]")
        if isinstance(value, dict):
            return {self.clean(k): self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.clean(v) for v in value)
        return value

    async def record(self, status: str) -> None:
        await self.registry.record_account(
            self.key,
            method="password",
            email=self.email,
            status=status,
            login_url=self.clean(self.login_url),
        )

    async def _cap(self) -> None:
        if (
            self.mode == "signup"
            and self.signup_since is None
            and await self.registry.new_accounts_today() >= MAX_NEW_ACCOUNTS_PER_DAY
        ):
            raise AccountHandoff("Daily limit of 10 new accounts reached")

    async def _switch_form(self, page: Page, acct: AccountPage) -> bool:
        controls: list[tuple[ElementHandle, str, bool]] = []
        try:
            return await self._switch_form_handles(page, acct, controls)
        finally:
            for control, _, _ in controls:
                await control.dispose()

    async def _switch_form_handles(
        self, page: Page, acct: AccountPage, controls: list[tuple[ElementHandle, str, bool]]
    ) -> bool:
        origin = await page.evaluate("location.origin")
        new_password = False
        for frame in await _writable_frames(page):
            new_password |= bool(
                await frame.locator("input[autocomplete=new-password]:visible").count()
            )
            for control in await frame.locator(
                "a:visible, button:visible, input[type=submit]:visible, [role=link]:visible"
            ).element_handles():
                # Accessible name first, then visible text; avoid duplicate combined names.
                state = await control.evaluate(_SWITCH_STATE_JS)
                controls.append((control, state["label"], state["submits"]))
        signup = (
            new_password
            or acct.password_inputs > 1
            or any(submits and _SIGNUP_ONLY.fullmatch(label) for _, label, submits in controls)
        )
        desired = _SIGNUP_ONLY if self.mode == "signup" else _SIGNIN_ONLY
        if signup == (self.mode == "signup"):
            return False
        for control, label, submits in controls:
            if not submits and desired.fullmatch(label):
                identity = (page.url, self.mode, label)
                if identity in self.form_switches:
                    raise AccountHandoff("Account mode could not be switched")
                if not await control.evaluate(
                    "(e, [expected, allowCode, origin]) => {" + _DOM +
                    f"const state = ({_SWITCH_STATE_JS})(e); "
                    "return e.isConnected && e.ownerDocument.defaultView.origin === origin && "
                    "visible(e) && !state.submits && "
                    "state.label === expected && accountContainer(e, allowCode); }",
                    [label, acct.code_only, origin],
                ):
                    continue
                self.form_switches.add(identity)
                await control.click(timeout=1500)
                await page.wait_for_load_state("domcontentloaded")
                return True
        if self.switched:
            self.failed = True
            await self.record("failed")
            raise AccountHandoff("Account mode could not be switched")
        if self.mode == "signup" and any(
            submits and _SIGNIN_ONLY.fullmatch(label) for _, label, submits in controls
        ):
            raise AccountHandoff("No sign-up control is available")
        return False

    async def detect(self, page: Page) -> AccountPage | None:
        return (
            None
            if self.application_started or self.application_seen
            else await _account_page(
                page,
                after_signup=self.signup_since is not None,
                google=self.google_failed or await _google_session(page, self.email),
            )
        )

    async def observe_application(self, snap: dict) -> None:
        """Latch observed application fields, including restored values and hidden uploads.

        Called only after observing a page that did not qualify as an account page.
        Keep this separate from application_started, which records actual writes.
        """
        if self.application_seen:
            return
        for index, frame in enumerate(snap["_frame_refs"]):
            nodes = [
                action["node"] for action in snap["actions"]
                if action["kind"] in {"fill", "select"} and action["frame"] == index
            ]
            if await frame.evaluate(_APPLICATION_SEEN_JS, nodes):
                self.application_seen = True
                return

    async def prepare(self, page: Page) -> tuple[AccountPage | None, list[str], bool]:
        """Return account detection, chosen terms, and whether to observe a new page."""
        if google_host(page.url):
            raise AccountHandoff("Use your existing browser sign-in")
        acct = await self.detect(page)
        if (acct is not None or self.attempted) and not account_allowed(page.url):
            raise AccountHandoff("Use your existing browser sign-in")
        if self.pending:
            self.pending = False
            lines = self.clean(await _page_text(page)).splitlines()
            diff = SequenceMatcher(a=self.before_text.splitlines(), b=lines, autojunk=False)
            new_text = "\n".join(
                line
                for op, _, _, start, end in diff.get_opcodes()
                if op in {"insert", "replace"}
                for line in lines[start:end]
            )
            if _ERROR.search(new_text):
                if self.switched or self.verification_read or acct is None:
                    self.failed = True
                    await self.record("failed")
                    raise AccountHandoff("Account sign-in failed")
                self.mode = "signup" if self.mode == "signin" else "signin"
                self.switched = True
                self.signup_since = None
        if acct is None:
            if self.attempted and not self.failed:
                await self.record(
                    "verified"
                    if self.verified
                    else "signed_in"
                    if self.mode == "signin"
                    else "created"
                )
                self.attempted = self.pending = False
                self.signup_since = None
            return None, [], False
        if (
            acct.google_button
            and not self.google_attempted
            and await _google_session(page, self.email)
        ):
            try:
                self.key = self.registry.site_key(page.url)
            except ValueError:
                raise AccountHandoff("The account site could not be identified") from None
            self.login_url = page.url
            self.mode = "signin" if await self.registry.find_account(self.key) else "signup"
            await self._cap()
            self.google_attempted = True
            if await google_sign_in(page, self.email):
                await self.registry.record_account(
                    self.key,
                    method="google",
                    email=self.email,
                    status="signed_in",
                    login_url=self.clean(self.login_url),
                )
                self.attempted = self.pending = False
                self.signup_since = None
                return None, [], True
            self.google_failed = True
            if acct.has_password and site_host(page.url) != site_host(self.login_url):
                await page.goto(self.login_url, wait_until="domcontentloaded")
            acct = (
                await self.detect(page)
                if site_host(page.url) == site_host(self.login_url)
                else None
            )
            if acct is None or not acct.has_password:
                raise AccountHandoff("Complete sign-in in the open tab")
        if (
            not self.password
            or not self.email
            or (not acct.has_password and self.signup_since is None)
        ):
            raise AccountHandoff("Complete sign-in in the open tab")
        try:
            key = self.registry.site_key(page.url)
        except ValueError:
            raise AccountHandoff("The account site could not be identified") from None
        if not self.key:
            self.key, self.login_url = key, page.url
            self.mode = "signin" if await self.registry.find_account(key) else "signup"
        elif site_host(page.url) != site_host(self.login_url):
            raise AccountHandoff("The account site changed")
        if self.failed:
            raise AccountHandoff("Account sign-in failed")
        await self._cap()
        if self.signup_since is not None and (acct.code_field or acct.check_email_text):
            if not self.verification_read:
                self.verification_read = True
                await self.record("created")
                verification = await self.mailbox.wait_for_verification(
                    page.context,
                    since=self.signup_since,
                    site_host=site_host(self.login_url),
                    company=self.company,
                )
                if verification is None:
                    raise AccountHandoff("Check your email to finish signing up")
                if verification.code and acct.code_field:
                    for frame in await _writable_frames(page):
                        for control in await frame.locator("input:visible").all():
                            if await control.evaluate(_CODE_JS):
                                await control.fill(verification.code, timeout=1500)
                                self.verified = True
                                return acct, [], True
                elif verification.link:
                    verification_page = await page.context.new_page()
                    try:
                        await verification_page.goto(
                            verification.link, wait_until="domcontentloaded"
                        )
                    finally:
                        await verification_page.close()
                        await page.bring_to_front()
                    await page.reload(wait_until="domcontentloaded")
                    self.verified = True
                    return acct, [], True
                raise AccountHandoff("Check your email to finish signing up")
            return acct, [], False
        if await self._switch_form(page, acct):
            return acct, [], True
        await fill_passwords(page, self.password)
        terms = await tick_required_terms(page) if self.mode == "signup" else []
        return acct, self.clean(terms), False

    async def before_click(self, page: Page, action: dict, acct: AccountPage) -> None:
        if action.get("role") not in {"button", "link"}:
            return
        labels = [action.get("full_label", "").strip(), action.get("full_text", "").strip()]
        if not any(
            SIGNIN_WORDS.fullmatch(s)
            or SIGNUP_WORDS.fullmatch(s)
            or (acct.code_only and VERIFY_WORDS.fullmatch(s))
            for s in labels
        ):
            return
        await self._cap()
        self.before_text = self.clean(await _page_text(page))
        self.pending = self.attempted = True
        if self.mode == "signup" and self.signup_since is None:
            self.signup_since = datetime.now(UTC)
