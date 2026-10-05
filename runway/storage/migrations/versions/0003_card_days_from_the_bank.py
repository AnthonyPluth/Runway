"""Card closing and due days come from the issuer's statements (Plaid Liabilities), so they're no longer set by hand.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns('accounts')}
    gone = [c for c in ('closing_day', 'due_day') if c in have]
    if gone:
        with op.batch_alter_table('accounts', schema=None) as batch_op:
            for c in gone:
                batch_op.drop_column(c)


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('closing_day', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('due_day', sa.Integer(), nullable=True))
