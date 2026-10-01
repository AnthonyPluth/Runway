"""Loans: the rate and monthly payment you enter, so a loan's balance goes down between syncs (and in the
retirement planner).

Revision ID: 0028
Revises: 0027
"""
import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('accounts')}
    if 'loan_rate' not in have:
        op.add_column('accounts', sa.Column('loan_rate', sa.Float()))
    if 'loan_payment' not in have:
        op.add_column('accounts', sa.Column('loan_payment', sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('loan_payment')
        batch.drop_column('loan_rate')
