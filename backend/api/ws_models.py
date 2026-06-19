from __future__ import annotations

import json
from typing import Annotated, Literal, Union

try:
    from pydantic import BaseModel, Field, confloat  # type: ignore
except Exception:

    class BaseModel:  # type: ignore
        def __init__(self, **data):
            for k, v in data.items():
                setattr(self, k, v)

        def model_dump(self):
            return {k: v for k, v in self.__dict__.items()}

        def model_dump_json(self):
            return json.dumps(self.model_dump())

    def Field(*args, **kwargs):  # type: ignore
        return kwargs

    def confloat(*args, **kwargs):  # type: ignore
        return float


class Status(BaseModel):
    """Generic batch-progress status message (Phase 1/2/3 narration)."""

    type: Literal["status"] = "status"
    message: str
    progress: float = 0.0


class SkillGap(BaseModel):
    """One missing/under-represented skill inside a JobAssessment payload.

    ``criticality`` is the skill-importance weight produced by the fit
    engine (typically in ``[0, 1]``); the frontend renders it as a chip
    intensity. It is a float on the wire, not a string.
    """

    skill: str
    criticality: float


class JobAssessment(BaseModel):
    """Per-job fit assessment broadcast after the matching/grading step.

    NOTE: legacy wire format used `type="job_progress"`. We keep that
    discriminator so the value-on-the-wire is unchanged for any external
    listener; the Python class name is updated to reflect intent.
    """

    type: Literal["job_progress"] = "job_progress"
    match_id: int
    ats_score: float
    gap_severity: float
    decision: str
    covered: list[str]
    gaps: list[SkillGap]


class ScrapingStatus(BaseModel):
    type: Literal["scraping_status"] = "scraping_status"
    message: str
    source: str
    progress: float


class MatchingStatus(BaseModel):
    type: Literal["matching_status"] = "matching_status"
    count: int


class TailoringStatus(BaseModel):
    type: Literal["tailoring_status"] = "tailoring_status"
    job_id: int
    progress: float


class ApplyReview(BaseModel):
    type: Literal["apply_review"] = "apply_review"
    job_id: int
    filled_fields: dict[str, str]
    screenshot_base64: str | None = None


class ApplyResult(BaseModel):
    type: Literal["apply_result"] = "apply_result"
    job_id: int
    status: str
    method: str


class LoginRequired(BaseModel):
    type: Literal["login_required"] = "login_required"
    site: str
    browser_window_title: str


class LoginConfirmed(BaseModel):
    type: Literal["login_confirmed"] = "login_confirmed"
    site: str


class CaptchaDetected(BaseModel):
    """Emitted when the scraper or applier hits a CAPTCHA / block page."""

    type: Literal["captcha_detected"] = "captcha_detected"
    site: str
    job_id: int | None = None
    message: str


class CaptchaResolved(BaseModel):
    """Emitted when the user clears the CAPTCHA (or we time out)."""

    type: Literal["captcha_resolved"] = "captcha_resolved"
    job_id: int | None = None


class GmailSyncStatus(BaseModel):
    type: Literal["gmail_sync_status"] = "gmail_sync_status"
    last_history_id: str | None = None
    messages_synced: int = 0
    progress: float = 0.0


class GmailMessageReceived(BaseModel):
    type: Literal["gmail_message_received"] = "gmail_message_received"
    gmail_message_id: str
    from_address: str
    subject: str | None = None
    category: str | None = None
    category_confidence: float | None = None
    linked_application_id: int | None = None
    link_confidence: float | None = None


class Pong(BaseModel):
    """Server reply to a client `ping`."""

    type: Literal["pong"] = "pong"


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str
    code: str


WSMessage = Annotated[
    Union[
        Status,
        JobAssessment,
        ScrapingStatus,
        MatchingStatus,
        TailoringStatus,
        ApplyReview,
        ApplyResult,
        LoginRequired,
        LoginConfirmed,
        CaptchaDetected,
        CaptchaResolved,
        GmailSyncStatus,
        GmailMessageReceived,
        Pong,
        ErrorMessage,
    ],
    Field(discriminator="type"),
]


class ConfirmSubmit(BaseModel):
    type: Literal["confirm_submit"]
    job_id: int


class CancelApply(BaseModel):
    type: Literal["cancel_apply"]
    job_id: int


class PatchFields(BaseModel):
    """Client edits to mis-filled review fields, keyed by CSS selector.

    Sent right before ``confirm_submit`` so the backend can re-fill the
    patched selectors with the user-corrected values before clicking submit.
    """

    type: Literal["patch_fields"]
    job_id: int
    fields: dict[str, str]


class LoginDone(BaseModel):
    type: Literal["login_done"]
    site: str


class LoginCancel(BaseModel):
    type: Literal["login_cancel"]
    site: str


ClientMessage = Annotated[
    Union[ConfirmSubmit, CancelApply, PatchFields, LoginDone, LoginCancel],
    Field(discriminator="type"),
]


__all__ = [
    "WSMessage",
    "Status",
    "JobAssessment",
    "SkillGap",
    "ScrapingStatus",
    "MatchingStatus",
    "TailoringStatus",
    "ApplyReview",
    "ApplyResult",
    "LoginRequired",
    "LoginConfirmed",
    "CaptchaDetected",
    "CaptchaResolved",
    "GmailSyncStatus",
    "GmailMessageReceived",
    "Pong",
    "ErrorMessage",
    "ClientMessage",
    "ConfirmSubmit",
    "CancelApply",
    "PatchFields",
    "LoginDone",
    "LoginCancel",
]
