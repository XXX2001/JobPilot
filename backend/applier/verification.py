"""Destination evidence for a submission (W06 / F02).

A click, an HTTP success or an agent saying "done" is not proof that a site
accepted an application. Only the destination page can confirm it: a
confirmation message → ``confirmed``; validation errors → ``rejected``;
anything else (another step, an unreadable page) → ``unknown``, which callers
record as *needs verification* and never retry automatically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

#: Works for Playwright ``page.evaluate`` and browser-use ``Page.evaluate``
#: (both take an arrow-function string; the JSON string survives both).
PROBE_JS = (
    "() => JSON.stringify({url: location.href, "
    "invalid: document.querySelectorAll('form :invalid, [aria-invalid=\"true\"]').length, "
    "text: (document.body ? document.body.innerText : '').slice(0, 20000)})"
)

# ponytail: phrase heuristics (EN/FR); per-site verifiers replace this when a
# supported flow has a stable receipt/application-id signal (W17 fixtures).
_CONFIRMED = re.compile(
    r"thank(s| you) for (your )?(applying|application|applicat)"
    r"|application (has been |was )?(successfully )?(received|submitted|sent|complete)"
    r"|we('ve| have) received your application"
    r"|successfully (applied|submitted)"
    r"|merci pour votre candidature"
    r"|candidature (a (bien )?été )?(bien )?(envoyée|reçue|transmise|enregistrée)",
    re.IGNORECASE,
)
_REJECTED = re.compile(
    r"this field is required|(is|are) required\b|please (fill|complete|correct|enter)"
    r"|invalid (email|phone|value)|champ (est )?obligatoire|veuillez (remplir|corriger|saisir)",
    re.IGNORECASE,
)
# A failure word near a confirmation phrase ("not successfully submitted",
# "thank you for applying! An error occurred") makes the page contradictory.
_FAILURE = re.compile(
    r"\b(not|never|unable|unsuccessful|failed|failure|error|could ?n.t|cannot|try again)\b"
    r"|n.a pas|n.ont pas|\bpas (été|encore)\b|échec|erreur|réessayer|impossible",
    re.IGNORECASE,
)
_CONFLICT_WINDOW = 80
_EVIDENCE_CHARS = 300


@dataclass(frozen=True)
class SubmissionCheck:
    outcome: Literal["confirmed", "rejected", "unknown"]
    evidence: str


def classify_page(
    *,
    text: str,
    invalid_count: int,
    url: str,
    before_url: str | None = None,
    before_text: str | None = None,
) -> SubmissionCheck:
    """Confirmation first. ``rejected`` (= nothing was submitted) only when
    the page did not navigate and still has invalid fields; everything else
    without a confirmation is ``unknown`` → needs verification, because a
    false "rejected" invites a duplicate submission.

    A confirmation phrase counts only when no failure word sits near it and
    it was not already on the page before the click (*before_text*)."""
    matches = list(_CONFIRMED.finditer(text))
    for m in matches:
        window = text[max(0, m.start() - _CONFLICT_WINDOW) : m.end() + _CONFLICT_WINDOW]
        if f := _FAILURE.search(window):
            snippet = " ".join(window.split())
            return SubmissionCheck(
                "unknown", f"conflicting text ({f.group(0)!r}): {snippet} @ {url}"[:_EVIDENCE_CHARS]
            )
    if before_text is not None:
        before = before_text.lower()
        matches = [m for m in matches if m.group(0).lower() not in before]
        if not matches and _CONFIRMED.search(text):
            return SubmissionCheck(
                "unknown", f"confirmation text was already shown before submit @ {url}"
            )
    if matches:
        m = matches[0]
        start = max(0, m.start() - 80)
        snippet = " ".join(text[start : m.end() + 120].split())
        return SubmissionCheck("confirmed", f"{snippet} @ {url}"[:_EVIDENCE_CHARS])
    if invalid_count > 0 and before_url is not None and url == before_url:
        return SubmissionCheck(
            "rejected", f"{invalid_count} form field(s) failed validation at {url}"
        )
    hint = f"; validation text {m.group(0)!r}" if (m := _REJECTED.search(text)) else ""
    return SubmissionCheck("unknown", f"no confirmation found at {url}{hint}"[:_EVIDENCE_CHARS])


async def page_text(page: Any) -> str | None:
    """Visible page text, or ``None`` if it cannot be read (used as *before_text*)."""
    try:
        raw = await page.evaluate(PROBE_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return str(data.get("text", ""))
    except Exception:  # noqa: BLE001 — no baseline is the old behaviour
        return None


async def verify_page(
    page: Any, *, before_url: str | None = None, before_text: str | None = None
) -> SubmissionCheck:
    """Inspect the current page; any failure to read it is ``unknown``, never confirmed."""
    try:
        raw = await page.evaluate(PROBE_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return classify_page(
            text=str(data.get("text", "")),
            invalid_count=int(data.get("invalid", 0)),
            url=str(data.get("url", "")),
            before_url=before_url,
            before_text=before_text,
        )
    except Exception as exc:  # noqa: BLE001 — unreadable page = no evidence
        return SubmissionCheck("unknown", f"could not inspect the page: {exc}"[:_EVIDENCE_CHARS])
