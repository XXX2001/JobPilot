"""T2a — Alembic is in sync with the models (no autogenerate diff)."""
from __future__ import annotations

import tempfile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from backend.config import PROJECT_ROOT
from backend.models import Base


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_is_clean_and_in_sync():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        # ``alembic/env.py`` runs in async mode, so the upgrade needs an async
        # driver URL. ``compare_metadata`` reflects via a plain sync engine on
        # the same file.
        command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{tmp.name}"), "head")

        sync_engine = create_engine(f"sqlite:///{tmp.name}")
        with sync_engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            diff = compare_metadata(mc, Base.metadata)
        sync_engine.dispose()

    # A non-empty diff means the models and the migration head disagree.
    assert diff == [], f"models/migrations out of sync: {diff}"


def test_w04_backfills_pdf_hash_for_existing_documents(tmp_path, monkeypatch):
    """Upgrading an old-schema DB hashes PDFs that exist; missing/invalid stay NULL."""
    import hashlib

    from sqlalchemy import text

    from backend.config import settings

    monkeypatch.setattr(settings, "jobpilot_data_dir", str(tmp_path))
    good = tmp_path / "cvs" / "1_x" / "cv.pdf"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"%PDF-1.4 hello")
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")

    db = tmp_path / "old.db"
    cfg = _alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "t2b4_indexes")
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("INSERT INTO jobs (id, title, company, url, dedup_hash, scraped_at) VALUES (1, 't', 'c', 'u', 'h', '2026-01-01')"))
        c.execute(text("INSERT INTO job_matches (id, job_id, score, status, matched_at) VALUES (1, 1, 50, 'new', '2026-01-01')"))
        for i, p in enumerate(["cvs/1_x/cv.pdf", str(tmp_path / "bad.pdf"), "cvs/gone.pdf"], 1):
            c.execute(
                text("INSERT INTO tailored_documents (id, job_match_id, doc_type, pdf_path, created_at) VALUES (:i, 1, 'cv', :p, '2026-01-01')"),
                {"i": i, "p": p},
            )
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        hashes = dict(c.execute(text("SELECT id, pdf_sha256 FROM tailored_documents")).fetchall())
    eng.dispose()
    assert hashes == {1: hashlib.sha256(good.read_bytes()).hexdigest(), 2: None, 3: None}
