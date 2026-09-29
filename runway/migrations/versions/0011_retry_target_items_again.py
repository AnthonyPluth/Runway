"""0010 meant to give Target orders without items their tries back, but looked for details IS NULL where an order
without them has details=0. This does it for those orders.

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE retail_orders SET attempts=0 WHERE retailer='target' AND COALESCE(details, 0)=0")


def downgrade() -> None:
    pass
