from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, not_, select
from sqlalchemy.exc import IntegrityError

from backend.api.deps import DBSession
from backend.models.application import Application
from backend.models.gmail import ApplicationCorrespondence, GmailMessage
from backend.utils.time import utc_now

router = APIRouter(prefix="/api/correspondence", tags=["correspondence"])


class CorrespondenceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    subject: Optional[str]
    snippet: Optional[str]
    received_at: datetime
    category: Optional[str]
    category_confidence: Optional[float]


class UnlinkedItemOut(CorrespondenceItemOut):
    pass


class CorrespondenceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    application_id: int
    message_id: int
    gmail_thread_id: str
    direction: str
    link_confidence: float
    link_method: str
    confirmed_by_user: bool


class CorrespondenceThreadOut(BaseModel):
    application_id: int
    messages: list[CorrespondenceItemOut]


class UnlinkedListOut(BaseModel):
    items: list[UnlinkedItemOut]


class LinkBody(BaseModel):
    application_id: int
    gmail_message_id: int  # caller-facing name; maps to ApplicationCorrespondence.message_id


@router.get("/unlinked", response_model=UnlinkedListOut)
async def list_unlinked(db: DBSession) -> UnlinkedListOut:
    linked_subq = select(ApplicationCorrespondence.message_id)
    stmt = (
        select(GmailMessage)
        .where(and_(
            not_(GmailMessage.id.in_(linked_subq)),
            GmailMessage.category != "noise",
        ))
        .order_by(GmailMessage.received_at.desc())
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return UnlinkedListOut(items=[UnlinkedItemOut.model_validate(r) for r in rows])


@router.get("/{application_id}", response_model=CorrespondenceThreadOut)
async def list_for_application(application_id: int, db: DBSession) -> CorrespondenceThreadOut:
    stmt = (
        select(GmailMessage)
        .join(ApplicationCorrespondence,
              ApplicationCorrespondence.message_id == GmailMessage.id)
        .where(ApplicationCorrespondence.application_id == application_id)
        .order_by(GmailMessage.received_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return CorrespondenceThreadOut(
        application_id=application_id,
        messages=[CorrespondenceItemOut.model_validate(r) for r in rows],
    )


@router.post("/link", response_model=CorrespondenceLinkOut, status_code=201)
async def link(body: LinkBody, db: DBSession) -> CorrespondenceLinkOut:
    app = (await db.execute(
        select(Application).where(Application.id == body.application_id)
    )).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "application not found")

    msg = (await db.execute(
        select(GmailMessage).where(GmailMessage.id == body.gmail_message_id)
    )).scalar_one_or_none()
    if msg is None:
        raise HTTPException(404, "gmail_message not found")

    link_row = ApplicationCorrespondence(
        application_id=body.application_id,
        message_id=body.gmail_message_id,
        gmail_thread_id=msg.gmail_thread_id,
        direction="inbound",
        link_confidence=1.0,
        link_method="manual",
        confirmed_by_user=True,
    )
    db.add(link_row)
    app.last_correspondence_at = utc_now()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "link already exists")
    await db.refresh(link_row)
    return CorrespondenceLinkOut.model_validate(link_row)


@router.delete("/{link_id}", status_code=204, response_class=Response)
async def unlink(link_id: int, db: DBSession) -> Response:
    row = (await db.execute(
        select(ApplicationCorrespondence).where(ApplicationCorrespondence.id == link_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "link not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)
