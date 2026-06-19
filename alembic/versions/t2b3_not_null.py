"""t2b3 NOT NULL + conditional CHECK on always-set columns

Tighten columns that are de-facto always set so the DB rejects the NULL /
under-specified rows the application never legitimately writes:

* ``tailored_documents.job_match_id`` -> NOT NULL (a tailored document always
  belongs to a concrete match).
* ``jobs.dedup_hash`` -> NOT NULL (the dedup invariant; stays UNIQUE).
* ``applications`` gains a conditional CHECK
  (``method = 'manual' OR job_match_id IS NOT NULL``): only the manual-apply
  path legitimately creates an application without a match.
* ``job_matches.job_id`` / ``job_matches.status`` -> NOT NULL (FK / defaulted).
* ``application_events.application_id`` -> NOT NULL (FK, always set).

SQLite cannot ``ALTER TABLE ... SET NOT NULL`` / ``ADD CONSTRAINT``, so each
table is recreated in batch mode (``recreate="always"``); the batch reflects
and preserves the existing columns, indexes, foreign keys, unique constraints
and CHECKs added by the earlier T2 migrations. Each affected table is recreated
exactly once, doing all its alterations together.

FK ENFORCEMENT IS OFF during ``alembic upgrade`` (``env.py``'s engine has no
``PRAGMA foreign_keys=ON`` listener), so a DELETE here does NOT cascade /
SET NULL. Existing dirty data is therefore cleaned up BEFORE the constraints
are added, handling children explicitly so nothing is left dangling:

* orphan ``tailored_documents`` (NULL ``job_match_id``) have no parent and no
  children, so they are simply DELETED.
* NULL ``jobs.dedup_hash`` rows are BACKFILLED (deleting them would orphan
  ``job_matches`` with FK enforcement off). The hash replicates the app's
  ``md5(lower(company|title|location))``; SQLite has no ``md5()`` builtin so it
  is computed in Python, one UPDATE per row. A computed hash that collides with
  an existing one (a genuine duplicate job) is disambiguated with a ``-{id}``
  suffix rather than deleting the row, so no job — and thus no ``job_matches``
  child — is lost.
* non-manual ``applications`` with no match are NORMALISED to
  ``method = 'manual'`` (the safest apply path), preserving the row.

The constraint name and CHECK SQL below are kept byte-for-byte identical to the
``CheckConstraint`` declaration in the model ``__table_args__`` so the model and
the migration agree (``compare_metadata`` is the canary).

Revision ID: t2b3_not_null
Revises: t2b2_unique_keys
Create Date: 2026-05-31 17:00:00.000000

"""
import hashlib
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't2b3_not_null'
down_revision: Union[str, Sequence[str], None] = 't2b2_unique_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


# ── Conditional CHECK condition (must match the model __table_args__) ────────
CK_APPLICATIONS_JOB_MATCH_REQUIRED = "method = 'manual' OR job_match_id IS NOT NULL"


def _dedup_hash(company, title, location) -> str:
    """Replicate the application's dedup hash BYTE-FOR-BYTE.

    Mirrors ``hashlib.md5(f"{company}|{title}|{location}".lower())`` from
    ``backend/scheduler/batch_runner.py`` (``_store_matches``) and
    ``backend/api/jobs.py`` — same field order, same single ``.lower()`` over
    the whole joined string, and the SAME raw f-string interpolation with NO
    ``or ''`` coalescing. A NULL field comes back from sqlite as Python ``None``
    and stringifies to ``"None"`` -> ``"none"`` after ``.lower()``, exactly as
    the app does, so a backfilled row matches what the app would recompute on
    the next scrape (preserving the dedup invariant).
    """
    key = f"{company}|{title}|{location}".lower()
    return hashlib.md5(key.encode()).hexdigest()


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    def _run(description: str, sql: str) -> None:
        """Execute a cleanup statement and log how many rows it touched.

        A fresh test DB is empty so every count is 0 (silent); on a real user
        DB a non-zero count leaves an audit trail of the rows that were cleaned
        up to satisfy the new constraints.
        """
        result = bind.execute(sa.text(sql))
        if result.rowcount and result.rowcount > 0:
            logger.info("t2b3: %s touched %d row(s)", description, result.rowcount)

    # ── 1. Orphan tailored_documents (NULL job_match_id): no parent, no
    #       children — delete them outright.
    _run(
        "orphan tailored_documents (NULL job_match_id) deleted",
        "DELETE FROM tailored_documents WHERE job_match_id IS NULL",
    )

    # ── 2. Backfill NULL jobs.dedup_hash in Python (SQLite has no md5()).
    #       Deleting these would orphan job_matches (FK enforcement is off), so
    #       we backfill the deterministic hash instead. Genuine duplicates whose
    #       computed hash already exists are disambiguated with a ``-{id}``
    #       suffix to avoid tripping the UNIQUE constraint AND to avoid data loss.
    null_hash_rows = bind.execute(
        sa.text(
            "SELECT id, company, title, location FROM jobs WHERE dedup_hash IS NULL"
        )
    ).fetchall()
    if null_hash_rows:
        assigned: set[str] = {
            row[0]
            for row in bind.execute(
                sa.text("SELECT dedup_hash FROM jobs WHERE dedup_hash IS NOT NULL")
            ).fetchall()
        }
        for job_id, company, title, location in null_hash_rows:
            digest = _dedup_hash(company, title, location)
            if digest in assigned:
                digest = f"{digest}-{job_id}"  # genuine duplicate — disambiguate
            assigned.add(digest)
            bind.execute(
                sa.text("UPDATE jobs SET dedup_hash = :h WHERE id = :id"),
                {"h": digest, "id": job_id},
            )
        logger.info(
            "t2b3: jobs.dedup_hash backfilled %d row(s)", len(null_hash_rows)
        )

    # ── 3. Normalise applications violating the conditional CHECK. A row
    #       violates if it is non-manual yet carries no match; coerce it to the
    #       safest apply path (``manual``) so the row survives.
    _run(
        "applications normalised to method='manual' (no match)",
        "UPDATE applications SET method = 'manual' "
        "WHERE method <> 'manual' AND job_match_id IS NULL",
    )

    # ── 4. Guarded cleanup of job_matches with a NULL job_id. These should not
    #       exist (job_id is a FK that is always set), but if one does, deleting
    #       it would dangle its children (FK enforcement is off). Delete the
    #       tailored_documents children and detach/normalise the applications
    #       children BEFORE deleting the parent match.
    _run(
        "tailored_documents of NULL-job_id job_matches deleted",
        "DELETE FROM tailored_documents WHERE job_match_id IN "
        "(SELECT id FROM job_matches WHERE job_id IS NULL)",
    )
    _run(
        "applications of NULL-job_id job_matches detached + normalised",
        "UPDATE applications SET job_match_id = NULL, method = 'manual' "
        "WHERE job_match_id IN (SELECT id FROM job_matches WHERE job_id IS NULL)",
    )
    _run(
        "job_matches with NULL job_id deleted",
        "DELETE FROM job_matches WHERE job_id IS NULL",
    )

    # ── 5. application_events with a NULL application_id: no children, safe to
    #       delete outright.
    _run(
        "application_events (NULL application_id) deleted",
        "DELETE FROM application_events WHERE application_id IS NULL",
    )

    # ── 6. Recreate each table once (SQLite batch) to apply its NOT NULLs /
    #       CHECK. The batch reflects and re-creates the existing columns,
    #       indexes, FKs, unique constraints and CHECKs, so nothing else changes.
    with op.batch_alter_table("tailored_documents", recreate="always") as batch:
        batch.alter_column(
            "job_match_id", existing_type=sa.Integer(), nullable=False
        )

    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.alter_column(
            "dedup_hash", existing_type=sa.String(), nullable=False
        )

    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_applications_job_match_required",
            CK_APPLICATIONS_JOB_MATCH_REQUIRED,
        )

    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.alter_column("job_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("status", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("application_events", recreate="always") as batch:
        batch.alter_column(
            "application_id", existing_type=sa.Integer(), nullable=False
        )


def downgrade() -> None:
    """Downgrade schema — relax the NOT NULLs and drop the conditional CHECK.

    The data cleanup in ``upgrade()`` is intentionally NOT reversed: deleted
    orphans / duplicates cannot be reconstructed, and the backfilled hashes and
    normalised methods are correct values to keep.
    """
    with op.batch_alter_table("application_events", recreate="always") as batch:
        batch.alter_column(
            "application_id", existing_type=sa.Integer(), nullable=True
        )

    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.alter_column("status", existing_type=sa.String(), nullable=True)
        batch.alter_column("job_id", existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.drop_constraint("ck_applications_job_match_required", type_="check")

    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.alter_column(
            "dedup_hash", existing_type=sa.String(), nullable=True
        )

    with op.batch_alter_table("tailored_documents", recreate="always") as batch:
        batch.alter_column(
            "job_match_id", existing_type=sa.Integer(), nullable=True
        )
