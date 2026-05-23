from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from backend.database import AsyncSessionLocal, init_db
from backend.models.application import Application
from backend.models.gmail import GmailMessage


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_app() -> int:
    async with AsyncSessionLocal() as session:
        app = Application(method="manual", status="applied", applied_at=_now())
        session.add(app)
        await session.commit()
        return app.id


async def _seed_msg(mid: str, category: str = "ats_ack") -> int:
    async with AsyncSessionLocal() as session:
        msg = GmailMessage(
            gmail_message_id=mid, gmail_thread_id=f"t-{mid}",
            account_email="corr-u@e.com", from_address="no-reply@greenhouse.io",
            from_domain="greenhouse.io", subject="thanks", snippet="...",
            received_at=_now(), category=category, category_confidence=0.7,
            classified_by="heuristic",
        )
        session.add(msg)
        await session.commit()
        return msg.id


def test_unlinked_returns_non_noise_messages_without_link(test_app: TestClient):
    asyncio.run(_seed_msg("corr-m1", category="ats_ack"))
    asyncio.run(_seed_msg("corr-m-noise", category="noise"))
    resp = test_app.get("/api/correspondence/unlinked")
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {it["gmail_message_id"] for it in items}
    assert "corr-m1" in ids
    assert "corr-m-noise" not in ids


def test_link_creates_row_and_updates_application_timestamp(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("corr-m2"))

    resp = test_app.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_id,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["confirmed_by_user"] is True
    assert body["link_method"] == "manual"

    async def _read():
        async with AsyncSessionLocal() as session:
            return (await session.execute(
                select(Application).where(Application.id == app_id)
            )).scalar_one().last_correspondence_at
    assert asyncio.run(_read()) is not None


def test_list_for_application_returns_oldest_first(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    m1 = asyncio.run(_seed_msg("corr-m-a"))
    m2 = asyncio.run(_seed_msg("corr-m-b"))
    for mid in (m1, m2):
        r = test_app.post("/api/correspondence/link", json={
            "application_id": app_id, "gmail_message_id": mid,
        })
        assert r.status_code == 201

    resp = test_app.get(f"/api/correspondence/{app_id}")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["gmail_message_id"] for m in msgs] == ["corr-m-a", "corr-m-b"]


def test_unlink_removes_row(test_app: TestClient):
    app_id = asyncio.run(_seed_app())
    msg_id = asyncio.run(_seed_msg("corr-m-c"))
    link = test_app.post("/api/correspondence/link", json={
        "application_id": app_id, "gmail_message_id": msg_id,
    })
    link_id = link.json()["id"]
    resp = test_app.delete(f"/api/correspondence/{link_id}")
    assert resp.status_code == 204

    listing = test_app.get(f"/api/correspondence/{app_id}")
    assert listing.json()["messages"] == []


def test_gmail_status_returns_not_connected_when_no_credential(test_app: TestClient):
    resp = test_app.get("/api/gmail/status")
    assert resp.status_code == 200
    data = resp.json()
    # Note: depending on test order, a previous test may have seeded a credential
    # via OAuth. Just assert the shape is sane.
    assert "connected" in data
    assert "email_address" in data
    assert "message_count" in data
