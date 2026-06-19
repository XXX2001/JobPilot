"""add_last_dashboard_seen_at_to_userprofile

Revision ID: e3a1f2b8c9d7
Revises: 41441908fc29
Create Date: 2026-05-23 00:00:00.000000

Adds UserProfile.last_dashboard_seen_at (nullable DateTime) to track when
the user last visited the Today dashboard. Used by GET /api/today to compute
"new since last visit" counts. NULL = never visited → fallback to 24 h window.
No backfill needed — NULL is handled by the endpoint.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3a1f2b8c9d7"
down_revision: Union[str, Sequence[str], None] = "41441908fc29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_dashboard_seen_at column to user_profile table."""
    op.add_column(
        "user_profile",
        sa.Column("last_dashboard_seen_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Remove last_dashboard_seen_at column from user_profile table."""
    op.drop_column("user_profile", "last_dashboard_seen_at")
