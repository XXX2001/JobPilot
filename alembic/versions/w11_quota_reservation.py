"""w11 quota reservation

Adds ``applications.quota_reserved_at`` — the automated-submission
reservation the daily guard counts (W11), separate from ``applied_at``
(which W06 leaves empty for unverified submissions). Backfill: rows that
were countable before (pending / applied / needs_verification with an
``applied_at``) keep their slot on the same instant. Nullable additive
column: the previous version runs unchanged.

Revision ID: w11_quota_reservation
Revises: w06_submission_evidence
Create Date: 2026-11-01 17:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w11_quota_reservation"
down_revision: Union[str, Sequence[str], None] = "w06_submission_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("quota_reserved_at", sa.DateTime(), nullable=True))
    op.create_index("ix_applications_quota_reserved_at", "applications", ["quota_reserved_at"])
    op.execute(
        "UPDATE applications SET quota_reserved_at = applied_at "
        "WHERE applied_at IS NOT NULL AND method != 'manual' "
        "AND status IN ('pending', 'applied', 'needs_verification')"
    )


def downgrade() -> None:
    op.drop_index("ix_applications_quota_reserved_at", table_name="applications")
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("quota_reserved_at")
