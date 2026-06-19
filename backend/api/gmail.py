from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.api.deps import DBSession
from backend.models.gmail import GmailCredential, GmailMessage

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


class GmailStatusOut(BaseModel):
    connected: bool
    email_address: Optional[str]
    last_synced_at: Optional[str]
    history_id: Optional[str]
    message_count: int
    enabled: bool


@router.get("/status", response_model=GmailStatusOut)
async def status(db: DBSession) -> GmailStatusOut:
    cred = (await db.execute(select(GmailCredential).limit(1))).scalar_one_or_none()
    if cred is None:
        return GmailStatusOut(
            connected=False, email_address=None,
            last_synced_at=None, history_id=None,
            message_count=0, enabled=False,
        )
    count = (await db.execute(
        select(func.count(GmailMessage.id)).where(GmailMessage.account_email == cred.email_address)
    )).scalar_one()
    return GmailStatusOut(
        connected=True,
        email_address=cred.email_address,
        last_synced_at=cred.last_synced_at.isoformat() if cred.last_synced_at else None,
        history_id=cred.history_id,
        message_count=int(count),
        enabled=cred.enabled,
    )


class SyncOut(BaseModel):
    synced: int


@router.post("/sync", response_model=SyncOut)
async def sync_now(request: Request, db: DBSession) -> SyncOut:
    """Force a sync pass for the connected account. Power-user / debug."""
    cred = (await db.execute(select(GmailCredential).limit(1))).scalar_one_or_none()
    if cred is None:
        raise HTTPException(404, "no gmail account connected")
    token_mgr = getattr(request.app.state, "gmail_token_manager", None)
    if token_mgr is None:
        raise HTTPException(503, "gmail integration not initialised")
    from backend.gmail.sync import GmailSyncWorker
    worker = GmailSyncWorker(token_manager=token_mgr)
    n = await worker.sync_now(cred.email_address)
    return SyncOut(synced=n)
