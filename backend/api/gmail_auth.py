from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend import config as _config
from backend.database import AsyncSessionLocal
from backend.gmail.auth import TOKEN_URL, revoke_refresh_token
from backend.gmail.credentials import (
    decrypt_refresh_token,
    delete_credential,
    load_credential,
    save_credential,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gmail", tags=["gmail"])

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
PHASE_1_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_STATE_TTL_SECONDS = 600


def _sign_state() -> str:
    """Return a state token of the form `<nonce>.<ts>.<hmac>`."""
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    key = _config.settings.CREDENTIAL_KEY.get_secret_value().encode()
    mac = hmac.new(key, f"{nonce}.{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{ts}.{mac}"


def _verify_state(token: str) -> bool:
    try:
        nonce, ts, mac = token.split(".")
    except ValueError:
        return False
    try:
        age = time.time() - int(ts)
    except ValueError:
        return False
    if age > _STATE_TTL_SECONDS or age < 0:
        return False
    key = _config.settings.CREDENTIAL_KEY.get_secret_value().encode()
    expected = hmac.new(key, f"{nonce}.{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def _ensure_oauth_configured() -> None:
    if not _config.settings.GMAIL_CLIENT_ID or not _config.settings.GMAIL_CLIENT_SECRET.get_secret_value():
        raise HTTPException(
            status_code=503,
            detail="Gmail OAuth not configured — set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env",
        )


@router.get("/oauth/start")
async def oauth_start() -> RedirectResponse:
    _ensure_oauth_configured()
    params = {
        "client_id": _config.settings.GMAIL_CLIENT_ID,
        "redirect_uri": _config.settings.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(PHASE_1_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": _sign_state(),
        "include_granted_scopes": "true",
    }
    return RedirectResponse(f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str, error: Optional[str] = None) -> RedirectResponse:
    if error:
        logger.warning("Gmail OAuth callback error: %s", error)
        return RedirectResponse("/settings?gmail_error=" + error, status_code=302)
    if not _verify_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    _ensure_oauth_configured()

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": _config.settings.GMAIL_CLIENT_ID,
            "client_secret": _config.settings.GMAIL_CLIENT_SECRET.get_secret_value(),
            "redirect_uri": _config.settings.GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        token_resp.raise_for_status()
        token_payload = token_resp.json()
        refresh_token = token_payload.get("refresh_token")
        if not refresh_token:
            raise HTTPException(
                status_code=400,
                detail="Google did not return a refresh_token — revoke the previous grant and retry.",
            )
        access_token = token_payload["access_token"]
        granted_scopes = token_payload.get("scope", " ".join(PHASE_1_SCOPES)).split()

        profile_resp = await client.get(
            GMAIL_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile_resp.raise_for_status()
        email_address = profile_resp.json()["emailAddress"]

    async with AsyncSessionLocal() as session:
        await save_credential(
            session,
            email_address=email_address,
            refresh_token=refresh_token,
            scopes=granted_scopes,
        )
        await session.commit()

    return RedirectResponse("/settings?gmail_connected=1", status_code=302)


class DisconnectBody(BaseModel):
    email: str


@router.post("/disconnect")
async def disconnect(body: DisconnectBody) -> dict:
    async with AsyncSessionLocal() as session:
        cred = await load_credential(session, body.email)
        rt: Optional[str] = None
        if cred is not None:
            rt = decrypt_refresh_token(cred.encrypted_refresh_token)
        if rt:
            await revoke_refresh_token(rt)
        removed = await delete_credential(session, body.email)
        await session.commit()
    return {"removed": bool(removed)}
