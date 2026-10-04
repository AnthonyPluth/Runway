"""A Plaid connection's two syncs keep their own error and last sync time: the bank sync's (transactions, balances, card
statements) in error and last_sync, as before, and the investment sync's (holdings and activity) in inv_error and
inv_last_sync. A connection with both kinds of product is read by both, and one side going fine mustn't clear the other's
problem.

What's there moves to the side that wrote it: an investment-only connection's to inv_*; any other's stays (a connection
with both kinds was only ever synced as a bank one, so its investments have never been read: inv_last_sync stays empty,
and their first sync reads the whole history).

Revision ID: 0037
Revises: 0036
"""
import sqlalchemy as sa
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None

items = sa.table('plaid_items', sa.column('products', sa.Text()), sa.column('last_sync', sa.Text()), sa.column('error', sa.Text()),
                 sa.column('inv_last_sync', sa.Text()), sa.column('inv_error', sa.Text()))
# An investment-only connection: no bank product among its comma-separated products (none listed means investments).
_products = sa.func.coalesce(items.c.products, 'investments')
INVESTMENTS_ONLY = sa.and_(_products.not_like('%transactions%'), _products.not_like('%liabilities%'))


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('plaid_items')}
    if 'inv_last_sync' not in have:
        op.add_column('plaid_items', sa.Column('inv_last_sync', sa.Text()))
    if 'inv_error' not in have:
        op.add_column('plaid_items', sa.Column('inv_error', sa.Text()))
    op.execute(items.update().where(INVESTMENTS_ONLY).values(
        inv_last_sync=items.c.last_sync, inv_error=items.c.error, last_sync=None, error=None))


def downgrade() -> None:
    # One error and time per connection again: an investment-only one gets its investment sync's back; one with both
    # keeps its bank sync's, the side it was synced as before.
    op.execute(items.update().where(INVESTMENTS_ONLY).values(last_sync=items.c.inv_last_sync, error=items.c.inv_error))
    with op.batch_alter_table('plaid_items') as batch:
        batch.drop_column('inv_error')
        batch.drop_column('inv_last_sync')
