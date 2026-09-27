"""Merchants as Plaid names them, with their logos (downloaded from Plaid and kept here), and each transaction's
merchant.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('merchants',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('website', sa.Text(), nullable=True),
    sa.Column('logo_url', sa.Text(), nullable=True),
    sa.Column('logo', sa.Text(), nullable=True),
    sa.Column('logo_type', sa.Text(), nullable=True),
    sa.Column('logo_checked', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('merchant_id', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('merchant_id')
    op.drop_table('merchants')
