"""Categories get an emoji and a color, shown next to them everywhere. NULL means Runway's default for the name.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('categories')}
    for name in ('icon', 'color'):
        if name not in have:
            op.add_column('categories', sa.Column(name, sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('categories') as b:
        b.drop_column('color')
        b.drop_column('icon')
