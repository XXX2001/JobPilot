"""W05 / F09 — regeneration is real, durable, and never destroys the last usable PDF."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from backend.config import DATA_DIR
from tests.pdfs import real_pdf


def _pdf(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(real_pdf(body))
    return path


async def _seed() -> tuple[int, int]:
    """Match with a base CV source and one existing (old) tailored CV."""
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.latex.artifacts import pdf_sha256
    from backend.models.document import TailoredDocument
    from backend.models.job import Job, JobMatch
    from backend.models.user import UserProfile

    tag = uuid.uuid4().hex[:8]
    tex = DATA_DIR / "templates" / f"regen-{tag}.tex"
    tex.parent.mkdir(parents=True, exist_ok=True)
    tex.write_text("\\documentclass{article}")
    async with AsyncSessionLocal() as db:
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.id == 1))
        ).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(id=1, full_name="T", email="t@example.com")
            db.add(profile)
        profile.base_cv_path = str(tex)
        job = Job(
            title="Eng", company=f"Co{tag}", url=f"https://e.x/{tag}", dedup_hash=f"regen-{tag}"
        )
        db.add(job)
        await db.flush()
        match = JobMatch(job_id=job.id, score=80.0, status="new")
        db.add(match)
        await db.flush()
        old = _pdf(DATA_DIR / "cvs" / f"{match.id}_old" / "cv.pdf", "OLD")
        doc = TailoredDocument(
            job_match_id=match.id, doc_type="cv", pdf_path=str(old), pdf_sha256=pdf_sha256(old)
        )
        db.add(doc)
        await db.commit()
        return match.id, doc.id


class _FakeGeneration:
    """Patched onto CVPipeline so it also applies to the lifespan-built singleton."""

    def __init__(self) -> None:
        self.fail = False
        self.gate: threading.Event | None = None
        self.calls = 0

    async def __call__(self, _self, base_cv_path, job, output_dir, **_kw):
        self.calls += 1
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait, 10)
        if self.fail:
            raise RuntimeError("tectonic: undefined control sequence")
        pdf = _pdf(output_dir / "cv.pdf", f"NEW for {job.title}")
        return SimpleNamespace(tex_path=output_dir / "cv.tex", pdf_path=pdf, diff=[])


@pytest.fixture
def gen(monkeypatch):
    from backend.latex.pipeline import CVPipeline

    fake = _FakeGeneration()
    async def _patched(self, *args, **kwargs):
        return await fake(self, *args, **kwargs)

    monkeypatch.setattr(CVPipeline, "generate_tailored_cv", _patched)
    return fake


def _wait(client: TestClient, op_id: int, *, until=("succeeded", "failed")) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        op = client.get(f"/api/documents/operations/{op_id}").json()
        if op["state"] in until:
            return op
        time.sleep(0.05)
    raise AssertionError(f"operation {op_id} stuck: {op}")


def test_regenerate_produces_a_retrievable_replacement(test_app, gen):
    mid, old_id = asyncio.run(_seed())

    resp = test_app.post(f"/api/documents/{mid}/regenerate", json={})

    assert resp.status_code == 202, resp.text
    op = _wait(test_app, resp.json()["operation_id"])
    assert op["state"] == "succeeded", op
    assert op["document_id"] != old_id
    pdf = test_app.get(f"/api/documents/{mid}/cv/pdf")
    assert pdf.status_code == 200 and b"NEW" in pdf.content
    # History is preserved: the old version is still retrievable by id.
    old = test_app.get(f"/api/documents/{mid}/cv/pdf?document_id={old_id}")
    assert old.status_code == 200 and b"OLD" in old.content


def test_failed_regeneration_keeps_the_previous_pdf(test_app, gen):
    gen.fail = True
    mid, _ = asyncio.run(_seed())

    op = _wait(
        test_app,
        test_app.post(f"/api/documents/{mid}/regenerate", json={"force": True}).json()[
            "operation_id"
        ],
    )

    assert op["state"] == "failed" and "undefined control sequence" in op["error"]
    pdf = test_app.get(f"/api/documents/{mid}/cv/pdf")
    assert pdf.status_code == 200 and b"OLD" in pdf.content


def test_duplicate_request_returns_the_active_operation(test_app, gen):
    gen.gate = threading.Event()
    mid, _ = asyncio.run(_seed())
    try:
        first = test_app.post(f"/api/documents/{mid}/regenerate", json={}).json()
        second = test_app.post(f"/api/documents/{mid}/regenerate", json={}).json()
        assert second["operation_id"] == first["operation_id"]
    finally:
        gen.gate.set()
    assert _wait(test_app, first["operation_id"])["state"] == "succeeded"
    assert gen.calls == 1


def test_unforced_request_reuses_a_succeeded_operation_for_unchanged_inputs(test_app, gen):
    mid, _ = asyncio.run(_seed())
    first = _wait(
        test_app, test_app.post(f"/api/documents/{mid}/regenerate", json={}).json()["operation_id"]
    )

    again = test_app.post(f"/api/documents/{mid}/regenerate", json={})

    assert again.json()["operation_id"] == first["id"] and again.json()["state"] == "succeeded"
    assert gen.calls == 1
    forced = test_app.post(f"/api/documents/{mid}/regenerate", json={"force": True}).json()
    assert forced["operation_id"] != first["id"]
    assert _wait(test_app, forced["operation_id"])["state"] == "succeeded"
    assert gen.calls == 2


def test_accepted_work_survives_a_restart(gen):
    """An operation committed as queued/running before a crash is resumed at startup."""
    from backend.database import AsyncSessionLocal
    from backend.main import app
    from backend.models.operation import DocumentOperation

    mid, _ = asyncio.run(_seed())

    async def _orphan() -> int:
        async with AsyncSessionLocal() as db:
            op = DocumentOperation(
                kind="regenerate_cv", job_match_id=mid, state="running", input_version="x"
            )
            db.add(op)
            await db.commit()
            return op.id

    op_id = asyncio.run(_orphan())
    with TestClient(app) as client:
        assert _wait(client, op_id)["state"] == "succeeded"


def test_unknown_operation_is_404(test_app):
    assert test_app.get("/api/documents/operations/999999").status_code == 404
