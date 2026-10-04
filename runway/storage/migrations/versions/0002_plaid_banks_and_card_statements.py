"""Plaid for banks and cards: each account picks where its data comes from (SimpleFIN or Plaid), and card
statements (balance, closing date, due date) come from the bank through Plaid Liabilities.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('card_statements',
    sa.Column('plaid_account_id', sa.Text(), nullable=False),
    sa.Column('item_id', sa.Text(), nullable=False),
    sa.Column('last_statement_balance', sa.Float(), nullable=True),
    sa.Column('last_statement_date', sa.Text(), nullable=True),
    sa.Column('next_due_date', sa.Text(), nullable=True),
    sa.Column('minimum_payment', sa.Float(), nullable=True),
    sa.Column('last_payment_amount', sa.Float(), nullable=True),
    sa.Column('last_payment_date', sa.Text(), nullable=True),
    sa.Column('is_overdue', sa.Integer(), nullable=True),
    sa.Column('updated', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('plaid_account_id')
    )
    op.create_table('plaid_accounts',
    sa.Column('plaid_account_id', sa.Text(), nullable=False),
    sa.Column('item_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('official_name', sa.Text(), nullable=True),
    sa.Column('mask', sa.Text(), nullable=True),
    sa.Column('type', sa.Text(), nullable=True),
    sa.Column('subtype', sa.Text(), nullable=True),
    sa.Column('current', sa.Float(), nullable=True),
    sa.Column('available', sa.Float(), nullable=True),
    sa.Column('ignored', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.PrimaryKeyConstraint('plaid_account_id')
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provider', sa.Text(), server_default=sa.text("'simplefin'"), nullable=True))
        batch_op.add_column(sa.Column('plaid_account_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('provider_since', sa.Text(), nullable=True))

    with op.batch_alter_table('plaid_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('products', sa.Text(), server_default=sa.text("'investments'"), nullable=True))
        batch_op.add_column(sa.Column('cursor', sa.Text(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('plaid_items', schema=None) as batch_op:
        batch_op.drop_column('cursor')
        batch_op.drop_column('products')

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('provider_since')
        batch_op.drop_column('plaid_account_id')
        batch_op.drop_column('provider')

    op.drop_table('plaid_accounts')
    op.drop_table('card_statements')
