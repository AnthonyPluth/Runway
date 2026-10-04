"""Baseline: the schema as it was when Runway started using migrations.

Databases made before then get any missing columns added and are marked as being at this revision (db.migrate).

Revision ID: 0001
Revises:
Create Date: 2026-09-25 14:01:40.310024
"""
from alembic import op
import sqlalchemy as sa

from runway.schema import now_text

# SQLite's instr(haystack, needle), for Postgres: Runway's queries used it then (migration 0037 drops it).
POSTGRES_INSTR = ("CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer AS 'SELECT strpos($1, $2)' "
                  "LANGUAGE sql IMMUTABLE")


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('org', sa.Text(), nullable=True),
    sa.Column('currency', sa.Text(), server_default=sa.text("'USD'"), nullable=True),
    sa.Column('balance', sa.Float(), server_default=sa.text('0'), nullable=True),
    sa.Column('available', sa.Float(), nullable=True),
    sa.Column('balance_date', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), server_default=sa.text("'checking'"), nullable=True),
    sa.Column('closing_day', sa.Integer(), nullable=True),
    sa.Column('due_day', sa.Integer(), nullable=True),
    sa.Column('pay_from', sa.Text(), nullable=True),
    sa.Column('owed_positive', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('in_forecast', sa.Integer(), server_default=sa.text('1'), nullable=True),
    sa.Column('daily_spend', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('hidden', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('owner', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ai_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.Text(), server_default=now_text(local=True), nullable=True),
    sa.Column('purpose', sa.Text(), nullable=True),
    sa.Column('model', sa.Text(), nullable=True),
    sa.Column('merchants', sa.Integer(), nullable=True),
    sa.Column('answered', sa.Integer(), nullable=True),
    sa.Column('new_cats', sa.Integer(), nullable=True),
    sa.Column('ok', sa.Integer(), nullable=True),
    sa.Column('seconds', sa.Float(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('reply', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_table('asset_values',
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('source', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('asset_id', 'date')
    )
    op.create_table('assets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('as_of', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), server_default=sa.text("'manual'"), nullable=True),
    sa.Column('yearly_change', sa.Float(), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('loan_account_id', sa.Text(), nullable=True),
    sa.Column('auto_update', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('last_lookup', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), server_default=now_text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_table('auth_pending',
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('nonce', sa.Text(), nullable=True),
    sa.Column('verifier', sa.Text(), nullable=True),
    sa.Column('next', sa.Text(), nullable=True),
    sa.Column('created', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_table('auth_sessions',
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('sub', sa.Text(), nullable=True),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('created', sa.Float(), nullable=True),
    sa.Column('expires', sa.Float(), nullable=True),
    sa.Column('id_token', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_table('budgets',
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('pay_with', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('category')
    )
    op.create_table('categories',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('is_transfer', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('is_income', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('parent', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('holding_snapshots',
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.Text(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('date', 'account_id', 'security_id')
    )
    op.create_table('holdings',
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.Text(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=True),
    sa.Column('price', sa.Float(), nullable=True),
    sa.Column('price_as_of', sa.Text(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('cost_basis', sa.Float(), nullable=True),
    sa.Column('currency', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('account_id', 'security_id')
    )
    op.create_table('inv_accounts',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('item_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('official_name', sa.Text(), nullable=True),
    sa.Column('type', sa.Text(), nullable=True),
    sa.Column('subtype', sa.Text(), nullable=True),
    sa.Column('mask', sa.Text(), nullable=True),
    sa.Column('balance', sa.Float(), nullable=True),
    sa.Column('currency', sa.Text(), server_default=sa.text("'USD'"), nullable=True),
    sa.Column('hidden', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('source', sa.Text(), server_default=sa.text("'plaid'"), nullable=True),
    sa.Column('institution', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('inv_snapshots',
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('date', 'account_id')
    )
    op.create_table('inv_transactions',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.Text(), nullable=True),
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('type', sa.Text(), nullable=True),
    sa.Column('subtype', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('price', sa.Float(), nullable=True),
    sa.Column('fees', sa.Float(), nullable=True),
    sa.Column('currency', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inv_transactions', schema=None) as batch_op:
        batch_op.create_index('inv_tx_account_date', ['account_id', 'date'], unique=False)

    op.create_table('manual_contributions',
    sa.Column('account_id', sa.Text(), nullable=True),
    sa.Column('date', sa.Text(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True)
    )
    op.create_table('manual_positions',
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.Text(), nullable=False),
    sa.Column('shares', sa.Float(), nullable=True),
    sa.Column('pct', sa.Float(), nullable=True),
    sa.Column('last_value', sa.Float(), nullable=True),
    sa.Column('updated', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('account_id', 'security_id')
    )
    op.create_table('cost_overrides',
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('security_id', sa.Text(), nullable=False),
    sa.Column('cost_basis', sa.Float(), nullable=False),
    sa.Column('per_share', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('account_id', 'security_id')
    )
    op.create_table('manual_state',
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('drift', sa.Float(), nullable=True),
    sa.Column('checked', sa.Text(), nullable=True),
    sa.Column('last_balance', sa.Float(), nullable=True),
    sa.Column('baseline', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('account_id')
    )
    op.create_table('networth_snapshots',
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('assets', sa.Float(), nullable=True),
    sa.Column('liabilities', sa.Float(), nullable=True),
    sa.Column('net', sa.Float(), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('date')
    )
    op.create_table('notify_log',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('sent', sa.Float(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('overrides',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('plaid_items',
    sa.Column('item_id', sa.Text(), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=False),
    sa.Column('institution_id', sa.Text(), nullable=True),
    sa.Column('institution_name', sa.Text(), nullable=True),
    sa.Column('env', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), server_default=now_text(), nullable=True),
    sa.Column('last_sync', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('item_id')
    )
    op.create_table('price_meta',
    sa.Column('ticker', sa.Text(), nullable=False),
    sa.Column('fetched_at', sa.Text(), nullable=True),
    sa.Column('ok', sa.Integer(), nullable=True),
    sa.Column('splits', sa.Text(), nullable=True),
    sa.Column('instrument_type', sa.Text(), nullable=True),
    sa.Column('long_name', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('ticker')
    )
    op.create_table('prices',
    sa.Column('ticker', sa.Text(), nullable=False),
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('close', sa.Float(), nullable=True),
    sa.Column('adjclose', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('ticker', 'date')
    )
    op.create_table('push_subscriptions',
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.Text(), nullable=False),
    sa.Column('auth', sa.Text(), nullable=False),
    sa.Column('device', sa.Text(), nullable=True),
    sa.Column('user_sub', sa.Text(), nullable=True),
    sa.Column('created', sa.Float(), nullable=True),
    sa.Column('last_ok', sa.Float(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('endpoint')
    )
    op.create_table('recurring',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('frequency', sa.Text(), nullable=False),
    sa.Column('anchor_date', sa.Text(), nullable=False),
    sa.Column('match', sa.Text(), nullable=True),
    sa.Column('end_date', sa.Text(), nullable=True),
    sa.Column('active', sa.Integer(), server_default=sa.text('1'), nullable=True),
    sa.Column('amount_mode', sa.Text(), server_default=sa.text("'fixed'"), nullable=True),
    sa.Column('dates', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_table('recurring_dismissed',
    sa.Column('key', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('rules',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('match', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('created_at', sa.Text(), server_default=now_text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('match'),
    sqlite_autoincrement=True
    )
    op.create_table('securities',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('ticker', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('type', sa.Text(), nullable=True),
    sa.Column('subtype', sa.Text(), nullable=True),
    sa.Column('close_price', sa.Float(), nullable=True),
    sa.Column('close_as_of', sa.Text(), nullable=True),
    sa.Column('is_cash', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('sector', sa.Text(), nullable=True),
    sa.Column('industry', sa.Text(), nullable=True),
    sa.Column('currency', sa.Text(), nullable=True),
    sa.Column('cusip', sa.Text(), nullable=True),
    sa.Column('isin', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('settings',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('sync_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.Text(), server_default=now_text(), nullable=True),
    sa.Column('ok', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_table('transactions',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('account_id', sa.Text(), nullable=False),
    sa.Column('posted', sa.Text(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('payee', sa.Text(), nullable=True),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('category_source', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('needs_review', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('pending', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('created_at', sa.Text(), server_default=now_text(), nullable=True),
    sa.Column('recurring_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.create_index('tx_account_posted', ['account_id', 'posted'], unique=False)
        batch_op.create_index('tx_recurring', ['recurring_id'], unique=False)
        batch_op.create_index('tx_review', ['needs_review'], unique=False)

    op.create_table('users',
    sa.Column('sub', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('first_name', sa.Text(), nullable=True),
    sa.Column('last_seen', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('sub')
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(POSTGRES_INSTR)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS instr(text, text)")
    op.drop_table('users')
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_index('tx_review')
        batch_op.drop_index('tx_recurring')
        batch_op.drop_index('tx_account_posted')

    op.drop_table('transactions')
    op.drop_table('sync_log')
    op.drop_table('settings')
    op.drop_table('securities')
    op.drop_table('rules')
    op.drop_table('recurring_dismissed')
    op.drop_table('recurring')
    op.drop_table('push_subscriptions')
    op.drop_table('prices')
    op.drop_table('price_meta')
    op.drop_table('plaid_items')
    op.drop_table('overrides')
    op.drop_table('notify_log')
    op.drop_table('networth_snapshots')
    op.drop_table('cost_overrides')
    op.drop_table('manual_state')
    op.drop_table('manual_positions')
    op.drop_table('manual_contributions')
    with op.batch_alter_table('inv_transactions', schema=None) as batch_op:
        batch_op.drop_index('inv_tx_account_date')

    op.drop_table('inv_transactions')
    op.drop_table('inv_snapshots')
    op.drop_table('inv_accounts')
    op.drop_table('holdings')
    op.drop_table('holding_snapshots')
    op.drop_table('categories')
    op.drop_table('budgets')
    op.drop_table('auth_sessions')
    op.drop_table('auth_pending')
    op.drop_table('assets')
    op.drop_table('asset_values')
    op.drop_table('ai_log')
    op.drop_table('accounts')
