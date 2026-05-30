"""t2a schema catchup and fks

Revision ID: e5a65a3427cf
Revises: e3a1f2b8c9d7
Create Date: 2026-05-30

Brings the Alembic chain in sync with ``Base.metadata`` so Alembic can be the
single source of truth for the schema:

* creates the Gmail tables that were previously only created by
  ``Base.metadata.create_all`` (``gmail_credentials``, ``gmail_messages``,
  ``application_correspondence``);
* adds columns that the runtime ``_migrate_add_columns`` shim used to add, plus
  later-added model columns that drifted from the migration chain;
* drops the dead ``search_settings.batch_time`` column;
* declares the foreign-key constraints on the bare relational columns. SQLite
  cannot ``ALTER TABLE ADD CONSTRAINT`` so the FK-bearing tables are recreated
  via ``op.batch_alter_table(..., recreate="always")``. Orphan rows are removed
  (or NULLed for ``SET NULL`` columns) first so enforcement holds on real data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a65a3427cf'
down_revision: Union[str, Sequence[str], None] = 'e3a1f2b8c9d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. Reconcile existing data so the new FK constraints can be added safely.
    #    CASCADE children: delete orphans. SET NULL parents: null dangling refs.
    bind.execute(sa.text(
        "DELETE FROM application_events WHERE application_id NOT IN "
        "(SELECT id FROM applications)"
    ))
    bind.execute(sa.text(
        "DELETE FROM job_matches WHERE job_id NOT IN (SELECT id FROM jobs)"
    ))
    bind.execute(sa.text(
        "DELETE FROM tailored_documents WHERE job_match_id IS NOT NULL "
        "AND job_match_id NOT IN (SELECT id FROM job_matches)"
    ))
    bind.execute(sa.text(
        "UPDATE applications SET job_match_id = NULL WHERE job_match_id IS NOT NULL "
        "AND job_match_id NOT IN (SELECT id FROM job_matches)"
    ))
    bind.execute(sa.text(
        "UPDATE jobs SET source_id = NULL WHERE source_id IS NOT NULL "
        "AND source_id NOT IN (SELECT id FROM job_sources)"
    ))

    # 2. Catch-up columns that drifted from the chain (some were previously
    #    added by the ad-hoc _migrate_add_columns runtime shim).
    op.add_column("applications", sa.Column("last_correspondence_at", sa.DateTime(), nullable=True))

    op.add_column("jobs", sa.Column("country", sa.String(), nullable=True))

    op.add_column("job_matches", sa.Column("gap_severity", sa.Float(), nullable=True))
    op.add_column("job_matches", sa.Column("ats_score", sa.Float(), nullable=True))
    op.add_column("job_matches", sa.Column("fit_assessment_json", sa.JSON(), nullable=True))

    op.add_column("user_profile", sa.Column("linkedin_url", sa.String(), nullable=True))
    op.add_column("user_profile", sa.Column("driver_license", sa.String(), nullable=True))
    op.add_column("user_profile", sa.Column("mobility", sa.String(), nullable=True))

    op.add_column("search_settings", sa.Column("countries", sa.JSON(), nullable=True))
    op.add_column(
        "search_settings",
        sa.Column(
            "cv_modification_sensitivity", sa.String(), nullable=False,
            server_default="balanced",
        ),
    )
    op.add_column(
        "search_settings",
        sa.Column(
            "cv_tailoring_enabled", sa.Boolean(), nullable=False, server_default="1",
        ),
    )
    op.add_column(
        "search_settings",
        sa.Column(
            "max_results_per_source", sa.Integer(), nullable=False, server_default="20",
        ),
    )
    op.add_column("search_settings", sa.Column("max_job_age_days", sa.Integer(), nullable=True))

    # 3. Create the Gmail tables (previously only via create_all).
    op.create_table(
        "gmail_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email_address", sa.String(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("history_id", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_address"),
    )

    op.create_table(
        "gmail_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gmail_message_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("account_email", sa.String(), nullable=False),
        sa.Column("from_address", sa.String(), nullable=False),
        sa.Column("from_domain", sa.String(), nullable=False),
        sa.Column("to_address", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("category_confidence", sa.Float(), nullable=True),
        sa.Column("classified_by", sa.String(), nullable=True),
        sa.Column("ats_vendor", sa.String(), nullable=True),
        sa.Column("extracted_company", sa.String(), nullable=True),
        sa.Column("extracted_role", sa.String(), nullable=True),
        sa.Column("extracted_interview_at", sa.DateTime(), nullable=True),
        sa.Column("extracted_salary_text", sa.String(), nullable=True),
        sa.Column("extracted_questions_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gmail_message_id"),
    )
    op.create_index(
        "ix_gmail_messages_account_received", "gmail_messages",
        ["account_email", "received_at"], unique=False,
    )
    op.create_index("ix_gmail_messages_category", "gmail_messages", ["category"], unique=False)
    op.create_index("ix_gmail_messages_from_domain", "gmail_messages", ["from_domain"], unique=False)
    op.create_index(
        "ix_gmail_messages_gmail_thread_id", "gmail_messages", ["gmail_thread_id"], unique=False,
    )
    op.create_index("ix_gmail_messages_received_at", "gmail_messages", ["received_at"], unique=False)

    op.create_table(
        "application_correspondence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("link_confidence", sa.Float(), nullable=False),
        sa.Column("link_method", sa.String(), nullable=False),
        sa.Column("confirmed_by_user", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["gmail_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_correspondence_app_created", "application_correspondence",
        ["application_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_application_correspondence_application_id", "application_correspondence",
        ["application_id"], unique=False,
    )
    op.create_index(
        "ix_application_correspondence_gmail_thread_id", "application_correspondence",
        ["gmail_thread_id"], unique=False,
    )
    op.create_index(
        "ix_application_correspondence_message_id", "application_correspondence",
        ["message_id"], unique=False,
    )

    # 4. Drop the dead column (guard: only if still present).
    cols = {r[1] for r in bind.execute(sa.text("PRAGMA table_info(search_settings)")).fetchall()}
    if "batch_time" in cols:
        with op.batch_alter_table("search_settings") as batch:
            batch.drop_column("batch_time")

    # 5. Add FK constraints by recreating the affected tables (SQLite batch).
    with op.batch_alter_table("application_events", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_application_events_application_id",
            "applications", ["application_id"], ["id"], ondelete="CASCADE",
        )
    with op.batch_alter_table("applications", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_applications_job_match_id",
            "job_matches", ["job_match_id"], ["id"], ondelete="SET NULL",
        )
    with op.batch_alter_table("job_matches", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_job_matches_job_id",
            "jobs", ["job_id"], ["id"], ondelete="CASCADE",
        )
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_jobs_source_id",
            "job_sources", ["source_id"], ["id"], ondelete="SET NULL",
        )
    with op.batch_alter_table("tailored_documents", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_tailored_documents_job_match_id",
            "job_matches", ["job_match_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the FK constraints by recreating the tables without them.
    for table, fk_name in (
        ("tailored_documents", "fk_tailored_documents_job_match_id"),
        ("jobs", "fk_jobs_source_id"),
        ("job_matches", "fk_job_matches_job_id"),
        ("applications", "fk_applications_job_match_id"),
        ("application_events", "fk_application_events_application_id"),
    ):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(fk_name, type_="foreignkey")

    op.drop_table("application_correspondence")
    op.drop_table("gmail_messages")
    op.drop_table("gmail_credentials")

    with op.batch_alter_table("search_settings") as batch:
        batch.add_column(sa.Column("batch_time", sa.String(), nullable=True))
        batch.drop_column("max_job_age_days")
        batch.drop_column("max_results_per_source")
        batch.drop_column("cv_tailoring_enabled")
        batch.drop_column("cv_modification_sensitivity")
        batch.drop_column("countries")

    op.drop_column("user_profile", "mobility")
    op.drop_column("user_profile", "driver_license")
    op.drop_column("user_profile", "linkedin_url")

    op.drop_column("job_matches", "fit_assessment_json")
    op.drop_column("job_matches", "ats_score")
    op.drop_column("job_matches", "gap_severity")

    op.drop_column("jobs", "country")

    op.drop_column("applications", "last_correspondence_at")
