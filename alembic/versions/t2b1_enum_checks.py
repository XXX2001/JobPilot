"""t2b1 enum CHECK constraints + legacy status migration

Tighten the enum-like (string-vocabulary) columns with named ``CHECK``
constraints so the DB rejects out-of-vocabulary values, and migrate the
legacy ``applications.status`` aliases (``manual`` / ``assisted``) to the
canonical ``applied`` value.

SQLite cannot ``ALTER TABLE ADD CONSTRAINT``, so each CHECK is added by
recreating its table in batch mode (``recreate="always"``); the batch
reflects and preserves the existing columns, indexes and foreign keys
added in T2a. Existing rows are normalised to a safe in-vocabulary value
BEFORE the CHECKs are added so the constraints hold on real user DBs (a
fresh test DB is empty, making the cleanup a harmless no-op there).

The CHECK SQL strings below are kept byte-for-byte identical to the
``CheckConstraint`` declarations in the model ``__table_args__`` so the
model and the migration agree.

Revision ID: t2b1_enum_checks
Revises: e5a65a3427cf
Create Date: 2026-05-31 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't2b1_enum_checks'
down_revision: Union[str, Sequence[str], None] = 'e5a65a3427cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Canonical CHECK conditions (must match the model __table_args__) ─────────
CK_APPLICATIONS_STATUS = (
    "status IN ('pending', 'applied', 'cancelled', 'failed', "
    "'interview', 'offer', 'rejected')"
)
CK_APPLICATIONS_METHOD = "method IN ('auto', 'assisted', 'manual')"
CK_JOB_MATCHES_STATUS = (
    "status IN ('new', 'skipped', 'applying', 'applied', "
    "'rejected', 'selected')"
)
CK_TAILORED_DOCUMENTS_DOC_TYPE = "doc_type IN ('cv', 'letter')"
CK_APPLICATION_CORRESPONDENCE_DIRECTION = "direction IN ('inbound', 'outbound')"
CK_GMAIL_MESSAGES_CATEGORY = (
    "category IS NULL OR category IN ('noise', 'rejection', 'offer', "
    "'interview_invite', 'ats_ack', 'unknown')"
)
CK_SEARCH_SETTINGS_CV_SENSITIVITY = (
    "cv_modification_sensitivity IN ('conservative', 'balanced', 'aggressive')"
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. Migrate legacy Application.status aliases to the canonical value.
    #    Mirrors ``scripts/migrate_legacy_applied.py`` + LEGACY_APPLIED_ALIASES.
    bind.execute(sa.text(
        "UPDATE applications SET status = 'applied' "
        "WHERE status IN ('manual', 'assisted')"
    ))

    # 2. Normalise any remaining out-of-vocabulary stragglers to a safe
    #    fallback so the new CHECKs hold on existing data. Each fallback is
    #    the column's canonical "neutral" value:
    #      - applications.status      -> 'pending'  (un-acted draft state)
    #      - applications.method      -> 'manual'   (the safest apply path)
    #      - job_matches.status       -> 'new'      (the table default)
    #      - tailored_documents.doc_type -> 'cv'    (the primary document)
    #      - application_correspondence.direction -> 'inbound' (received mail)
    #      - gmail_messages.category  -> 'unknown'  (only non-NULL values)
    #      - search_settings.cv_modification_sensitivity -> 'balanced' (default)
    bind.execute(sa.text(
        f"UPDATE applications SET status = 'pending' "
        f"WHERE NOT ({CK_APPLICATIONS_STATUS})"
    ))
    bind.execute(sa.text(
        f"UPDATE applications SET method = 'manual' "
        f"WHERE NOT ({CK_APPLICATIONS_METHOD})"
    ))
    bind.execute(sa.text(
        f"UPDATE job_matches SET status = 'new' "
        f"WHERE NOT ({CK_JOB_MATCHES_STATUS})"
    ))
    bind.execute(sa.text(
        f"UPDATE tailored_documents SET doc_type = 'cv' "
        f"WHERE NOT ({CK_TAILORED_DOCUMENTS_DOC_TYPE})"
    ))
    bind.execute(sa.text(
        f"UPDATE application_correspondence SET direction = 'inbound' "
        f"WHERE NOT ({CK_APPLICATION_CORRESPONDENCE_DIRECTION})"
    ))
    bind.execute(sa.text(
        "UPDATE gmail_messages SET category = 'unknown' "
        "WHERE category IS NOT NULL AND category NOT IN "
        "('noise', 'rejection', 'offer', 'interview_invite', 'ats_ack', 'unknown')"
    ))
    bind.execute(sa.text(
        f"UPDATE search_settings SET cv_modification_sensitivity = 'balanced' "
        f"WHERE NOT ({CK_SEARCH_SETTINGS_CV_SENSITIVITY})"
    ))

    # 3. Add the CHECKs by recreating each affected table (SQLite batch). One
    #    batch block per table; the batch reflects and re-creates the existing
    #    columns/indexes/foreign-keys, so nothing else is dropped.
    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.create_check_constraint("ck_applications_status", CK_APPLICATIONS_STATUS)
        batch.create_check_constraint("ck_applications_method", CK_APPLICATIONS_METHOD)
    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.create_check_constraint("ck_job_matches_status", CK_JOB_MATCHES_STATUS)
    with op.batch_alter_table("tailored_documents", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_tailored_documents_doc_type", CK_TAILORED_DOCUMENTS_DOC_TYPE
        )
    with op.batch_alter_table("application_correspondence", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_application_correspondence_direction",
            CK_APPLICATION_CORRESPONDENCE_DIRECTION,
        )
    with op.batch_alter_table("gmail_messages", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_gmail_messages_category", CK_GMAIL_MESSAGES_CATEGORY
        )
    with op.batch_alter_table("search_settings", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_search_settings_cv_sensitivity", CK_SEARCH_SETTINGS_CV_SENSITIVITY
        )


def downgrade() -> None:
    """Downgrade schema — drop the CHECKs by recreating the tables without them.

    The legacy ``status`` data migration in ``upgrade()`` is intentionally
    NOT reversed: ``applied`` is the correct canonical value and there is no
    way to know which rows were originally ``manual`` vs ``assisted``.
    """
    for table, constraints in (
        ("search_settings", ("ck_search_settings_cv_sensitivity",)),
        ("gmail_messages", ("ck_gmail_messages_category",)),
        (
            "application_correspondence",
            ("ck_application_correspondence_direction",),
        ),
        ("tailored_documents", ("ck_tailored_documents_doc_type",)),
        ("job_matches", ("ck_job_matches_status",)),
        ("applications", ("ck_applications_status", "ck_applications_method")),
    ):
        with op.batch_alter_table(table, recreate="always") as batch:
            for name in constraints:
                batch.drop_constraint(name, type_="check")
