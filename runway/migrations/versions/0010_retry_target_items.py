"""Target orders whose items couldn't be read yet get their tries back, now that Runway knows where Target keeps them.

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE retail_orders SET attempts=0 WHERE retailer='target' AND details IS NULL")


def downgrade() -> None:
    pass
