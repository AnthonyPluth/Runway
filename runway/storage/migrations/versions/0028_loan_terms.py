"""Loan terms, so the retirement planner can project what's still owed when a home is sold: mortgage and student loan
terms from Plaid Liabilities (loan_terms), and an interest rate and monthly payment you can set on any loan account.

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
    insp = sa.inspect(op.get_bind())
    if 'loan_terms' not in insp.get_table_names():
        op.create_table('loan_terms',
                        sa.Column('plaid_account_id', sa.Text(), nullable=False),
                        sa.Column('item_id', sa.Text(), nullable=False),
                        sa.Column('kind', sa.Text()),
                        sa.Column('interest_rate', sa.Float()),
                        sa.Column('monthly_payment', sa.Float()),
                        sa.Column('maturity_date', sa.Text()),
                        sa.Column('updated', sa.Text()),
                        sa.PrimaryKeyConstraint('plaid_account_id'))
    have = {c['name'] for c in insp.get_columns('accounts')}
    for name in ('interest_rate', 'monthly_payment'):
        if name not in have:
            op.add_column('accounts', sa.Column(name, sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('monthly_payment')
        batch.drop_column('interest_rate')
    op.drop_table('loan_terms')
