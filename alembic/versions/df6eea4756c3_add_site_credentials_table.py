"""add_site_credentials_table

Revision ID: df6eea4756c3
Revises: 071b973b48b2
Create Date: 2026-03-03 12:53:46.224539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'df6eea4756c3'
down_revision: Union[str, Sequence[str], None] = '071b973b48b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('site_credentials',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('site_name', sa.String(), nullable=False),
        sa.Column('encrypted_email', sa.String(), nullable=True),
        sa.Column('encrypted_password', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('site_credentials')
