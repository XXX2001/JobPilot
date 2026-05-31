"""t2b4 covering indexes for hot query paths

Add composite indexes that cover the application's hottest read paths so the
DB can satisfy them with an index scan instead of a full table scan:

* ``job_matches (status, batch_date, score)`` — the queue listing filters by
  ``status`` and orders by ``batch_date`` / ``score`` (``backend/api/queue.py``).
* ``tailored_documents (job_match_id, doc_type, created_at)`` — document
  lookups by match + type + recency (``backend/api/documents.py``).
* ``applications (status, created_at)`` — the tracker listing filters by
  ``status`` and orders by ``created_at`` (``backend/api/applications.py``).

These are purely ADDITIVE indexes: no table rebuild is needed, so plain
``op.create_index`` / ``op.drop_index`` are used (no batch recreate). The
existing single-column indexes are intentionally kept — redundant indexes are
cheap and other queries may rely on the standalone ones.

The index names below are kept byte-for-byte identical to the ``Index``
declarations in the model ``__table_args__`` so the model and the migration
agree (``compare_metadata`` is the canary).

Revision ID: t2b4_indexes
Revises: t2b3_not_null
Create Date: 2026-05-31 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 't2b4_indexes'
down_revision: Union[str, Sequence[str], None] = 't2b3_not_null'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add the covering indexes."""
    op.create_index(
        "ix_job_matches_status_batch_date_score",
        "job_matches",
        ["status", "batch_date", "score"],
    )
    op.create_index(
        "ix_tailored_documents_match_doc_created",
        "tailored_documents",
        ["job_match_id", "doc_type", "created_at"],
    )
    op.create_index(
        "ix_applications_status_created",
        "applications",
        ["status", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema — drop the covering indexes."""
    op.drop_index("ix_applications_status_created", table_name="applications")
    op.drop_index(
        "ix_tailored_documents_match_doc_created", table_name="tailored_documents"
    )
    op.drop_index(
        "ix_job_matches_status_batch_date_score", table_name="job_matches"
    )
