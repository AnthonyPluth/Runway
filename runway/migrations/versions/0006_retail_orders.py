"""Amazon and Target orders, their items and charges, matched to transactions and used to split them.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

from runway.schema import now_text

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('retail_orders',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('retailer', sa.Text(), nullable=False),
    sa.Column('order_number', sa.Text(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=True),
    sa.Column('placed', sa.Text(), nullable=True),
    sa.Column('total', sa.Float(), nullable=True),
    sa.Column('subtotal', sa.Float(), nullable=True),
    sa.Column('tax', sa.Float(), nullable=True),
    sa.Column('shipping', sa.Float(), nullable=True),
    sa.Column('payment', sa.Text(), nullable=True),
    sa.Column('details', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('raw', sa.Text(), nullable=True),
    sa.Column('updated', sa.Text(), server_default=now_text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('retail_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('order_id', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('department', sa.Text(), nullable=True),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('category_source', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True,
    )
    op.create_index('retail_items_order', 'retail_items', ['order_id'], unique=False)
    op.create_table('retail_charges',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('order_id', sa.Text(), nullable=False),
    sa.Column('date', sa.Text(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('payment', sa.Text(), nullable=True),
    sa.Column('tx_id', sa.Text(), nullable=True),
    sa.Column('match_source', sa.Text(), nullable=True),
    sa.Column('not_tx', sa.Text(), nullable=True),
    sa.Column('applied', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('retail_charges_order', 'retail_charges', ['order_id'], unique=False)
    op.create_index('retail_charges_tx', 'retail_charges', ['tx_id'], unique=False)
    op.create_table('retail_item_memory',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('retail_item_memory')
    op.drop_index('retail_charges_tx', table_name='retail_charges')
    op.drop_index('retail_charges_order', table_name='retail_charges')
    op.drop_table('retail_charges')
    op.drop_index('retail_items_order', table_name='retail_items')
    op.drop_table('retail_items')
    op.drop_table('retail_orders')
