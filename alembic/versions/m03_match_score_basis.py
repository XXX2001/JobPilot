"""m03 job_matches.score_basis

What a match's score was computed from, as ``"{cv_hash12}:{GAZETTEER_VERSION}"``.
Existing rows keep NULL: they were scored by the old keyword matcher and are
recomputed by ``rescore_all``.

Revision ID: m03_match_score_basis
Revises: m02_city_dedup_first_polled
Create Date: 2026-11-14 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m03_match_score_basis"
down_revision: Union[str, Sequence[str], None] = "m02_city_dedup_first_polled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job_matches") as batch:
        batch.add_column(sa.Column("score_basis", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_matches") as batch:
        batch.drop_column("score_basis")
