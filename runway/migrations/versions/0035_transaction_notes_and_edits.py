"""Transactions: a note of yours, and a date and amount you can change. When you change a synced one's, the bank's own
is kept beside it (bank_posted, bank_amount), and a sync updates that instead of what you set.

Revision ID: 0035
Revises: 0034
"""
import sqlalchemy as sa
from alembic import op

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('transactions')}
    for name, kind in (('notes', sa.Text()), ('bank_posted', sa.Text()), ('bank_amount', sa.Float())):
        if name not in have:
            op.add_column('transactions', sa.Column(name, kind))


def downgrade() -> None:
    with op.batch_alter_table('transactions') as batch:
        batch.drop_column('bank_amount')
        batch.drop_column('bank_posted')
        batch.drop_column('notes')
