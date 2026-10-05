"""Churning: a downgrade is a product change, so the two card plans become one.

Revision ID: 0021
Revises: 0020
"""
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE churn_cards SET plan = 'product_change' WHERE plan = 'downgrade'")


def downgrade() -> None:
    pass   # the two can't be told apart again
