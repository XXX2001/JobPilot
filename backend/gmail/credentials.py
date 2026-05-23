from __future__ import annotations

from typing import Iterable, Optional

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.gmail import GmailCredential


def _fernet() -> Fernet:
    key = settings.CREDENTIAL_KEY.get_secret_value()
    if not key:
        raise RuntimeError(
            "CREDENTIAL_KEY is not set — refusing to encrypt/decrypt Gmail refresh tokens."
        )
    return Fernet(key.encode())


def encrypt_refresh_token(refresh_token: str) -> str:
    return _fernet().encrypt(refresh_token.encode()).decode()


def decrypt_refresh_token(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()


async def save_credential(
    session: AsyncSession,
    email_address: str,
    refresh_token: str,
    scopes: Iterable[str],
) -> GmailCredential:
    """Upsert a credential row, rotating the refresh token if it already exists."""
    existing = await load_credential(session, email_address)
    encrypted = encrypt_refresh_token(refresh_token)
    scope_str = " ".join(scopes)
    if existing is None:
        row = GmailCredential(
            email_address=email_address,
            encrypted_refresh_token=encrypted,
            scopes=scope_str,
        )
        session.add(row)
        return row
    existing.encrypted_refresh_token = encrypted
    existing.scopes = scope_str
    return existing


async def load_credential(
    session: AsyncSession, email_address: str
) -> Optional[GmailCredential]:
    result = await session.execute(
        select(GmailCredential).where(GmailCredential.email_address == email_address)
    )
    return result.scalar_one_or_none()


async def delete_credential(session: AsyncSession, email_address: str) -> bool:
    row = await load_credential(session, email_address)
    if row is None:
        return False
    await session.delete(row)
    return True
