"""Correspondence API tests — refactored to use ``tests/factories``.

Pre-T8 this file used ad-hoc ``_seed_app()`` / ``_seed_msg()`` helpers and
``corr-`` email/message-id prefixes to dodge the shared-DB unique-constraint
collisions. Now that ``tests/conftest.py`` wipes the DB between tests we use
the canonical factories and don't need to invent disambiguating prefixes.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal
from backend.models.application import Application

from tests.factories import (
    make_application,
    make_gmail_message,
)


async def _seed_app() -> int:
    async with AsyncSessionLocal() as session:
        app = make_application(method="manual", status="applied")
        from tests.factories import _now as factory_now

        app.applied_at = factory_now()
        session.add(app)
        await session.commit()
        return app.id


async def _seed_msg(mid: str, category: str = "ats_ack") -> int:
    async with AsyncSessionLocal() as session:
        msg = make_gmail_message(
            gmail_message_id=mid,
            gmail_thread_id=f"t-{mid}",
            category=category,
        )
        session.add(msg)
        await session.commit()
        return msg.id


def test_unlinked_returns_non_noise_messages_without_link(test_app: TestClient):
    asyncio.run(_seed_msg("m1", category="ats_ack"))
    asyncio.run(_seed_msg("m-noise", category="noise"))
    resp = test_app.get("/api/correspondence/unlinked")
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {it["gmail_message_id"] for it in items}
    assert "m1" in ids
    assert "m-noise" not in ids


def test_link_creates_row_and_updates_application_timestamp(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("m2"))

    resp = test_app.post(
        "/api/correspondence/link",
        json={"application_id": app_id, "gmail_message_id": msg_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["confirmed_by_user"] is True
    assert body["link_method"] == "manual"

    async def _read():
        async with AsyncSessionLocal() as session:
            return (
                (
                    await session.execute(
                        select(Application).where(Application.id == app_id)
                    )
                )
                .scalar_one()
                .last_correspondence_at
            )

    assert asyncio.run(_read()) is not None


def test_list_for_application_returns_oldest_first(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    m1 = asyncio.run(_seed_msg("m-a"))
    m2 = asyncio.run(_seed_msg("m-b"))
    for mid in (m1, m2):
        r = test_app.post(
            "/api/correspondence/link",
            json={"application_id": app_id, "gmail_message_id": mid},
        )
        assert r.status_code == 201

    resp = test_app.get(f"/api/correspondence/{app_id}")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["gmail_message_id"] for m in msgs] == ["m-a", "m-b"]


def test_unlink_removes_row(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("m-c"))
    link = test_app.post(
        "/api/correspondence/link",
        json={"application_id": app_id, "gmail_message_id": msg_id},
    )
    link_id = link.json()["id"]
    resp = test_app.delete(f"/api/correspondence/{link_id}")
    assert resp.status_code == 204

    listing = test_app.get(f"/api/correspondence/{app_id}")
    assert listing.json()["messages"] == []


def test_gmail_status_returns_not_connected_when_no_credential(test_app: TestClient):
    # T8: the per-test wipe in conftest guarantees a fresh DB here, so this
    # assertion is now deterministic (pre-T8 a prior test could leave a
    # credential row behind and flip ``connected`` to True).
    resp = test_app.get("/api/gmail/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["email_address"] is None
    assert data["message_count"] == 0


# Ensure the import-from-factories private helper is reachable (factory's _now
# is intentionally not in __all__; we use it here only because Application
# rows in the DB carry naive UTC datetimes for legacy reasons).
# This block is unused at runtime but keeps mypy/pyright from flagging the
# private import as dead.
_ = pytest  # silence unused-import linter
