"""m01 monitor state

Discovery monitor schema (spec D2): a job's identity is ``(source_id,
external_id)`` when the source provides a stable ID — a partial unique index,
so legacy rows without an ``external_id`` stay unconstrained. Poll state
(interval, next due, ETag, failures, window) lives on the ``job_sources`` row.
All additive; the previous version runs unchanged.

Revision ID: m01_monitor_state
Revises: w11_quota_reservation
Create Date: 2026-11-14 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m01_monitor_state"
down_revision: Union[str, Sequence[str], None] = "w11_quota_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HAS_EXTERNAL_ID = "external_id IS NOT NULL AND external_id != ''"


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("first_seen_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.create_index(
        "uq_jobs_source_external",
        "jobs",
        ["source_id", "external_id"],
        unique=True,
        sqlite_where=sa.text(_HAS_EXTERNAL_ID),
    )
    with op.batch_alter_table("job_sources") as batch:
        batch.add_column(sa.Column("poll_interval_s", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("next_poll_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("etag", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("last_error", sa.String(), nullable=True))
        batch.add_column(sa.Column("window_hours", sa.Integer(), nullable=True))
    op.create_index("ix_job_sources_next_poll_at", "job_sources", ["next_poll_at"])


def downgrade() -> None:
    op.drop_index("ix_job_sources_next_poll_at", table_name="job_sources")
    with op.batch_alter_table("job_sources") as batch:
        for col in (
            "window_hours", "last_error", "fail_count", "etag", "next_poll_at", "poll_interval_s"
        ):
            batch.drop_column(col)
    op.drop_index("uq_jobs_source_external", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("last_seen_at")
        batch.drop_column("first_seen_at")
