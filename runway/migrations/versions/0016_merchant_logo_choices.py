"""A logo you choose for a merchant (a website's logo, or none), for every transaction from it.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'merchant_logos' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('merchant_logos',
                        sa.Column('key', sa.Text(), primary_key=True),
                        sa.Column('website', sa.Text()),
                        sa.Column('hidden', sa.Integer(), server_default=sa.text('0')))


def downgrade() -> None:
    op.drop_table('merchant_logos')
