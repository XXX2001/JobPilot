"""Test data factories — one canonical way to construct each model.

Every factory:

* Returns a SQLAlchemy model instance (not a dict). Callers are responsible
  for ``session.add(...)`` + ``session.commit()`` so they retain control over
  transactions.
* Accepts ``**overrides`` so any field can be customised inline without the
  factory having to know about every test's quirks.
* Picks sensible defaults that satisfy ``nullable=False`` columns. Defaults
  are deterministic (no UUID/random fluctuation) unless the caller passes
  ``unique=True`` for a value that needs collision-avoidance — useful now
  that DB isolation is per-test (T8) and the email-prefix workaround is gone.

These factories are exemplars. Other tracks adopting them post-merge can
extend them in-place rather than redefining ``_seed_*`` helpers in every
test file. Keep them BORING: a factory that does I/O, calls into business
logic, or returns randomised data is a test-debugging nightmare.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from itertools import count
from typing import Any

from backend.models.application import Application, ApplicationEvent
from backend.models.document import TailoredDocument
from backend.models.gmail import (
    ApplicationCorrespondence,
    GmailCredential,
    GmailMessage,
)
from backend.models.job import Job, JobMatch, JobSource


# ── Helpers ────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Naive UTC, matching the models' ``_now()`` convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Process-wide counters used when ``unique=True`` is requested. Even though
# DB isolation is now per-test, sometimes a single test creates many rows
# and needs ascending unique keys without spelling them out.
_seq: dict[str, count[int]] = {}


def _next(domain: str) -> int:
    return next(_seq.setdefault(domain, count(1)))


def _apply_overrides(instance: Any, overrides: dict[str, Any]) -> Any:
    for key, value in overrides.items():
        setattr(instance, key, value)
    return instance


# ── Job + matching ─────────────────────────────────────────────────────────


def make_job_source(
    *,
    name: str = "test-source",
    type: str = "adzuna",
    url: str = "https://example.com",
    enabled: bool = True,
    **overrides: Any,
) -> JobSource:
    return _apply_overrides(
        JobSource(name=name, type=type, url=url, config={}, enabled=enabled),
        overrides,
    )


def make_job(
    *,
    title: str = "Senior Python Engineer",
    company: str = "Acme",
    location: str = "Remote",
    url: str = "https://example.com/jobs/1",
    description: str = "Build delightful Python.",
    unique: bool = False,
    **overrides: Any,
) -> Job:
    """Build a ``Job`` row with the bare-minimum NOT NULL columns set."""
    if unique:
        n = _next("job")
        url = f"{url}?n={n}"
    return _apply_overrides(
        Job(
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            apply_method="manual",
        ),
        overrides,
    )


def make_job_match(
    *,
    job_id: int,
    score: float = 0.75,
    status: str = "new",
    batch_date: date | None = None,
    **overrides: Any,
) -> JobMatch:
    return _apply_overrides(
        JobMatch(
            job_id=job_id,
            score=score,
            status=status,
            batch_date=batch_date or _now().date(),
        ),
        overrides,
    )


# ── Applications ───────────────────────────────────────────────────────────


def make_application(
    *,
    method: str = "manual",
    status: str = "pending",
    job_match_id: int | None = None,
    applied_at: datetime | None = None,
    **overrides: Any,
) -> Application:
    """Build an ``Application`` row.

    For tests that need an applied-state row, pass ``status="applied"`` and
    optionally ``applied_at=...`` — the default leaves ``applied_at=None``
    to keep the row as a draft.
    """
    return _apply_overrides(
        Application(
            method=method,
            status=status,
            job_match_id=job_match_id,
            applied_at=applied_at,
        ),
        overrides,
    )


def make_application_event(
    *,
    application_id: int,
    event_type: str = "applied",
    details: str | None = None,
    **overrides: Any,
) -> ApplicationEvent:
    return _apply_overrides(
        ApplicationEvent(
            application_id=application_id,
            event_type=event_type,
            details=details,
        ),
        overrides,
    )


def make_tailored_document(
    *,
    job_match_id: int,
    doc_type: str = "cv",
    file_path: str = "/tmp/doc.pdf",
    **overrides: Any,
) -> TailoredDocument:
    return _apply_overrides(
        TailoredDocument(
            job_match_id=job_match_id,
            doc_type=doc_type,
            file_path=file_path,
        ),
        overrides,
    )


# ── Gmail ──────────────────────────────────────────────────────────────────


def make_gmail_credential(
    *,
    email_address: str = "user@example.com",
    encrypted_refresh_token: str = "enc-blob",
    scopes: str = "https://www.googleapis.com/auth/gmail.readonly",
    enabled: bool = True,
    unique: bool = False,
    **overrides: Any,
) -> GmailCredential:
    """Build a ``GmailCredential`` row.

    Pre-T8 every Gmail test had to dodge the unique constraint on
    ``email_address`` with ``creds-``/``auth-u1``/``sync-u1`` prefixes
    because the DB was session-scoped. With per-test DB isolation the
    default email is the same in every test; pass ``unique=True`` only
    when a *single* test needs to seed multiple distinct credentials.
    """
    if unique:
        n = _next("gmail_credential_email")
        email_address = f"u{n}-{email_address}"
    return _apply_overrides(
        GmailCredential(
            email_address=email_address,
            encrypted_refresh_token=encrypted_refresh_token,
            scopes=scopes,
            enabled=enabled,
        ),
        overrides,
    )


def make_gmail_message(
    *,
    gmail_message_id: str = "m-1",
    gmail_thread_id: str = "t-1",
    account_email: str = "user@example.com",
    from_address: str = "no-reply@greenhouse.io",
    from_domain: str = "greenhouse.io",
    subject: str | None = "We received your application",
    snippet: str | None = "Thanks for applying",
    received_at: datetime | None = None,
    category: str | None = "ats_ack",
    category_confidence: float | None = 0.7,
    classified_by: str | None = "heuristic",
    unique: bool = False,
    **overrides: Any,
) -> GmailMessage:
    """Build a ``GmailMessage`` row.

    Pass ``unique=True`` when seeding multiple messages in one test — both
    ``gmail_message_id`` and ``gmail_thread_id`` get a counter suffix so the
    unique constraint on ``gmail_message_id`` is satisfied without the
    caller having to invent IDs.
    """
    if unique:
        n = _next("gmail_message_id")
        gmail_message_id = f"{gmail_message_id}-{n}"
        gmail_thread_id = f"{gmail_thread_id}-{n}"
    return _apply_overrides(
        GmailMessage(
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            account_email=account_email,
            from_address=from_address,
            from_domain=from_domain,
            subject=subject,
            snippet=snippet,
            received_at=received_at or _now(),
            category=category,
            category_confidence=category_confidence,
            classified_by=classified_by,
        ),
        overrides,
    )


def make_correspondence(
    *,
    application_id: int,
    message_id: int,
    gmail_thread_id: str = "t-1",
    direction: str = "inbound",
    link_confidence: float = 1.0,
    link_method: str = "manual",
    confirmed_by_user: bool = True,
    **overrides: Any,
) -> ApplicationCorrespondence:
    return _apply_overrides(
        ApplicationCorrespondence(
            application_id=application_id,
            message_id=message_id,
            gmail_thread_id=gmail_thread_id,
            direction=direction,
            link_confidence=link_confidence,
            link_method=link_method,
            confirmed_by_user=confirmed_by_user,
        ),
        overrides,
    )


__all__ = [
    "make_application",
    "make_application_event",
    "make_correspondence",
    "make_gmail_credential",
    "make_gmail_message",
    "make_job",
    "make_job_match",
    "make_job_source",
    "make_tailored_document",
]
