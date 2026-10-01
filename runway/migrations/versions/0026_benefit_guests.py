"""Card benefits: how many guests a lounge (or other access) benefit lets you bring in free.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'guests' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('churn_benefits')}:
        op.add_column('churn_benefits', sa.Column('guests', sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table('churn_benefits') as batch:
        batch.drop_column('guests')
