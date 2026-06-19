"""Property-based tests for the heuristic Gmail classifier.

The existing ``test_gmail_classifier.py`` covers 11 hand-picked tuples. This
file extends that with Hypothesis strategies that fuzz the surrounding noise
to pin invariants the deep-dive (§10 — LOW: classifier fuzz) called out:

* **Subjects containing a rejection cue (``unfortunately`` near
  ``decided``/``chosen``)** are classified as ``rejection`` regardless of
  surrounding noise.
* **Senders from an ATS vendor domain** always return that vendor name,
  whatever the subject says.
* **Confidence is always in ``(0, 0.85]``** for any classified non-unknown
  category — the cap exists so the Phase 2 LLM tier can overrule.

If any of these invariants ever regress, Hypothesis will find a counter-
example and shrink it. Pre-T8 there were no property tests at all.
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.gmail.classifier_heuristics import (
    ATS_DOMAINS,
    _MAX_HEURISTIC_CONFIDENCE,
    classify,
)


# A "noise" word fragment Hypothesis sprinkles around the cue words to make
# sure the pattern is robust to surrounding context. ASCII-only so the test
# doesn't accidentally trip up our case-insensitive regex with Unicode width.
_noise_text = st.text(
    alphabet=string.ascii_letters + " .,-!?",
    min_size=0,
    max_size=80,
)

# Reasonable sender local parts — the heuristic checks the WHOLE address for
# the vendor fragment, so local-part content shouldn't influence routing.
_sender_local = st.text(
    alphabet=string.ascii_lowercase + string.digits + ".-_",
    min_size=1,
    max_size=20,
)


@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    prefix=_noise_text,
    suffix=_noise_text,
    cue=st.sampled_from(["decided", "chosen"]),
)
def test_rejection_pattern_robust_to_surrounding_noise(
    prefix: str, suffix: str, cue: str
) -> None:
    """``unfortunately .. {cue}`` anywhere in the subject → ``rejection``.

    The regex (``\\bunfortunately\\b.*\\b(decided|chosen)\\b``) is unanchored;
    the fuzz proves it stays unanchored across arbitrary noise either side.
    """
    subject = f"{prefix} unfortunately we have {cue} to proceed with others {suffix}"
    category, confidence, _vendor = classify(
        from_address="recruiter@example.com",
        subject=subject,
        snippet=None,
    )
    assert category == "rejection", (
        f"expected rejection for subject {subject!r}, got {category!r}"
    )
    assert 0.0 < confidence <= _MAX_HEURISTIC_CONFIDENCE


@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    local=_sender_local,
    domain_fragment=st.sampled_from(list(ATS_DOMAINS.keys())),
    subject=_noise_text,
)
def test_ats_vendor_always_extracted_from_sender_domain(
    local: str, domain_fragment: str, subject: str
) -> None:
    """A sender whose address contains a known ATS fragment → vendor populated.

    The classifier should never miss the vendor because the local part or
    subject was weird. The category can vary (rejection patterns may match
    even on a Greenhouse address, that's correct) but the vendor must be
    consistent with the domain.
    """
    expected_vendor = ATS_DOMAINS[domain_fragment]
    sender = f"{local}@{domain_fragment}"

    _category, _confidence, vendor = classify(
        from_address=sender, subject=subject, snippet=None
    )
    assert vendor == expected_vendor, (
        f"sender {sender!r} should extract vendor {expected_vendor!r}, got {vendor!r}"
    )


@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    sender=st.emails(),
    subject=_noise_text,
    snippet=_noise_text,
)
def test_confidence_invariant_for_any_input(
    sender: str, subject: str, snippet: str
) -> None:
    """Confidence is always in ``[0, 0.85]`` so the Phase 2 LLM can override.

    The unknown bucket uses ``0.0``; all others use up to ``0.85``. Both
    ends of the interval must be respected for every possible input or the
    LLM tier could be undercut by a heuristic claiming certainty.
    """
    _category, confidence, _vendor = classify(
        from_address=sender, subject=subject, snippet=snippet
    )
    assert 0.0 <= confidence <= _MAX_HEURISTIC_CONFIDENCE, (
        f"confidence {confidence} out of [0, {_MAX_HEURISTIC_CONFIDENCE}] "
        f"for from={sender!r} subj={subject!r}"
    )
