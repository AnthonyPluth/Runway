"""An investment account from a Plaid connection is matched to a Runway account (so it shows under Settings → Accounts
and counts in net worth once): inv_accounts.account_id.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns('inv_accounts')}
    if 'account_id' not in have:
        with op.batch_alter_table('inv_accounts', schema=None) as batch_op:
            batch_op.add_column(sa.Column('account_id', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('inv_accounts', schema=None) as batch_op:
        batch_op.drop_column('account_id')
