"""Observe, choose, and execute bounded application steps up to owner review.

Adapted from browser-use/jev-ultrafast, jev_ultrafast/agent.py (MIT License).
https://github.com/browser-use/jev-ultrafast
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, fields
from pathlib import Path

from playwright.async_api import Page

from backend.applier.captcha_handler import check_and_handle_captcha

from .account_flow import (
    OPT_IN_WORDS,
    SIGNIN_WORDS,
    SIGNUP_WORDS,
    VERIFY_WORDS,
    AccountError,
    AccountFlow,
    AccountPage,
    account_allowed,
    account_control,
    google_click,
    google_host,
    opt_in_click,
    site_host,
)
from .decide import DecisionError, RequestTooLong, classify_page, decide, pick_slot
from .execute import StalePage, act, attach
from .snapshot import ADVANCE_PATTERN, SUBMIT_PATTERN, fresh, observe

_POLL_SECONDS = 5
_MAX_REQUESTS = 120
MAX_COMPLETION_SCROLLS = 20
HANDOFF_TIMEOUT_S = 600
_STRICT_SUBMIT = re.compile(
    r"submit|send|envoyer|soumettre|finish|complete|terminer|bewerben|absenden|"
    r"einreichen|abschicken|postule|postulez|candidater|valider|déposer|transmettre|confirm|"
    r"enviar|invia|verzenden|solliciteren",
    re.IGNORECASE,
)
_UPLOAD = re.compile(
    r"\b(?:attach|upload|resume|résumé|cv|curriculum|cover|lettre|motivation)\b", re.I
)
_LETTER = re.compile(r"\b(?:cover|lettre|motivation)\b", re.I)
_CV = re.compile(r"\b(?:resume|résumé|cv|curriculum)\b", re.I)
_CONSENT = re.compile(
    r"^(?:accept(?: cookies)?|agree|got it|close|dismiss|accepter|j'accepte|fermer|"
    r"tout accepter|alle akzeptieren|schließen)$",
    re.I,
)
_NON_SUBMIT_ROLES = {
    "checkbox",
    "radio",
    "option",
    "combobox",
    "switch",
    "tab",
    "menuitem",
    "menuitemradio",
    "gridcell",
}
_CHOICE_ROLES = {"radio", "checkbox", "switch"}
_SEARCH_FIELD = re.compile(
    r"\b(?:search|rechercher?|recherche par|suche|suchen|keywords?|mot-clé|mots-clés)\b", re.I
)
_UPLOAD_TARGET = """e => ({
    label: window.__jobpilotHarness?.guard(e, true)?.[2] || '',
    name: e.name || '', input_id: e.id || '', accept: e.accept || '',
    nearby_text: e.parentElement?.innerText || ''
})"""


class HarnessBlocked(RuntimeError):
    """The application needs owner input or another application strategy."""

    def __init__(self, message, *, result=None):
        super().__init__(message)
        self.result = result


class HarnessStuck(HarnessBlocked):
    """The harness exhausted a progress, action, request, or time limit."""


def profile_slots(
    full_name: str,
    email: str,
    phone: str,
    location: str,
    linkedin_url: str,
    extra: dict[str, str],
) -> dict[str, tuple[str, str]]:
    names = full_name.split()
    values = {
        "first_name": names[0] if names else "",
        "last_name": " ".join(names[1:]),
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "location": location,
        "linkedin_url": linkedin_url,
    }
    slots = {
        key: (f"{key.replace('_', ' ')}: {value}", value)
        for key, value in values.items()
        if value.strip()
    }
    # Separate IDs preserve additional answers even when a key names a standard slot.
    slots.update(
        {
            f"extra_{index}": (f"{key}: {value}", value)
            for index, (key, value) in enumerate(extra.items())
            if value.strip()
        }
    )
    slots["needs_written_answer"] = ("Write an answer using only the stored CV and profile.", "")
    slots["leave_empty"] = ("No stored value answers this field; leave it for the owner.", "")
    return slots


def build_goal(
    job_title: str,
    company: str,
    full_name: str,
    email: str,
    phone: str,
    location: str,
    linkedin_url: str,
) -> str:
    linkedin = f", LinkedIn {linkedin_url}" if linkedin_url else ""
    return (
        f"Fill in this job application for {job_title} at {company} as {full_name}, "
        f"email {email}, phone {phone}, location {location}{linkedin}. "
        "Attach the CV when asked. Do not submit the application: "
        "when every field you can fill is filled, choose DONE."
    )


@dataclass
class HarnessResult:
    stop: str
    submit_action: dict | None
    typed: dict[str, dict]
    needs_you: list[str]
    written: list[str]
    uploads: list[str]
    steps: int
    clicked_submit: bool = False
    clicked: int = 0
    # Answers the model picked by clicking (dropdown options, radios, checkboxes): shown to the
    # owner for review, since they can be claims ("Right to work: Yes").
    chosen: list[str] = field(default_factory=list)
    login_host: str = ""
    login_note: str = ""


class _LimitedClient:
    def __init__(self, client, clean=lambda value: value):
        self.client = client
        self.model = client.model
        self.requests = 0
        self.clean = clean

    async def post(self, body):
        if self.requests >= _MAX_REQUESTS:
            raise HarnessStuck("Reached the model request limit")
        self.requests += 1
        return await self.client.post(self.clean(body))


def _identity(snap, action):
    index = action["frame"]
    # Node IDs restart on navigation; a new document must get new fill opportunities.
    return (snap["_frame_refs"][index], snap["frames"][index]["page_key"][0], action["node"])


def _document(target, documents, *, default=False, single_upload=False):
    own = " ".join(
        target.get(key, "") for key in ("full_label", "label", "name", "input_id")
    ).strip()
    # Treat identifier separators as word boundaries (e.g. cover_letter).
    hint = (own or target.get("nearby_text", "").strip()).replace("_", " ")
    letter, cv = bool(_LETTER.search(hint)), bool(_CV.search(hint))
    if letter and cv:
        return None  # Shared nearby text does not identify either input.
    if letter:
        kind = "letter"
    elif (
        cv
        or default
        or (
            single_upload
            and not hint
            and re.search(r"pdf|word|\.doc", target.get("accept", ""), re.I)
        )
    ):
        kind = default if isinstance(default, str) and not cv else "cv"
    else:
        return None
    return kind if documents.get(kind) else None


def _append_once(items, value):
    if value not in items:
        items.append(value)


def _click_labels(action):
    return (action["full_label"].strip(), action.get("full_text", "").strip())


def _matches(action, pattern):
    return any(pattern.search(label) for label in _click_labels(action))


def _advance_only(action):
    labels = [label for label in _click_labels(action) if label]
    return bool(labels) and all(ADVANCE_PATTERN.fullmatch(label) for label in labels)


@dataclass
class _Run:
    page: Page
    goal: str
    slots: dict
    documents: dict[str, Path]
    writer: Callable[[dict], Awaitable[str | None]] | None
    client: _LimitedClient
    job_id: int | None
    cancel_event: asyncio.Event | None
    max_actions: int
    notify: Callable[[str, str], Awaitable[None]] | None
    account_flow: AccountFlow
    account: AccountPage | None = None
    result: HarnessResult = field(
        default_factory=lambda: HarnessResult("ready", None, {}, [], [], [], 0)
    )
    history: list = field(default_factory=list)
    excluded: set = field(default_factory=set)
    skipped_clicks: set = field(default_factory=set)
    completion_scrolls: int = 0
    attached: set = field(default_factory=set)
    choosers: list = field(default_factory=list)
    clicked_toggles: set = field(default_factory=set)
    chose_field: bool = False
    chooser_document: str | None = "cv"
    timeout: asyncio.Timeout | None = None

    def check_limits(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise HarnessBlocked("Application cancelled")
        if self.result.steps >= self.max_actions:
            raise HarnessStuck("Reached the action limit")

    async def require_fresh(self, snap, action=None):
        if not await fresh(self.page, snap, action):
            raise StalePage("Page changed since the decision. Observe again.")

    def filechooser(self, chooser):
        self.choosers.append(
            asyncio.create_task(self.answer_chooser(chooser, self.chooser_document))
        )

    async def answer_chooser(self, chooser, opening_document):
        target = await chooser.element.evaluate(_UPLOAD_TARGET)
        # The opening control is a better fallback than a shared parent's text.
        kind = _document({**target, "nearby_text": ""}, self.documents, default=opening_document)
        if await self.account_flow.detect(self.page) is None:
            self.account_flow.application_started = True
        await chooser.set_files(self.documents[kind] if kind else [], timeout=1000)
        if kind is None:
            raise HarnessBlocked("No document available for the file chooser")
        _append_once(self.result.uploads, kind)

    async def drain_choosers(self):
        if self.choosers:
            pending, self.choosers = self.choosers, []
            await asyncio.gather(*pending)

    async def pending_uploads(self, snap):
        pending = []
        for upload in snap["uploads"]:
            if upload["filled"] or _identity(snap, upload) in self.attached:
                continue
            frame = snap["_frame_refs"][upload["frame"]]
            # Snapshot upload labels may include a shared parent's text. Read the
            # input's own accessible name before considering any nearby fallback.
            target = await frame.evaluate(
                f"node => {{ const e = window.__jobpilotHarness?.nodes.get(node); "
                f"return e?.isConnected ? ({_UPLOAD_TARGET})(e) : null; }}",
                upload["node"],
            )
            if target is None:
                raise StalePage("Upload changed since observation")
            kind = _document(target, self.documents, single_upload=len(snap["uploads"]) == 1)
            if kind:
                pending.append((upload, kind))
        return pending

    async def upload_documents(self, snap):
        changed = False
        while True:
            pending = await self.pending_uploads(snap)
            if not pending:
                return changed
            candidate, kind = pending[0]
            self.check_limits()
            if await self.account_flow.detect(self.page) is None:
                self.account_flow.application_started = True
            await attach(self.page, snap, candidate, self.documents[kind])
            self.attached.add(_identity(snap, candidate))
            _append_once(self.result.uploads, kind)
            self.result.steps += 1
            self.history.append(
                {"action": candidate["label"], "kind": "upload", "page_changed": True}
            )
            changed = True
            snap = await observe(self.page)

    async def handoff(self, kind, note=""):
        host = self.account_flow.clean(site_host(self.account_flow.login_url or self.page.url))
        if kind == "login_wall":
            self.result.login_host = host
            self.result.login_note = self.account_flow.clean(note)
        loop = asyncio.get_running_loop()
        deadline = self.timeout.when() if self.timeout is not None else None
        remaining = deadline - loop.time() if deadline is not None else None
        if self.timeout is not None:
            self.timeout.reschedule(None)
        try:
            async with asyncio.timeout(HANDOFF_TIMEOUT_S):
                if kind == "captcha" and await check_and_handle_captcha(
                    self.page, job_id=self.job_id, cancel_event=self.cancel_event
                ):
                    return
                if self.notify is not None:
                    await self.notify(
                        "login" if kind == "login_wall" else "captcha",
                        f"{'Sign-in needed' if kind == 'login_wall' else 'Captcha'} on {host}"
                        + (f": {self.account_flow.clean(note)}" if note else ""),
                    )
                while True:
                    self.check_limits()
                    await asyncio.sleep(_POLL_SECONDS)
                    if google_host(self.page.url):
                        continue
                    if (
                        kind == "login_wall"
                        and await self.account_flow.detect(self.page) is not None
                    ):
                        continue
                    snap = await observe(self.page)
                    # Owner time and page-kind-only polls are outside run budgets.
                    current, p = await classify_page(
                        self.client.client, self.account_flow.clean(snap["text"])
                    )
                    if current != kind or p < 0.6:
                        self.result.login_host = self.result.login_note = ""
                        return
        except TimeoutError as exc:
            raise HarnessBlocked("Owner hand-off timed out") from exc
        finally:
            if self.timeout is not None:
                self.timeout.reschedule(loop.time() + remaining if remaining is not None else None)

    def form_context(self, snap, decision):
        return bool(
            (decision.page_kind == "application_form" and decision.page_kind_p >= 0.5)
            or self.result.typed
            or self.result.uploads
            or self.chose_field
            or snap["uploads"]
            or any(
                a["kind"] in {"fill", "select"}
                or (a["kind"] == "click" and a.get("role") in _CHOICE_ROLES)
                for a in snap["actions"]
            )
        )

    async def form_complete(self, snap):
        return not any(
            a["kind"] == "fill" and _identity(snap, a) not in self.excluded for a in snap["actions"]
        ) and not await self.pending_uploads(snap)

    async def scroll_for_more(self, snap) -> bool:
        """Only on-screen fields are observed, so a form is never complete while it can scroll.

        Live Greenhouse: the model chose DONE with the screening questions still below the fold.
        """
        if self.completion_scrolls >= MAX_COMPLETION_SCROLLS:
            return False
        scroll = next(
            (a for a in snap["actions"] if a["kind"] == "scroll" and a.get("delta", 0) > 0), None
        )
        if scroll is None:
            return False
        await act(self.page, snap, scroll)
        self.completion_scrolls += 1
        return True

    def click_stop(self, snap, decision):
        action = decision.action
        label = action["full_label"].strip()
        if self.account is not None:
            # Check both the accessible name and the full visible text. Labels with
            # application verbs cannot borrow an account control's allowed name.
            labels = [label for label in _click_labels(action) if label]
            if any(OPT_IN_WORDS.search(label) for label in labels):
                return "skip"
            if action.get("role") in {"checkbox", "switch"} and action.get("checked") == "true":
                return "skip"
            if labels and all(
                SIGNIN_WORDS.fullmatch(s) or SIGNUP_WORDS.fullmatch(s) for s in labels
            ):
                return None
            # Verification verbs are safe only with one code input on the whole page.
            if self.account.code_only and labels and all(
                VERIFY_WORDS.fullmatch(s) for s in labels
            ):
                return None
        on_form = self.form_context(snap, decision)
        identity = _identity(snap, action)
        strict = _matches(action, _STRICT_SUBMIT)
        if on_form and not _advance_only(action):
            # Broad application verbs cannot hide behind an additional strict word
            # ("Apply and confirm"). Strict-only field names use the identity below.
            if any(
                not _STRICT_SUBMIT.fullmatch(match.group())
                for label in _click_labels(action)
                for match in SUBMIT_PATTERN.finditer(label)
            ):
                return "submit_point" if strict else "ready"
        # Open-field actions share their node with a fill action. Check these
        # before strict words: opening "Confirm email" does not submit a form.
        if any(a["kind"] == "fill" and _identity(snap, a) == identity for a in snap["actions"]):
            return "skip" if identity in self.excluded or action.get("value") else None
        if strict:
            return "submit_point"
        if not on_form:
            return None
        if not label.strip() and action.get("role") == "button":
            return "skip"
        if action.get("role") in {"checkbox", "switch"} and identity in self.clicked_toggles:
            return "skip"
        if (
            _advance_only(action)
            or action.get("role") in _NON_SUBMIT_ROLES
            or _UPLOAD.search(label)
            or _CONSENT.fullmatch(label)
        ):
            return None
        # Default-deny: an unknown control on a form is never clicked. While fields remain it
        # is skipped (live Greenhouse: "Enter manually" ended the run with half the form empty).
        return "skip"

    def ready(self, snap, decision):
        self.result.stop = "ready"
        self.result.submit_action = self.submit_for_done(snap, decision)
        return self.result

    def submit_for_done(self, snap, decision):
        chosen = decision.submit_action
        # submit_target always names some click; trust it only for a submitting control.
        if (
            chosen is not None
            and decision.submit_p >= 0.3
            and (chosen.get("submits") or _matches(chosen, SUBMIT_PATTERN))
        ):
            return chosen
        candidates = [
            action
            for action in snap["actions"]
            if action["kind"] == "click"
            and action.get("submits")
            and _matches(action, SUBMIT_PATTERN)
            and not _advance_only(action)
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def fill(self, snap, action):
        executed = False
        criteria = {key: description for key, (description, _) in self.slots.items()}
        slot, p = await pick_slot(self.client, action, snap["title"], criteria)
        value = self.slots.get(slot, ("", ""))[1] if p >= 0.5 else ""
        source = slot
        if slot == "needs_written_answer" and p >= 0.5 and self.writer is not None:
            frame_text = snap["frames"][action["frame"]]["text"]
            start = max(0, frame_text.find(action["label"]) - 500)
            value = await self.writer(
                {
                    "label": self.account_flow.clean(action["label"]),
                    "page_text": self.account_flow.clean(frame_text[start : start + 1500]),
                    "page_title": self.account_flow.clean(snap["title"]),
                    "goal": self.goal,
                    "job_id": self.job_id,
                    "profile": {key: value for key, (_, value) in self.slots.items() if value},
                }
            )
            value = (value or "").strip()[:1200]
            source = "written"
        await self.require_fresh(snap)
        if not value:
            _append_once(self.result.needs_you, action["label"])
        else:
            if action.get("current_value", action.get("value", "")) != value:
                if await self.account_flow.detect(self.page) is None:
                    self.account_flow.application_started = True
                await act(self.page, snap, action, value)
                executed = True
            self.result.typed[f"{action['frame']}:{action['node']}"] = {
                "label": action["label"],
                "value": value,
                "source": source,
            }
            if source == "written":
                _append_once(self.result.written, action["label"])
        self.excluded.add(_identity(snap, action))
        return value or None, executed

    async def run(self):
        stale, unchanged = 0, 0
        previous = None
        last_decision = None
        while True:
            self.check_limits()
            try:
                if self.result.uploads:
                    self.account_flow.application_started = True
                self.account, terms, changed = await self.account_flow.prepare(self.page)
                self.result.login_host = (
                    self.account_flow.clean(site_host(self.page.url)) if self.account else ""
                )
                if not self.account:
                    self.result.login_note = ""
                for term in terms:
                    _append_once(self.result.chosen, term)
                if changed:
                    self.result.steps += 1
                    stale, unchanged = 0, 0
                    previous = None
                    continue
                snap = await observe(self.page)
                # A site's job search box is never part of the application (live: FANUC's
                # "Rechercher par lieu" got the owner's city).
                self.excluded.update(
                    _identity(snap, a)
                    for a in snap["actions"]
                    if a["kind"] == "fill"
                    and (a.get("role") == "searchbox" or _SEARCH_FIELD.search(a.get("label", "")))
                )
                if previous is not None:
                    changed = snap["fingerprint"] != previous
                    self.history[-1]["page_changed"] = changed
                    if not changed and self.history[-1].get("click_id") is not None:
                        # An "Open <field>" click that changed nothing is not offered again (live
                        # OpenJev repeated "Open City" on a combobox that opens only on typing);
                        # other no-op clicks still count toward the stuck limit.
                        self.skipped_clicks.add(self.history[-1]["click_id"])
                    unchanged = (
                        unchanged + 1
                        if not changed
                        and self.history[-1]["kind"] != "wait"
                        and not self.history[-1].get("skipped")
                        else 0
                    )
                    previous = None
                    if unchanged >= 3:
                        if (
                            not self.account
                            and self.form_context(snap, last_decision)
                            and await self.form_complete(snap)
                        ):
                            if await self.scroll_for_more(snap):
                                unchanged = 0
                                continue
                            await self.require_fresh(snap)
                            return self.ready(snap, last_decision)
                        raise HarnessStuck("Three unchanged steps made no progress")
                if self.account_flow.google_failed:
                    for candidate in snap["actions"]:
                        if candidate["kind"] == "click" and await google_click(snap, candidate):
                            self.skipped_clicks.add(_identity(snap, candidate))
                offered = {
                    **snap,
                    "actions": [
                        action
                        for action in snap["actions"]
                        if not (
                            action["kind"] == "fill" and _identity(snap, action) in self.excluded
                        )
                        and not (
                            action["kind"] == "click"
                            and _identity(snap, action) in self.skipped_clicks
                        )
                    ],
                }
                decision_goal = self.goal
                if self.account:
                    mode = "sign in" if self.account_flow.mode == "signin" else "sign up"
                    decision_goal = (
                        f"Complete this isolated account form to {mode}. "
                        "Password and verification inputs are handled by code. Fill the other "
                        "profile fields, then click the sign-in, create-account or verify-code "
                        "control. Leave marketing, newsletter and job alert opt-ins unticked. "
                        "After leaving this account form, resume the application goal: " + self.goal
                    )
                decision = await decide(self.client, offered, decision_goal, self.history)
                action = decision.action
                await self.require_fresh(snap, action)
                last_decision = decision
                # A reCAPTCHA badge on a usable form is not a blocking captcha (live Greenhouse
                # was classed "captcha" while its fields were fillable): hand off only when the
                # page offers nothing to fill or choose.
                fillable = bool(self.result.typed or self.result.uploads) or any(
                    a["kind"] in ("fill", "select") for a in offered["actions"]
                )
                if (
                    (decision.page_kind == "login_wall" and self.account is None)
                    or (decision.page_kind == "captcha" and not fillable)
                ) and decision.page_kind_p >= 0.6:
                    await self.handoff(decision.page_kind)
                    stale, unchanged = 0, 0
                    continue
                if decision.page_kind == "application_received" and decision.page_kind_p >= 0.8:
                    self.result.stop = "already_received"
                    return self.result
                on_form = self.form_context(snap, decision)
                if on_form and await self.upload_documents(snap):
                    stale, unchanged = 0, 0
                    continue  # Upload events may have changed fields or navigated; decide again.
                if decision.operation == "DONE":
                    if self.account:
                        await self.handoff("login_wall", "Complete sign-in in the open tab")
                        continue
                    if not on_form:
                        raise HarnessBlocked("DONE outside an application form")
                    if await self.scroll_for_more(snap):
                        continue
                    return self.ready(snap, decision)
                if (
                    decision.operation == "WAIT" and not self.account
                    and on_form and await self.form_complete(snap)
                ):
                    if await self.scroll_for_more(snap):
                        continue
                    await self.require_fresh(snap)
                    return self.ready(snap, decision)
                if decision.operation == "BLOCKED":
                    raise HarnessBlocked("The decision model cannot progress")
                if action is None or action not in offered["actions"]:
                    raise DecisionError("Decision has no observed target")
                text = None
                skipped = False
                if decision.operation == "CLICK":
                    # Recheck isolation immediately before relaxing the click guard.
                    self.account = (
                        await self.account_flow.detect(self.page)
                        if self.account and account_allowed(self.page.url)
                        and not self.account_flow.application_started and not self.result.uploads
                        else None
                    )
                    if self.account and not await account_control(
                        snap, action, code_only=self.account.code_only
                    ):
                        self.account = None
                    stop = (
                        "skip" if self.account and await opt_in_click(snap, action)
                        else self.click_stop(snap, decision)
                    )
                    if stop == "skip":
                        if (
                            not self.account and await self.form_complete(snap)
                            and not await self.scroll_for_more(snap)
                        ):
                            await self.require_fresh(snap, action)
                            return self.ready(snap, decision)
                        self.skipped_clicks.add(_identity(snap, action))
                        stop, skipped = None, True
                    if stop:
                        await self.require_fresh(snap, action)
                        if stop == "ready":
                            return self.ready(snap, decision)
                        self.result.stop, self.result.submit_action = stop, action
                        return self.result
                    kind = _document(action, self.documents, default=True)
                    if skipped:
                        pass
                    elif _UPLOAD.search(action["full_label"]) and kind in self.result.uploads:
                        self.skipped_clicks.add(_identity(snap, action))
                        skipped = True
                    else:
                        if self.account:
                            await self.account_flow.before_click(self.page, action, self.account)
                        # Input may succeed even when navigation/settling raises.
                        self.result.clicked += 1
                        # Preserve evidence for the runner's no-fallback-after-submit rule.
                        if action.get("submits"):
                            self.result.clicked_submit = True
                        if action.get("role") in _CHOICE_ROLES:
                            self.chose_field = True
                        chosen = None
                        if action.get("role") in _CHOICE_ROLES | {"option", "menuitemradio"}:
                            if self.account is None:
                                self.account_flow.application_started = True
                            last = self.history[-1] if self.history else {}
                            # The question is the field the previous step really opened.
                            opened = (
                                last.get("action", "").startswith("Open ")
                                and last.get("page_changed")
                                and not last.get("skipped")
                            )
                            question = last["action"].removeprefix("Open ") if opened else ""
                            chosen = (
                                f"{question}: {action['label']}" if question else action["label"]
                            )
                        self.chooser_document = kind
                        try:
                            await act(self.page, snap, action)
                            await self.drain_choosers()
                        finally:
                            self.chooser_document = "cv"
                        if chosen:
                            self.result.chosen.append(chosen)
                        if action.get("role") in {"checkbox", "switch"}:
                            self.clicked_toggles.add(_identity(snap, action))
                elif decision.operation == "TYPE_TEXT":
                    text, executed = await self.fill(snap, action)
                    skipped = not executed
                else:
                    if action["kind"] == "select":
                        if action.get("submits"):
                            return self.ready(snap, decision)
                        if await self.account_flow.detect(self.page) is None:
                            self.account_flow.application_started = True
                        self.chose_field = True
                    # Indirect submission through arbitrary event listeners cannot
                    # be inferred from the DOM; explicit select handlers are guarded.
                    await act(self.page, snap, action)
                self.result.steps += 1
                self.history.append(
                    {
                        "action": action["label"],
                        "kind": action["kind"],
                        "text": text,
                        "page_changed": None,
                        "skipped": skipped,
                        # not sent to the model: decide.build_request whitelists history keys
                        "click_id": _identity(snap, action)
                        if action["kind"] == "click" and action["label"].startswith("Open ")
                        else None,
                    }
                )
                previous = snap["fingerprint"]
                stale = 0
            except StalePage as exc:
                stale += 1
                if stale >= 3:
                    raise HarnessStuck("Three consecutive stale decisions") from exc
            except AccountError as exc:
                await self.handoff("login_wall", str(exc))
                stale, unchanged = 0, 0
                previous = None


async def run_harness(
    page,
    *,
    goal: str,
    slots: dict,
    documents: dict[str, Path],
    writer: Callable[[dict], Awaitable[str | None]] | None,
    client,
    job_id: int | None,
    cancel_event: asyncio.Event | None = None,
    max_actions: int = 60,
    notify: Callable[[str, str], Awaitable[None]] | None = None,
    wall_clock_s: float = 300,
    account_flow: AccountFlow | None = None,
) -> HarnessResult:
    """Consume and close the decision client; leave the caller's page and context open."""
    account_flow = account_flow or AccountFlow(
        password=None, email=slots.get("email", ("", ""))[1], company=""
    )
    run = _Run(
        page,
        goal,
        slots,
        documents,
        writer,
        _LimitedClient(client, account_flow.clean),
        job_id,
        cancel_event,
        max_actions,
        notify,
        account_flow,
    )
    tasks = []

    async def bounded_run():
        # Keep the timeout in the worker task so handoff can restore it during
        # cancellation before the timeout context exits.
        async with asyncio.timeout(wall_clock_s) as timeout:
            run.timeout = timeout
            return await run.run()

    try:
        try:
            page.on("filechooser", run.filechooser)
            if cancel_event is None:
                return await bounded_run()
            work = asyncio.create_task(bounded_run())
            cancelled = asyncio.create_task(cancel_event.wait())
            tasks = [work, cancelled]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                raise HarnessBlocked("Application cancelled")
            return work.result()
        finally:
            page.remove_listener("filechooser", run.filechooser)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*run.choosers, return_exceptions=True)
            await client.aclose()
    except RequestTooLong as exc:
        raise HarnessBlocked(
            "Decision request is still too long after shrinking", result=run.result
        ) from exc
    except TimeoutError as exc:
        raise HarnessStuck("Harness wall-clock time limit reached", result=run.result) from exc
    except HarnessBlocked as exc:
        if account_flow.clean(str(exc)) != str(exc):
            raise HarnessBlocked(account_flow.clean(str(exc)), result=run.result) from None
        exc.result = run.result
        raise
    except asyncio.CancelledError as exc:
        exc.result = run.result
        raise
    except Exception as exc:
        safe = account_flow.clean(str(exc))
        if safe != str(exc):
            raise HarnessBlocked(safe, result=run.result) from None
        raise HarnessBlocked(safe, result=run.result) from exc
    finally:
        for item in fields(run.result):
            setattr(run.result, item.name, account_flow.clean(getattr(run.result, item.name)))
