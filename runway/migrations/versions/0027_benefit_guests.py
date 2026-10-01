"""Card benefits: how many guests a lounge (or other access) benefit lets you bring in free.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'guests' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('churn_benefits')}:
        op.add_column('churn_benefits', sa.Column('guests', sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table('churn_benefits') as batch:
        batch.drop_column('guests')
