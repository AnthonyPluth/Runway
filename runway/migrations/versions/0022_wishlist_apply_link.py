"""Churning: a link to apply at, on each planned card or bank bonus.

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'apply_url' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('churn_wishlist')}:
        op.add_column('churn_wishlist', sa.Column('apply_url', sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('churn_wishlist') as batch:
        batch.drop_column('apply_url')
