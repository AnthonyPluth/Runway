"""Churning: credit cards opened for their sign-up bonuses, what they earn, points values, balances and to-dos.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

from runway.storage.schema import now_text

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'churn_cards' in sa.inspect(op.get_bind()).get_table_names():   # already there (a database upgraded again)
        return
    op.create_table('churn_cards',
                    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                    sa.Column('owner', sa.Text(), nullable=False),
                    sa.Column('issuer', sa.Text(), nullable=False),
                    sa.Column('product', sa.Text(), nullable=False),
                    sa.Column('family', sa.Text()),
                    sa.Column('account_id', sa.Text()),
                    sa.Column('opened_on', sa.Text(), nullable=False),
                    sa.Column('closed_on', sa.Text()),
                    sa.Column('status', sa.Text(), server_default=sa.text("'open'")),
                    sa.Column('changed_from', sa.Integer()),
                    sa.Column('authorized_user', sa.Integer(), server_default=sa.text('0')),
                    sa.Column('business', sa.Integer(), server_default=sa.text('0')),
                    sa.Column('annual_fee', sa.Float(), server_default=sa.text('0')),
                    sa.Column('fee_month', sa.Integer()),
                    sa.Column('currency', sa.Text(), server_default=sa.text("'cash'")),
                    sa.Column('base_rate', sa.Float(), server_default=sa.text('1')),
                    sa.Column('earn_note', sa.Text()),
                    sa.Column('bonus', sa.Float()),
                    sa.Column('bonus_spend', sa.Float()),
                    sa.Column('bonus_months', sa.Integer(), server_default=sa.text('3')),
                    sa.Column('bonus_deadline', sa.Text()),
                    sa.Column('bonus_earned_on', sa.Text()),
                    sa.Column('manual_spend', sa.Float()),
                    sa.Column('eligible_on', sa.Text()),
                    sa.Column('notes', sa.Text()),
                    sa.Column('created_at', sa.Text(), server_default=now_text()),
                    sa.PrimaryKeyConstraint('id'),
                    sqlite_autoincrement=True)
    op.create_table('churn_rates',
                    sa.Column('card_id', sa.Integer(), nullable=False),
                    sa.Column('category', sa.Text(), nullable=False),
                    sa.Column('multiplier', sa.Float(), nullable=False),
                    sa.PrimaryKeyConstraint('card_id', 'category'))
    op.create_table('churn_currencies',
                    sa.Column('key', sa.Text(), nullable=False),
                    sa.Column('name', sa.Text(), nullable=False),
                    sa.Column('cents', sa.Float(), nullable=False),
                    sa.PrimaryKeyConstraint('key'))
    op.create_table('churn_balances',
                    sa.Column('owner', sa.Text(), nullable=False),
                    sa.Column('currency', sa.Text(), nullable=False),
                    sa.Column('points', sa.Float(), nullable=False),
                    sa.Column('as_of', sa.Text()),
                    sa.PrimaryKeyConstraint('owner', 'currency'))
    op.create_table('churn_tasks',
                    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                    sa.Column('card_id', sa.Integer(), nullable=False),
                    sa.Column('due_on', sa.Text(), nullable=False),
                    sa.Column('action', sa.Text(), nullable=False),
                    sa.Column('done', sa.Integer(), server_default=sa.text('0')),
                    sa.PrimaryKeyConstraint('id'),
                    sqlite_autoincrement=True)
    op.create_index('churn_tasks_card', 'churn_tasks', ['card_id'], unique=False)


def downgrade() -> None:
    op.drop_index('churn_tasks_card', table_name='churn_tasks')
    op.drop_table('churn_tasks')
    op.drop_table('churn_balances')
    op.drop_table('churn_currencies')
    op.drop_table('churn_rates')
    op.drop_table('churn_cards')
