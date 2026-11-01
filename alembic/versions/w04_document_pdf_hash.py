"""w04 document pdf hash

Adds ``tailored_documents.pdf_sha256`` (W04 artifact identity) and backfills
it for rows whose PDF still exists. Rows whose PDF is missing or invalid keep
NULL: selection reports them unavailable and asks for regeneration — no other
document is substituted.

Additive and nullable, so the previous application version runs unchanged on
the upgraded schema (rollback = previous version, no downgrade needed).

Revision ID: w04_document_pdf_hash
Revises: t2b4_indexes
Create Date: 2026-11-01 14:00:00.000000

"""
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w04_document_pdf_hash"
down_revision: Union[str, Sequence[str], None] = "t2b4_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tailored_documents", sa.Column("pdf_sha256", sa.String(length=64), nullable=True))

    from backend.config import settings
    from backend.latex.artifacts import pdf_sha256, resolve_data_path

    data_dir = Path(settings.jobpilot_data_dir)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, pdf_path FROM tailored_documents WHERE pdf_path IS NOT NULL")
    ).fetchall()
    for doc_id, raw in rows:
        digest = pdf_sha256(resolve_data_path(raw, data_dir))
        if digest:
            conn.execute(
                sa.text("UPDATE tailored_documents SET pdf_sha256 = :h WHERE id = :id"),
                {"h": digest, "id": doc_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("tailored_documents") as batch:
        batch.drop_column("pdf_sha256")
