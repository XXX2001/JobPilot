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


class ScrapingStatus(BaseModel):
    type: Literal["scraping_status"]
    message: str
    source: str
    progress: float


class MatchingStatus(BaseModel):
    type: Literal["matching_status"]
    count: int


class TailoringStatus(BaseModel):
    type: Literal["tailoring_status"]
    job_id: int
    progress: float


class ApplyReview(BaseModel):
    type: Literal["apply_review"]
    job_id: int
    filled_fields: dict[str, str]
    screenshot_base64: str | None = None


class ApplyResult(BaseModel):
    type: Literal["apply_result"]
    job_id: int
    status: str
    method: str


class LoginRequired(BaseModel):
    type: Literal["login_required"]
    site: str
    browser_window_title: str


class LoginConfirmed(BaseModel):
    type: Literal["login_confirmed"]
    site: str


class ErrorMessage(BaseModel):
    type: Literal["error"]
    message: str
    code: str


WSMessage = Annotated[
    Union[
        ScrapingStatus,
        MatchingStatus,
        TailoringStatus,
        ApplyReview,
        ApplyResult,
        LoginRequired,
        LoginConfirmed,
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


class LoginDone(BaseModel):
    type: Literal["login_done"]
    site: str


class LoginCancel(BaseModel):
    type: Literal["login_cancel"]
    site: str


ClientMessage = Annotated[
    Union[ConfirmSubmit, CancelApply, LoginDone, LoginCancel], Field(discriminator="type")
]


__all__ = [
    "WSMessage",
    "ScrapingStatus",
    "MatchingStatus",
    "TailoringStatus",
    "ApplyReview",
    "ApplyResult",
    "LoginRequired",
    "LoginConfirmed",
    "ErrorMessage",
    "ClientMessage",
    "ConfirmSubmit",
    "CancelApply",
    "LoginDone",
    "LoginCancel",
]
