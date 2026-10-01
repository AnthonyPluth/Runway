"""Card statements you enter by hand (for a card Plaid sends none for), and the accounts you deleted (so a sync doesn't
bring them back).

Revision ID: 0029
Revises: 0028
"""
import sqlalchemy as sa
from alembic import op

from runway.schema import now_text

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'manual_statements' not in tables:
        op.create_table('manual_statements',
                        sa.Column('account_id', sa.Text(), nullable=False),
                        sa.Column('statement_date', sa.Text(), nullable=False),
                        sa.Column('balance', sa.Float(), nullable=False),
                        sa.Column('due_date', sa.Text(), nullable=False),
                        sa.Column('minimum_payment', sa.Float()),
                        sa.Column('entered_at', sa.Text(), server_default=now_text()),
                        sa.PrimaryKeyConstraint('account_id', 'statement_date'))
    if 'deleted_accounts' not in tables:
        op.create_table('deleted_accounts',
                        sa.Column('id', sa.Text(), nullable=False),
                        sa.Column('name', sa.Text()),
                        sa.Column('kind', sa.Text()),
                        sa.Column('plaid_account_id', sa.Text()),
                        sa.Column('inv_ids', sa.Text()),
                        sa.Column('deleted_at', sa.Text(), server_default=now_text()),
                        sa.Column('restored_at', sa.Text()),
                        sa.PrimaryKeyConstraint('id'))


def downgrade() -> None:
    op.drop_table('deleted_accounts')
    op.drop_table('manual_statements')
