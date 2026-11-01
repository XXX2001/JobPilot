"""w06 submission evidence

* ``applications.status`` accepts ``awaiting_submission`` (prepared; the user
  submits) and ``needs_verification`` (a submit may have happened, no proof).
* ``applications.confirmation_source`` / ``confirmation_evidence`` record how
  an ``applied`` row is known. Existing applied rows become
  ``legacy_unknown`` — their history is kept, not rewritten or upgraded to a
  false certainty.

Rollback: the previous version reads the new columns as absent and never
writes the new statuses, but its CHECK-free reads of a ``needs_verification``
row would show the raw string — restore from backup if that matters.

Revision ID: w06_submission_evidence
Revises: w05_document_operations
Create Date: 2026-11-01 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w06_submission_evidence"
down_revision: Union[str, Sequence[str], None] = "w05_document_operations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CK_STATUS = (
    "status IN ('pending', 'applied', 'cancelled', 'failed', "
    "'awaiting_submission', 'needs_verification', "
    "'interview', 'offer', 'rejected')"
)
CK_STATUS_OLD = (
    "status IN ('pending', 'applied', 'cancelled', 'failed', "
    "'interview', 'offer', 'rejected')"
)
CK_SOURCE = (
    "confirmation_source IS NULL OR confirmation_source IN "
    "('destination', 'user', 'legacy_unknown')"
)


def upgrade() -> None:
    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.add_column(sa.Column("confirmation_source", sa.String(), nullable=True))
        batch.add_column(sa.Column("confirmation_evidence", sa.Text(), nullable=True))
        batch.drop_constraint("ck_applications_status", type_="check")
        batch.create_check_constraint("ck_applications_status", CK_STATUS)
        batch.create_check_constraint("ck_applications_confirmation_source", CK_SOURCE)
    # Includes post-submission recruiting states: they imply an earlier applied row.
    op.execute(
        "UPDATE applications SET confirmation_source = 'legacy_unknown' "
        "WHERE status IN ('applied', 'interview', 'offer', 'rejected')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE applications SET status = 'pending' "
        "WHERE status IN ('awaiting_submission', 'needs_verification')"
    )
    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.drop_constraint("ck_applications_confirmation_source", type_="check")
        batch.drop_constraint("ck_applications_status", type_="check")
        batch.create_check_constraint("ck_applications_status", CK_STATUS_OLD)
        batch.drop_column("confirmation_evidence")
        batch.drop_column("confirmation_source")
