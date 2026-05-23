"""add_initial_indexes

Revision ID: 41441908fc29
Revises: df6eea4756c3
Create Date: 2026-05-22 00:00:00.000000

Adds indexes on FK-like columns and commonly-filtered/sorted columns. The
codebase previously had zero indexes, so every list query was a full-table
scan. This migration is additive only -- no FK constraints, no enums, no
re-baselining.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "41441908fc29"
down_revision: Union[str, Sequence[str], None] = "df6eea4756c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table_name, [columns]) — kept in a list so upgrade/downgrade
# stay in lockstep and naming is deterministic.
_INDEXES: list[tuple[str, str, list[str]]] = [
    # job_sources: filtered by name/type, and morning_batch filters enabled=True.
    ("ix_job_sources_name", "job_sources", ["name"]),
    ("ix_job_sources_type", "job_sources", ["type"]),
    ("ix_job_sources_enabled", "job_sources", ["enabled"]),
    # jobs: source_id is a FK-like join column; scraped_at drives the
    # default `ORDER BY scraped_at DESC` list query.
    ("ix_jobs_source_id", "jobs", ["source_id"]),
    ("ix_jobs_scraped_at", "jobs", ["scraped_at"]),
    # job_matches: status is filtered (`status == 'new'`) by the queue.
    ("ix_job_matches_status", "job_matches", ["status"]),
    # Compound: covers `WHERE job_id = ? ORDER BY matched_at DESC` (the
    # repeated per-job match lookup) and pure job_id joins.
    (
        "ix_job_matches_job_id_matched_at",
        "job_matches",
        ["job_id", "matched_at"],
    ),
    # Compound: covers the morning-batch dedup check
    # `WHERE job_id = ? AND batch_date = today`.
    (
        "ix_job_matches_job_id_batch_date",
        "job_matches",
        ["job_id", "batch_date"],
    ),
    # applications: job_match_id is FK-like (joined in every list query);
    # status is filtered; created_at orders the list; applied_at filters
    # the daily-limit check.
    ("ix_applications_job_match_id", "applications", ["job_match_id"]),
    ("ix_applications_status", "applications", ["status"]),
    ("ix_applications_created_at", "applications", ["created_at"]),
    ("ix_applications_applied_at", "applications", ["applied_at"]),
    # application_events: per-application timeline lookup.
    (
        "ix_application_events_application_id_event_date",
        "application_events",
        ["application_id", "event_date"],
    ),
    # tailored_documents: looked up by job_match_id, listed by created_at.
    (
        "ix_tailored_documents_job_match_id",
        "tailored_documents",
        ["job_match_id"],
    ),
    (
        "ix_tailored_documents_created_at",
        "tailored_documents",
        ["created_at"],
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    for index_name, table_name, columns in _INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    """Downgrade schema."""
    for index_name, table_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table_name)
