"""Card statements: the card's purchase APR from Plaid Liabilities, for the interest on a balance the forecast carries.

Revision ID: 0029
Revises: 0028
"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'purchase_apr' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('card_statements')}:
        op.add_column('card_statements', sa.Column('purchase_apr', sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table('card_statements') as batch:
        batch.drop_column('purchase_apr')
