"""A recurring item can have a category: a transaction linked to it by hand takes the item's category (and so its icon),
and one matched to it automatically takes it when it has none yet. Items have none until you pick one.

Revision ID: 0041
Revises: 0040
"""
import sqlalchemy as sa
from alembic import op

revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'category' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('recurring')}:
        op.add_column('recurring', sa.Column('category', sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('recurring') as batch:
        batch.drop_column('category')
