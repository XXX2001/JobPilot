"""w05 document operations

Creates ``document_operations`` — durable document-generation work (W05).
New table only, so the previous application version ignores it (rollback =
previous version).

Revision ID: w05_document_operations
Revises: w04_document_pdf_hash
Create Date: 2026-11-01 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w05_document_operations"
down_revision: Union[str, Sequence[str], None] = "w04_document_pdf_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("job_match_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="ck_document_operations_state"
        ),
        sa.CheckConstraint("kind IN ('regenerate_cv')", name="ck_document_operations_kind"),
        sa.ForeignKeyConstraint(["job_match_id"], ["job_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["tailored_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_operations_job_match_id", "document_operations", ["job_match_id"], unique=False
    )
    op.create_index(
        "uq_document_operations_active",
        "document_operations",
        ["kind", "job_match_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_document_operations_active", table_name="document_operations")
    op.drop_index("ix_document_operations_job_match_id", table_name="document_operations")
    op.drop_table("document_operations")
