"""Phase 1 heuristic classifier — zero-cost, deterministic.

Returns ``(category, confidence ∈ (0, 0.85], ats_vendor | None)``.
Confidence is capped below 1.0 so the Phase 2 LLM tier can override.
"""

from __future__ import annotations

import re
from typing import Optional

# Sender domain (or fragment) → ATS vendor name. Matches use a *contains* test
# so tenant-scoped Workday hosts like "careers.acme.myworkday.com" still hit.
ATS_DOMAINS: dict[str, str] = {
    "greenhouse-mail.io": "greenhouse",
    "greenhouse.io": "greenhouse",
    "hire.lever.co": "lever",
    "jobs.lever.co": "lever",
    "myworkday.com": "workday",
    "myworkdayjobs.com": "workday",
    "ashbyhq.com": "ashby",
    "workablemail.com": "workable",
    "workable.com": "workable",
    "smartrecruiters.com": "smartrecruiters",
    "taleo.net": "taleo",
    "icims.com": "icims",
    "bamboohr.com": "bamboohr",
}

# Newsletters / job-board digests we should never escalate.
NOISE_DOMAINS: tuple[str, ...] = (
    "newsletter@indeed.com",
    "linkedin-jobs@linkedin.com",
    "alerts@glassdoor.com",
)

REJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bnot moving forward\b",
        r"\bunfortunately\b.*\b(decided|chosen)\b",
        r"\bdecided to (proceed|move forward) with other\b",
        r"\bregret(fully)? (to )?inform(ing)?\b",
        r"\bnot (the )?right fit\b",
        r"\bwon't be moving forward\b",
    ]
]
INTERVIEW_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\binterview\b.*\b(invitation|invite|scheduling)\b",
        r"\b(invitation|invite)\b.*\binterview\b",
        r"\bnext step(s)?\b.*\b(call|chat|conversation)\b",
        r"\b(would|are) you available\b",
        r"\bcalendly\.com\b",
        r"\bsavvycal\.com\b",
        r"\bcal\.com\b",
    ]
]
OFFER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\boffer letter\b",
        r"\bcompensation (package|details)\b",
        r"\bpleased to extend\b",
        r"\bwelcome aboard\b",
    ]
]

_MAX_HEURISTIC_CONFIDENCE = 0.85


def _vendor_for(from_address: str) -> Optional[str]:
    lower = from_address.lower()
    for fragment, vendor in ATS_DOMAINS.items():
        if fragment in lower:
            return vendor
    return None


def classify(
    from_address: str, subject: Optional[str], snippet: Optional[str]
) -> tuple[str, float, Optional[str]]:
    """Return (category, confidence, ats_vendor). Confidence in (0, 0.85]."""
    blob = " ".join(filter(None, [subject, snippet])).strip()
    lower_addr = from_address.lower()

    if any(d in lower_addr for d in NOISE_DOMAINS):
        return "noise", _MAX_HEURISTIC_CONFIDENCE, None

    vendor = _vendor_for(from_address)

    # Subject/body patterns trump default ATS-ack
    for pat in REJECTION_PATTERNS:
        if pat.search(blob):
            return "rejection", _MAX_HEURISTIC_CONFIDENCE, vendor
    for pat in OFFER_PATTERNS:
        if pat.search(blob):
            return "offer", _MAX_HEURISTIC_CONFIDENCE, vendor
    for pat in INTERVIEW_PATTERNS:
        if pat.search(blob):
            return "interview_invite", _MAX_HEURISTIC_CONFIDENCE, vendor

    if vendor is not None:
        return "ats_ack", 0.7, vendor

    return "unknown", 0.0, None
