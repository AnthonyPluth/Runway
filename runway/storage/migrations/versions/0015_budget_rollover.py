"""Budgets can roll over: what's left of one month's budget is added to the next.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'rollover_from' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('budgets')}:
        op.add_column('budgets', sa.Column('rollover_from', sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('budgets') as b:
        b.drop_column('rollover_from')
