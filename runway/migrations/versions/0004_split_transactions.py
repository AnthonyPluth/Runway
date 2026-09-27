"""One transaction can be split across several categories ($100 at Target: $60 Groceries, $40 Shopping).

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
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_split', sa.Integer(), server_default=sa.text('0'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('is_split')
    op.drop_index('tx_splits_tx', table_name='tx_splits')
    op.drop_table('tx_splits')
