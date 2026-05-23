from __future__ import annotations

import pytest

from backend.database import AsyncSessionLocal, init_db
from backend.gmail.credentials import (
    delete_credential,
    encrypt_refresh_token,
    decrypt_refresh_token,
    load_credential,
    save_credential,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_fernet_roundtrip():
    blob = encrypt_refresh_token("real-google-refresh-token-1234")
    assert blob != "real-google-refresh-token-1234"  # actually encrypted
    assert decrypt_refresh_token(blob) == "real-google-refresh-token-1234"


async def test_save_and_load_credential():
    async with AsyncSessionLocal() as session:
        cred = await save_credential(
            session,
            email_address="creds-user@example.com",
            refresh_token="rt-xyz",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        await session.commit()
        assert cred.id is not None
        assert cred.encrypted_refresh_token != "rt-xyz"

    async with AsyncSessionLocal() as session:
        loaded = await load_credential(session, "creds-user@example.com")
        assert loaded is not None
        assert decrypt_refresh_token(loaded.encrypted_refresh_token) == "rt-xyz"


async def test_save_credential_is_upsert():
    """Calling save_credential twice for the same email rotates the token."""
    async with AsyncSessionLocal() as session:
        await save_credential(session, "creds-u@e.com", "first", ["gmail.readonly"])
        await save_credential(session, "creds-u@e.com", "second", ["gmail.readonly"])
        await session.commit()
        loaded = await load_credential(session, "creds-u@e.com")
    assert decrypt_refresh_token(loaded.encrypted_refresh_token) == "second"


async def test_delete_credential():
    async with AsyncSessionLocal() as session:
        await save_credential(session, "creds-u@e.com", "rt", ["gmail.readonly"])
        await session.commit()
    async with AsyncSessionLocal() as session:
        removed = await delete_credential(session, "creds-u@e.com")
        await session.commit()
        assert removed is True
        assert await load_credential(session, "creds-u@e.com") is None
