"""Shared job-site password and account registry metadata.

Revision ID: a01_job_site_accounts
Revises: m04_match_score_flags
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a01_job_site_accounts"
down_revision: Union[str, Sequence[str], None] = "m04_match_score_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL distinguishes legacy credentials from harness registry accounts.
    with op.batch_alter_table("site_credentials") as batch:
        for name in ("method", "status", "login_url", "note"):
            batch.add_column(sa.Column(name, sa.String(), nullable=True))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("user_profile") as batch:
        batch.add_column(sa.Column("encrypted_job_site_password", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_profile") as batch:
        batch.drop_column("encrypted_job_site_password")
    with op.batch_alter_table("site_credentials") as batch:
        for name in ("last_used_at", "note", "login_url", "status", "method"):
            batch.drop_column(name)
