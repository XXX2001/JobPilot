"""Observe, choose, and execute bounded application steps up to owner review.

Adapted from browser-use/jev-ultrafast, jev_ultrafast/agent.py (MIT License).
https://github.com/browser-use/jev-ultrafast
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from backend.applier.captcha_handler import check_and_handle_captcha

from .decide import DecisionError, RequestTooLong, classify_page, decide, pick_slot
from .execute import StalePage, act, attach
from .snapshot import ADVANCE_PATTERN, SUBMIT_PATTERN, fresh, observe

_POLL_SECONDS = 5
_MAX_REQUESTS = 120
HANDOFF_TIMEOUT_S = 600
_STRICT_SUBMIT = re.compile(
    r"submit|send|envoyer|soumettre|finish|complete|terminer|bewerben|absenden|"
    r"einreichen|abschicken|postule|postulez|candidater|valider|déposer|transmettre|confirm|"
    r"enviar|invia|verzenden|solliciteren",
    re.IGNORECASE,
)
_UPLOAD = re.compile(r"attach|upload|resume|résumé|\bcv\b|curriculum|cover|lettre|motivation", re.I)
_LETTER = re.compile(r"cover|lettre|motivation", re.I)
_CV = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
_CONSENT = re.compile(
    r"\b(?:accept|agree|ok|got it|close|dismiss|accepter|j'accepte|fermer|"
    r"tout accepter|alle akzeptieren|schließen)\b",
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


class _LimitedClient:
    def __init__(self, client):
        self.client = client
        self.model = client.model
        self.requests = 0

    async def post(self, body):
        if self.requests >= _MAX_REQUESTS:
            raise HarnessStuck("Reached the model request limit")
        self.requests += 1
        return await self.client.post(body)


def _identity(snap, action):
    index = action["frame"]
    # Node IDs restart on navigation; a new document must get new fill opportunities.
    return (snap["_frame_refs"][index], snap["frames"][index]["page_key"][0], action["node"])


def _document(target, documents, *, default=False, single_upload=False):
    own = " ".join(
        target.get(key, "") for key in ("full_label", "label", "name", "input_id")
    ).strip()
    hint = own or target.get("nearby_text", "").strip()
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
        kind = "cv"
    else:
        return None
    return kind if documents.get(kind) else None


def _append_once(items, value):
    if value not in items:
        items.append(value)


@dataclass
class _Run:
    page: object
    goal: str
    slots: dict
    documents: dict[str, Path]
    writer: Callable[[dict], Awaitable[str | None]] | None
    client: _LimitedClient
    job_id: int | None
    cancel_event: asyncio.Event | None
    max_actions: int
    notify: Callable[[str, str], Awaitable[None]] | None
    result: HarnessResult = field(
        default_factory=lambda: HarnessResult("ready", None, {}, [], [], [], 0)
    )
    history: list = field(default_factory=list)
    excluded: set = field(default_factory=set)
    skipped_clicks: set = field(default_factory=set)
    attached: set = field(default_factory=set)
    choosers: list = field(default_factory=list)
    clicked_toggles: set = field(default_factory=set)
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
        self.choosers.append(asyncio.create_task(self.answer_chooser(chooser)))

    async def answer_chooser(self, chooser):
        target = await chooser.element.evaluate(_UPLOAD_TARGET)
        kind = _document(target, self.documents, default=True)
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
            await attach(self.page, snap, candidate, self.documents[kind])
            self.attached.add(_identity(snap, candidate))
            _append_once(self.result.uploads, kind)
            self.result.steps += 1
            self.history.append(
                {"action": candidate["label"], "kind": "upload", "page_changed": True}
            )
            changed = True
            snap = await observe(self.page)

    async def handoff(self, kind):
        loop = asyncio.get_running_loop()
        remaining = self.timeout.when() - loop.time() if self.timeout is not None else None
        if self.timeout is not None:
            self.timeout.reschedule(None)
        try:
            async with asyncio.timeout(HANDOFF_TIMEOUT_S):
                if kind == "captcha" and await check_and_handle_captcha(
                    self.page, job_id=self.job_id, cancel_event=self.cancel_event
                ):
                    return
                if self.notify is not None:
                    host = urlparse(self.page.url).hostname or self.page.url
                    await self.notify(
                        "login" if kind == "login_wall" else "captcha",
                        f"{'Login needed' if kind == 'login_wall' else 'Captcha'} on {host}",
                    )
                while True:
                    self.check_limits()
                    await asyncio.sleep(_POLL_SECONDS)
                    snap = await observe(self.page)
                    # Owner time and page-kind-only polls are outside run budgets.
                    current, p = await classify_page(self.client.client, snap["text"])
                    if current != kind or p < 0.6:
                        return
        except TimeoutError as exc:
            raise HarnessBlocked("Owner hand-off timed out") from exc
        finally:
            if self.timeout is not None:
                self.timeout.reschedule(loop.time() + remaining)

    def form_context(self, snap, decision):
        return bool(
            (decision.page_kind == "application_form" and decision.page_kind_p >= 0.5)
            or self.result.typed
            or self.result.uploads
            or any(a["kind"] == "fill" for a in snap["actions"])
        )

    async def form_complete(self, snap):
        return not any(
            a["kind"] == "fill" and _identity(snap, a) not in self.excluded for a in snap["actions"]
        ) and not await self.pending_uploads(snap)

    def click_stop(self, snap, decision):
        action = decision.action
        label = action["full_label"]
        if _STRICT_SUBMIT.search(label):
            return "submit_point"
        if not self.form_context(snap, decision):
            return None
        identity = _identity(snap, action)
        # Open-field actions share their node with a fill action. Check these
        # before the combobox/advance allowlist so a filled field cannot reopen.
        if any(a["kind"] == "fill" and _identity(snap, a) == identity for a in snap["actions"]):
            return "ready" if identity in self.excluded or action.get("value") else None
        if not label.strip() and action.get("role") == "button":
            return "ready"
        if action.get("role") in {"checkbox", "switch"} and identity in self.clicked_toggles:
            return "ready"
        if (
            ADVANCE_PATTERN.search(label)
            or action.get("role") in _NON_SUBMIT_ROLES
            or _UPLOAD.search(label)
            or _CONSENT.search(label)
        ):
            return None
        return "ready"

    def ready(self, snap, decision):
        self.result.stop = "ready"
        self.result.submit_action = self.submit_for_done(snap, decision)
        return self.result

    def submit_for_done(self, snap, decision):
        if decision.submit_action is not None and decision.submit_p >= 0.3:
            return decision.submit_action
        candidates = [
            action
            for action in snap["actions"]
            if action["kind"] == "click"
            and action.get("submits")
            and SUBMIT_PATTERN.search(action["full_label"])
            and not (
                ADVANCE_PATTERN.search(action["full_label"])
                and not _STRICT_SUBMIT.search(action["full_label"])
            )
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
                    "label": action["label"],
                    "page_text": frame_text[start : start + 1500],
                    "page_title": snap["title"],
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
                snap = await observe(self.page)
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
                        if self.form_context(snap, last_decision) and await self.form_complete(
                            snap
                        ):
                            await self.require_fresh(snap)
                            return self.ready(snap, last_decision)
                        raise HarnessStuck("Three unchanged steps made no progress")
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
                decision = await decide(self.client, offered, self.goal, self.history)
                action = decision.action
                await self.require_fresh(snap, action)
                last_decision = decision
                if decision.page_kind in {"login_wall", "captcha"} and decision.page_kind_p >= 0.6:
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
                    if not on_form:
                        raise HarnessBlocked("DONE outside an application form")
                    return self.ready(snap, decision)
                if decision.operation == "WAIT" and on_form and await self.form_complete(snap):
                    await self.require_fresh(snap)
                    return self.ready(snap, decision)
                if decision.operation == "BLOCKED":
                    raise HarnessBlocked("The decision model cannot progress")
                if action is None or action not in offered["actions"]:
                    raise DecisionError("Decision has no observed target")
                text = None
                skipped = False
                if decision.operation == "CLICK":
                    stop = self.click_stop(snap, decision)
                    if stop:
                        await self.require_fresh(snap, action)
                        if stop == "ready":
                            return self.ready(snap, decision)
                        self.result.stop, self.result.submit_action = stop, action
                        return self.result
                    kind = _document(action, self.documents, default=True)
                    if _UPLOAD.search(action["full_label"]) and kind in self.result.uploads:
                        self.skipped_clicks.add(_identity(snap, action))
                        skipped = True
                    else:
                        await act(self.page, snap, action)
                        self.result.clicked += 1
                        # Preserve evidence for the runner's no-fallback-after-submit rule.
                        if action.get("submits") and self.result.typed:
                            self.result.clicked_submit = True
                        if action.get("role") in {"checkbox", "switch"}:
                            self.clicked_toggles.add(_identity(snap, action))
                        await self.drain_choosers()
                elif decision.operation == "TYPE_TEXT":
                    text, executed = await self.fill(snap, action)
                    skipped = not executed
                else:
                    # Known limit: a <select> whose change event submits its form
                    # is not detected by the click guard.
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
) -> HarnessResult:
    """Consume and close the decision client; leave the caller's page and context open."""
    run = _Run(
        page,
        goal,
        slots,
        documents,
        writer,
        _LimitedClient(client),
        job_id,
        cancel_event,
        max_actions,
        notify,
    )
    tasks = []
    page.on("filechooser", run.filechooser)

    async def bounded_run():
        # Keep the timeout in the worker task so handoff can restore it during
        # cancellation before the timeout context exits.
        async with asyncio.timeout(wall_clock_s) as timeout:
            run.timeout = timeout
            return await run.run()

    try:
        if cancel_event is None:
            return await bounded_run()
        work = asyncio.create_task(bounded_run())
        cancelled = asyncio.create_task(cancel_event.wait())
        tasks = [work, cancelled]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            raise HarnessBlocked("Application cancelled")
        return work.result()
    except RequestTooLong as exc:
        raise HarnessBlocked(
            "Decision request is still too long after shrinking", result=run.result
        ) from exc
    except TimeoutError as exc:
        raise HarnessStuck("Harness wall-clock time limit reached", result=run.result) from exc
    except HarnessBlocked as exc:
        exc.result = run.result
        raise
    finally:
        page.remove_listener("filechooser", run.filechooser)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*run.choosers, return_exceptions=True)
        await client.aclose()
