"""Churning: checking and savings account sign-up bonuses.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

from runway.schema import now_text

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'churn_bank_bonuses' in sa.inspect(op.get_bind()).get_table_names():   # already there (a database upgraded again)
        return
    op.create_table('churn_bank_bonuses',
                    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                    sa.Column('owner', sa.Text(), nullable=False),
                    sa.Column('bank', sa.Text(), nullable=False),
                    sa.Column('account_type', sa.Text(), server_default=sa.text("'checking'")),
                    sa.Column('account_id', sa.Text()),
                    sa.Column('opened_on', sa.Text(), nullable=False),
                    sa.Column('bonus', sa.Float(), nullable=False),
                    sa.Column('dd_total', sa.Float()),
                    sa.Column('dd_count', sa.Integer()),
                    sa.Column('debit_count', sa.Integer()),
                    sa.Column('min_balance', sa.Float()),
                    sa.Column('hold_until', sa.Text()),
                    sa.Column('other_reqs', sa.Text()),
                    sa.Column('deadline_days', sa.Integer(), server_default=sa.text('90')),
                    sa.Column('deadline', sa.Text()),
                    sa.Column('post_days', sa.Integer(), server_default=sa.text('60')),
                    sa.Column('manual_dd', sa.Float()),
                    sa.Column('manual_debits', sa.Integer()),
                    sa.Column('status', sa.Text(), server_default=sa.text("'open'")),
                    sa.Column('received_on', sa.Text()),
                    sa.Column('received_amount', sa.Float()),
                    sa.Column('closed_on', sa.Text()),
                    sa.Column('monthly_fee', sa.Float(), server_default=sa.text('0')),
                    sa.Column('fee_waiver', sa.Text()),
                    sa.Column('early_close_fee', sa.Float()),
                    sa.Column('keep_open_days', sa.Integer()),
                    sa.Column('repeat_months', sa.Integer()),
                    sa.Column('once_per_lifetime', sa.Integer(), server_default=sa.text('0')),
                    sa.Column('eligible_on', sa.Text()),
                    sa.Column('notes', sa.Text()),
                    sa.Column('created_at', sa.Text(), server_default=now_text()),
                    sa.PrimaryKeyConstraint('id'),
                    sqlite_autoincrement=True)


def downgrade() -> None:
    op.drop_table('churn_bank_bonuses')
