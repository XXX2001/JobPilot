"""m02 city dedup key, first_polled_at

``dedup_key`` now hashes only the city (the location's first comma-separated
token), because sources spell the rest differently and the same posting from a
board and from LinkedIn was stored twice. ``jobs.dedup_hash`` is recomputed (and
restored on downgrade) so stored rows are still found. ``job_sources`` gains
``first_polled_at``: the poller does not notify postings older than the seed.

Revision ID: m02_city_dedup_first_polled
Revises: m01_monitor_state
Create Date: 2026-11-14 18:00:00.000000

"""
import hashlib
import re
from typing import Callable, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m02_city_dedup_first_polled"
down_revision: Union[str, Sequence[str], None] = "m01_monitor_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _city_key(company: str | None, title: str | None, location: str | None) -> str:
    """Frozen copy of ``backend.scraping.deduplicator.dedup_key`` as of this revision."""
    city = (location or "").split(",")[0]
    return hashlib.md5(f"{_norm(company)}|{_norm(title)}|{_norm(city)}".encode()).hexdigest()


def _m01_key(company: str | None, title: str | None, location: str | None) -> str:
    """``dedup_key`` as of m01: the whole location."""
    return hashlib.md5(f"{_norm(company)}|{_norm(title)}|{_norm(location)}".encode()).hexdigest()


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
    for job_id, taken in _rehash(_city_key):
        # Same handling as m01: the row holding the hash is the one lookups reach,
        # so it takes its twin's matches (unless one-per-(job, batch_date) forbids).
        conn.execute(
            sa.text(
                "UPDATE OR IGNORE job_matches SET job_id = "
                "(SELECT id FROM jobs WHERE dedup_hash = :h) WHERE job_id = :id"
            ),
            {"h": taken, "id": job_id},
        )
    with op.batch_alter_table("job_sources") as batch:
        batch.add_column(sa.Column("first_polled_at", sa.DateTime(), nullable=True))
    # Sources already seeded: their first poll is their earliest stored job.
    conn.execute(
        sa.text(
            "UPDATE job_sources SET first_polled_at = COALESCE("
            "(SELECT MIN(first_seen_at) FROM jobs WHERE jobs.source_id = job_sources.id), "
            "last_scraped_at) WHERE last_scraped_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("job_sources") as batch:
        batch.drop_column("first_polled_at")
    _rehash(_m01_key)  # moved matches stay with the surviving twin
