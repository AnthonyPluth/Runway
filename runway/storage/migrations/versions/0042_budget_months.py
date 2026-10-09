"""A budget can have its own amount for a month (December's gifts, a summer trip): budget_months, one row per budget and
month. Every month without one has the budget's usual amount (budgets.amount), so existing budgets stay as they are.

Revision ID: 0042
Revises: 0041
"""
import sqlalchemy as sa
from alembic import op

revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'budget_months' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('budget_months',
                    sa.Column('category', sa.Text(), nullable=False),
                    sa.Column('month', sa.Text(), nullable=False),
                    sa.Column('amount', sa.Float(), nullable=False),
                    sa.ForeignKeyConstraint(['category'], ['budgets.category'], name='fk_budget_months_category',
                                            ondelete='CASCADE', deferrable=True, initially='IMMEDIATE'),
                    sa.PrimaryKeyConstraint('category', 'month'))


def downgrade() -> None:
    op.drop_table('budget_months')
