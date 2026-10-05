"""Home values moved from RentCast to Realie: forget the RentCast key and its lookup counts. Values already
looked up keep their "rentcast" source, so they still say where they came from.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM settings WHERE key='rentcast_api_key' OR key LIKE 'rentcast_calls:%'")


def downgrade() -> None:
    pass
