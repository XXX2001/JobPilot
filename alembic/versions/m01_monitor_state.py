"""m01 monitor state

Discovery monitor schema (spec D2): a job's identity is ``(source_id,
external_id)`` when the source provides a stable ID — a partial unique index,
so legacy rows without an ``external_id`` stay unconstrained. Poll state
(interval, next due, ETag, failures, window) lives on the ``job_sources`` row.
The columns are additive. ``jobs.dedup_hash`` is recomputed with the
``dedup_key`` normalisation (and restored on downgrade), because the lookup
formula changed with this revision: an un-rehashed row with stray whitespace
would never be found again and its job would be stored and queued a second time.

Revision ID: m01_monitor_state
Revises: w11_quota_reservation
Create Date: 2026-11-14 10:00:00.000000

"""
import hashlib
import re
from typing import Callable, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m01_monitor_state"
down_revision: Union[str, Sequence[str], None] = "w11_quota_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HAS_EXTERNAL_ID = "external_id IS NOT NULL AND external_id != ''"




def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _dedup_key(company: str | None, title: str | None, location: str | None) -> str:
    """Frozen copy of ``backend.scraping.deduplicator.dedup_key`` as of this revision."""
    return hashlib.md5(f"{_norm(company)}|{_norm(title)}|{_norm(location)}".encode()).hexdigest()


def _legacy_key(company: str | None, title: str | None, location: str | None) -> str:
    """What the batch runner and ``/api/jobs/search`` stored before this revision."""
    return hashlib.md5(f"{company}|{title}|{location}".lower().encode()).hexdigest()


def _rehash(key: Callable[..., str]) -> list[tuple[int, str]]:
    """Recompute ``jobs.dedup_hash``; return ``(job_id, hash)`` for rows another row blocks."""
    conn = op.get_bind()
    blocked: list[tuple[int, str]] = []
    rows = conn.execute(
        sa.text("SELECT id, company, title, location, dedup_hash FROM jobs ORDER BY id")
    ).fetchall()
    for job_id, company, title, location, old in rows:
        new = key(company, title, location)
        if new == old:
            continue
        # OR IGNORE: the hash is unique, so a row whose new hash is taken keeps its old one.
        done = conn.execute(
            sa.text("UPDATE OR IGNORE jobs SET dedup_hash = :h WHERE id = :id"),
            {"h": new, "id": job_id},
        )
        if not done.rowcount:
            blocked.append((job_id, new))
    return blocked


def upgrade() -> None:
    conn = op.get_bind()
    for job_id, taken in _rehash(_dedup_key):
        # Two rows for one posting. Lookups now reach only the row holding the
        # hash, so it takes the other's matches — an "applied" on the twin must
        # keep the job out of the queue. A match that would break the one-per-
        # (job, batch_date) constraint stays where it is.
        conn.execute(
            sa.text(
                "UPDATE OR IGNORE job_matches SET job_id = "
                "(SELECT id FROM jobs WHERE dedup_hash = :h) WHERE job_id = :id"
            ),
            {"h": taken, "id": job_id},
        )
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
    _rehash(_legacy_key)  # moved matches stay with the surviving twin
