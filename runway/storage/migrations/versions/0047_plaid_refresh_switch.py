"""Plaid connections: whether Runway asks Plaid to refresh their transactions before the daily sync (plaid_items.refresh),
and refreshing paused after failing several times in a row (refresh_failures, refresh_paused, refresh_paused_at).

A connection already linked gets the rule a new one gets from its accounts (plaidbank.refresh_default): on if any of the
accounts stored for it isn't a credit card, off if they all are. One with no accounts stored yet stays NULL, and its next
sync decides the same way. Nothing else changes.

Revision ID: 0047
Revises: 0046
"""
import sqlalchemy as sa
from alembic import op

revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None

COLUMNS = ('refresh', 'refresh_failures', 'refresh_paused', 'refresh_paused_at')


def upgrade() -> None:
    if 'refresh' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('plaid_items')}:
        return
    op.add_column('plaid_items', sa.Column('refresh', sa.Integer()))
    op.add_column('plaid_items', sa.Column('refresh_failures', sa.Integer(), server_default=sa.text('0')))
    op.add_column('plaid_items', sa.Column('refresh_paused', sa.Text()))
    op.add_column('plaid_items', sa.Column('refresh_paused_at', sa.Text()))
    op.execute("UPDATE plaid_items SET refresh = CASE WHEN EXISTS (SELECT 1 FROM plaid_accounts a "
               "WHERE a.item_id = plaid_items.item_id AND COALESCE(a.type, '') <> 'credit') THEN 1 ELSE 0 END "
               "WHERE EXISTS (SELECT 1 FROM plaid_accounts a WHERE a.item_id = plaid_items.item_id)")


def downgrade() -> None:
    with op.batch_alter_table('plaid_items') as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
