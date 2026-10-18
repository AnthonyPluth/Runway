"""Deleted card charges: deleted_charges, the posted charges on a card you deleted from the transactions list, which the
card's balance at the bank still has, so the forecast keeps counting them (forecast.card_cycle). Each refers to its
account and goes with it. Nothing else changes.

Revision ID: 0046
Revises: 0045
"""
import sqlalchemy as sa
from alembic import op

from runway.storage.schema import now_text

revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'deleted_charges' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('deleted_charges',
                    sa.Column('id', sa.Text(), nullable=False),
                    sa.Column('account_id', sa.Text(), nullable=False),
                    sa.Column('posted', sa.Text(), nullable=False),
                    sa.Column('amount', sa.Float(), nullable=False),
                    sa.Column('category', sa.Text(), nullable=True),
                    sa.Column('deleted_at', sa.Text(), server_default=now_text()),
                    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='fk_deleted_charges_account_id',
                                            ondelete='CASCADE', deferrable=True, initially='IMMEDIATE'),
                    sa.PrimaryKeyConstraint('id'))


def downgrade() -> None:
    op.drop_table('deleted_charges')
