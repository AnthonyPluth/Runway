"""One transaction can be split across several categories ($100 at Target: $60 Groceries, $40 Shopping).

This was briefly numbered 0004 alongside the investment-accounts migration, so a database can reach here from either
0004: each change is made only if it isn't there yet (including inv_accounts.account_id, for a database that ran this
one as its 0004).

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'tx_splits' not in insp.get_table_names():
        op.create_table('tx_splits',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tx_id', sa.Text(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sqlite_autoincrement=True,
        )
        op.create_index('tx_splits_tx', 'tx_splits', ['tx_id'], unique=False)
    if 'is_split' not in {c["name"] for c in insp.get_columns('transactions')}:
        with op.batch_alter_table('transactions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_split', sa.Integer(), server_default=sa.text('0'), nullable=True))
    if 'account_id' not in {c["name"] for c in insp.get_columns('inv_accounts')}:
        with op.batch_alter_table('inv_accounts', schema=None) as batch_op:
            batch_op.add_column(sa.Column('account_id', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('is_split')
    op.drop_index('tx_splits_tx', table_name='tx_splits')
    op.drop_table('tx_splits')
