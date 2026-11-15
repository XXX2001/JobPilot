"""Code-owned account preparation; no secret enters the decision model."""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page

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
        if (e.matches('input[type=password], input[type=email], ' +
                      'input[type=checkbox], [role=checkbox]'))
            return true;
        if (!e.matches('input:not([type]), input[type=text], input[type=tel], ' +
                       'select, [role=combobox]'))
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
        '[role=textbox], [role=combobox], [role=radio], [role=switch], ' +
        '[role=slider], [role=spinbutton]')
        .filter(e => !e.matches('input[type=submit], input[type=button], input[type=reset]'));
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
    return {
        has_password: passwords.length > 0, password_inputs: passwords.length,
        google_button: controls.some(googleControl) ||
            query(document, 'iframe').some(e => visible(e) &&
                /^https:\/\/accounts\.google\.com\/gsi\//i.test(e.src)),
        signup_link: controls.map(label).some(t =>
            /cr[ée]er un compte|s['’]inscrire|create (an )?account/i.test(t) ||
            /sign.?up|register|registrieren/i.test(t)),
        code_field: query(document, 'input').some(code),
        check_email_text: [
            /check (your )?(e-?mail|inbox)/i,
            /v[ée]rifiez (votre |vos )?(e-?mail|messagerie)/i,
            /consultez (votre |vos )?(e-?mail|messagerie)/i,
            /prüfen sie.*(e-mail|postfach)/i
        ].some(pattern => pattern.test(document.body?.innerText || '')),
        input_count: fields(document).length,
        code_count: query(document, 'input').filter(code).length,
        application: query(document, 'input[type=file], textarea, [contenteditable]').length > 0 ||
            fields(document).some(e => !accountField(e) && !code(e))
    };
}"""
)
_LABEL_JS = "e => {" + _DOM + "return label(e); }"
_CODE_JS = "e => {" + _DOM + "return code(e); }"
_ACCOUNT_FIELD_JS = "e => {" + _DOM + "return accountField(e); }"
_GOOGLE_CONTROL_JS = "e => {" + _DOM + "return googleControl(e); }"


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


async def _google_button(page: Page) -> Locator | None:
    for frame in await _writable_frames(page):
        for control in await frame.locator(
            "button, a, input[type=submit], input[type=button], [role=button], [role=link]"
        ).all():
            if await control.is_visible() and await control.evaluate(_GOOGLE_CONTROL_JS):
                return control
    for frame in page.frames:
        if frame.url.startswith("https://accounts.google.com/gsi/") and await _visible_frame(frame):
            for control in await frame.get_by_role("button").all():
                if await control.is_visible():
                    return control
    return None


async def _google_step(page: Page, email: str, clicked: set[tuple[str, str]]) -> bool:
    """False means human input is required; never enter text on Google's pages."""
    text = ""
    for frame in page.frames:
        if await frame.locator(
            'input[type=password], input[autocomplete~="one-time-code" i]'
        ).count():
            return False
        if await frame.locator("iframe[src*=captcha], [class*=captcha], [id*=captcha]").count():
            return False
        text += await frame.locator("body").inner_text(timeout=1000)
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
        for control in await page.get_by_role("button", name=_GOOGLE_CONSENT_WORDS).all():
            if await control.is_visible():
                await control.click(timeout=1500)
                clicked.add(identity)
                return True
        return True
    controls = await page.locator(
        "button, a, [role=button], [role=link], [data-identifier], [data-email], li"
    ).all()
    # Innermost controls first; a chooser container with multiple emails is never eligible.
    for control in reversed(controls):
        if not await control.is_visible():
            continue
        emails = _EMAIL_IN_TEXT.findall(await control.inner_text())
        if len(emails) == 1 and emails[0].casefold() == email.strip().casefold():
            await control.click(timeout=1500)
            clicked.add(identity)
            return True
    return False


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
            await button.click(timeout=1500)
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
        for frame in await _writable_frames(page):
            for box in await frame.locator(
                "input[type=checkbox]:visible, [role=checkbox]:visible"
            ).all():
                label = await box.evaluate(_LABEL_JS)
                if (
                    label
                    and _TERMS.search(label)
                    and not OPT_IN_WORDS.search(label)
                    and not await box.is_checked()
                ):
                    await box.check(timeout=1500)
                    chosen.append(label)
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


async def account_field(snap: dict, action: dict) -> bool:
    frame = snap["_frame_refs"][action["frame"]]
    return await frame.evaluate(
        "node => { const e = window.__jobpilotHarness?.nodes.get(node); "
        f"return !!e?.isConnected && ({_ACCOUNT_FIELD_JS})(e); }}",
        action["node"],
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
    google_attempted: bool = field(default=False, init=False)
    google_failed: bool = field(default=False, init=False)
    before_text: str = field(default="", init=False, repr=False)
    form_switches: set[tuple[str, str, str]] = field(default_factory=set, init=False, repr=False)

    def clean(self, value: Any) -> Any:
        """Sanitize outward data too, in case a site echoes a password outside its input."""
        if isinstance(value, str):
            if not self.password:
                return value
            for encoded in {
                quote(self.password),
                quote(self.password, safe=""),
                quote_plus(self.password),
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
        controls: list[tuple[Locator, str, bool]] = []
        new_password = False
        for frame in await _writable_frames(page):
            new_password |= bool(
                await frame.locator("input[autocomplete=new-password]:visible").count()
            )
            for control in await frame.locator(
                "a:visible, button:visible, input[type=submit]:visible, [role=link]:visible"
            ).all():
                # Accessible name first, then visible text; avoid duplicate combined names.
                label = await control.get_attribute("aria-label") or await control.inner_text()
                if not label:
                    label = await control.get_attribute("value") or ""
                submits = await control.evaluate("e => !!e.form && e.type === 'submit'")
                controls.append((control, label.strip(), submits))
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
            if self.application_started
            else await _account_page(
                page,
                after_signup=self.signup_since is not None,
                google=self.google_failed or await _google_session(page, self.email),
            )
        )

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
                await account_page(page)
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
