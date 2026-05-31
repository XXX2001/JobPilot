"""t2b2 unique constraints on natural keys + duplicate collapse

Enforce two natural-key uniqueness invariants at the DB level (defence in
depth — the application code already de-duplicates, but the constraint makes
the invariant impossible to violate):

* ``job_sources.name`` UNIQUE — a source name identifies the source.
* ``(job_matches.job_id, job_matches.batch_date)`` UNIQUE — at most one match
  per job per daily batch. ``batch_date`` is nullable and SQLite treats
  multiple NULLs as DISTINCT, so dateless/ad-hoc matches stay unconstrained.

SQLite cannot ``ALTER TABLE ADD CONSTRAINT``, so each unique constraint is
added by recreating its table in batch mode (``recreate="always"``); the batch
reflects and preserves the existing columns, indexes, foreign keys and CHECK
constraints added by the earlier T2 migrations. Existing dirty data is
collapsed BEFORE the constraints are added so they hold on real user DBs (a
fresh test DB is empty, making the cleanup a harmless no-op there).

The old non-unique indexes (``ix_job_sources_name`` and
``ix_job_matches_job_id_batch_date``) are dropped: each unique constraint
brings its own index, so they are redundant. Index names are never referenced
in queries, so dropping them is safe.

The constraint names below are kept byte-for-byte identical to the
``UniqueConstraint`` declarations in the model ``__table_args__`` so the model
and the migration agree (``compare_metadata`` is the canary).

Revision ID: t2b2_unique_keys
Revises: t2b1_enum_checks
Create Date: 2026-05-31 15:30:00.000000

"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't2b2_unique_keys'
down_revision: Union[str, Sequence[str], None] = 't2b1_enum_checks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    def _run(description: str, sql: str) -> None:
        """Execute a cleanup statement and log how many rows it touched.

        A fresh test DB is empty so every count is 0 (silent); on a real user
        DB a non-zero count leaves an audit trail of the duplicates that were
        collapsed to satisfy the new unique constraints.
        """
        result = bind.execute(sa.text(sql))
        if result.rowcount and result.rowcount > 0:
            logger.info("t2b2: %s touched %d row(s)", description, result.rowcount)

    # ── 1. Collapse duplicate job_sources.name ──────────────────────────────
    #    Survivor = smallest id per name. Repoint jobs.source_id of the loser
    #    rows to the survivor BEFORE deleting the losers, so no job is orphaned.
    _run(
        "jobs.source_id repointed to surviving job_source",
        "UPDATE jobs SET source_id = ("
        "    SELECT MIN(s2.id) FROM job_sources s2"
        "    WHERE s2.name = ("
        "        SELECT s1.name FROM job_sources s1 WHERE s1.id = jobs.source_id"
        "    )"
        ") "
        "WHERE source_id IS NOT NULL "
        "AND source_id NOT IN (SELECT MIN(id) FROM job_sources GROUP BY name)",
    )
    _run(
        "duplicate job_sources rows deleted",
        "DELETE FROM job_sources "
        "WHERE id NOT IN (SELECT MIN(id) FROM job_sources GROUP BY name)",
    )

    # ── 2. Collapse duplicate (job_matches.job_id, batch_date) ───────────────
    #    Survivor = MAX(id) per (job_id, batch_date) group (the most recent
    #    match). Only non-NULL batch dates are deduped — NULLs are DISTINCT.
    #    FK enforcement is OFF during ``alembic upgrade`` (env.py's engine has
    #    no ``PRAGMA foreign_keys=ON`` listener), so deleting a loser does NOT
    #    cascade. We therefore explicitly repoint the loser rows' children to
    #    the surviving match BEFORE deleting them — mirroring the job_sources
    #    cleanup above — so no dangling references are left behind.
    #
    #    A "loser" is a job_matches row with a non-NULL batch_date that is not
    #    the MAX(id) of its group; its survivor is the MAX(id) of the same
    #    (job_id, batch_date) group (correlated subquery).
    _loser_predicate = (
        "batch_date IS NOT NULL "
        "AND id NOT IN ("
        "    SELECT MAX(id) FROM job_matches"
        "    WHERE batch_date IS NOT NULL"
        "    GROUP BY job_id, batch_date"
        ")"
    )
    _survivor_for_loser = (
        "SELECT MAX(m2.id) FROM job_matches m2 "
        "WHERE m2.job_id = ("
        "    SELECT m1.job_id FROM job_matches m1 WHERE m1.id = {child}.job_match_id"
        ") "
        "AND m2.batch_date = ("
        "    SELECT m1.batch_date FROM job_matches m1 WHERE m1.id = {child}.job_match_id"
        ")"
    )
    _run(
        "tailored_documents repointed to surviving job_match",
        f"UPDATE tailored_documents SET job_match_id = ("
        f"    {_survivor_for_loser.format(child='tailored_documents')}"
        f") "
        f"WHERE job_match_id IN (SELECT id FROM job_matches WHERE {_loser_predicate})",
    )
    _run(
        "applications repointed to surviving job_match",
        f"UPDATE applications SET job_match_id = ("
        f"    {_survivor_for_loser.format(child='applications')}"
        f") "
        f"WHERE job_match_id IN (SELECT id FROM job_matches WHERE {_loser_predicate})",
    )
    _run(
        "duplicate (job_id, batch_date) job_matches deleted",
        f"DELETE FROM job_matches WHERE {_loser_predicate}",
    )

    # ── 3. Swap the redundant non-unique indexes for the unique constraints ──
    #    Drop the old index first, then recreate the table in batch mode with
    #    the unique constraint; the batch preserves all other columns, indexes,
    #    FKs and CHECKs.
    op.drop_index("ix_job_sources_name", table_name="job_sources")
    with op.batch_alter_table("job_sources", recreate="always") as batch:
        batch.create_unique_constraint("uq_job_sources_name", ["name"])

    op.drop_index("ix_job_matches_job_id_batch_date", table_name="job_matches")
    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.create_unique_constraint(
            "uq_job_matches_job_id_batch_date", ["job_id", "batch_date"]
        )


def downgrade() -> None:
    """Downgrade schema — drop the unique constraints, restore the old indexes.

    The duplicate-collapse in ``upgrade()`` is intentionally NOT reversed:
    deleted rows cannot be reconstructed and the surviving rows are correct.
    """
    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.drop_constraint("uq_job_matches_job_id_batch_date", type_="unique")
    op.create_index(
        "ix_job_matches_job_id_batch_date", "job_matches", ["job_id", "batch_date"]
    )

    with op.batch_alter_table("job_sources", recreate="always") as batch:
        batch.drop_constraint("uq_job_sources_name", type_="unique")
    op.create_index("ix_job_sources_name", "job_sources", ["name"])
