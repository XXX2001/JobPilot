"""m04 job_matches.provisional, job_matches.location_unverified

What a match's score rests on, for the queue and the notification: ``provisional`` is
a title-only score (no description, or one naming under two skills);
``location_unverified`` is a location a country filter let through without proof.
Existing rows get false and are set by the next rescore.

Revision ID: m04_match_score_flags
Revises: m03_match_score_basis
Create Date: 2026-11-14 23:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m04_match_score_flags"
down_revision: Union[str, Sequence[str], None] = "m03_match_score_basis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job_matches") as batch:
        for name in ("provisional", "location_unverified"):
            batch.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    with op.batch_alter_table("job_matches") as batch:
        batch.drop_column("location_unverified")
        batch.drop_column("provisional")
