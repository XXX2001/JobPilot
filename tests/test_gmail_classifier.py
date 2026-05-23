from __future__ import annotations

import pytest

from backend.gmail.classifier_heuristics import classify


@pytest.mark.parametrize("from_address,subject,expected_category,expected_vendor", [
    # ATS acks
    ("no-reply@greenhouse.io", "We received your application", "ats_ack", "greenhouse"),
    ("notifications@hire.lever.co", "Application received — Acme Corp", "ats_ack", "lever"),
    ("careers@acme.myworkday.com", "Application received", "ats_ack", "workday"),
    ("noreply@ashbyhq.com", "Thank you for applying", "ats_ack", "ashby"),
    # Rejections
    ("recruiter@acme.com", "Update on your application — unfortunately we have decided to proceed with other candidates",
     "rejection", None),
    ("hiring@beta.io", "Regretfully informing you", "rejection", None),
    # Interview invites
    ("recruiter@gamma.com", "Interview invitation — next steps", "interview_invite", None),
    ("hr@delta.io", "Are you available for a chat next week? calendly.com/delta-hr", "interview_invite", None),
    # Offers
    ("ceo@epsilon.io", "We are pleased to extend an offer letter", "offer", None),
    # Noise
    ("newsletter@indeed.com", "10 jobs you might like", "noise", None),
    # Unknown
    ("friend@gmail.com", "lunch tomorrow?", "unknown", None),
])
def test_classify_known_patterns(from_address, subject, expected_category, expected_vendor):
    category, confidence, vendor = classify(
        from_address=from_address, subject=subject, snippet=subject,
    )
    assert category == expected_category, f"got {category!r} (conf {confidence:.2f})"
    if expected_vendor is not None:
        assert vendor == expected_vendor


def test_confidence_capped_at_0_85():
    """Heuristic confidence never exceeds 0.85 so the Phase 2 LLM can override."""
    _, confidence, _ = classify(
        from_address="no-reply@greenhouse.io",
        subject="We received your application",
        snippet="Thanks",
    )
    assert 0.0 < confidence <= 0.85


def test_ats_vendor_alone_is_ats_ack_default():
    """If the sender is an ATS but the subject doesn't match a more specific
    pattern (rejection / interview / offer), default to 'ats_ack'."""
    cat, _, vendor = classify(
        from_address="random@boards.greenhouse.io",
        subject="Some neutral subject line",
        snippet="",
    )
    assert cat == "ats_ack"
    assert vendor == "greenhouse"


def test_rejection_pattern_beats_ats_default():
    """Rejection wording inside an ATS email is still classified as rejection."""
    cat, _, vendor = classify(
        from_address="no-reply@greenhouse.io",
        subject="Update — unfortunately we have decided to proceed with other candidates",
        snippet="Thank you for applying",
    )
    assert cat == "rejection"
    assert vendor == "greenhouse"  # vendor still extracted
