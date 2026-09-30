"""Net worth: leave an account out of Net worth (and only there).

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'networth_hidden' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('accounts')}:
        op.add_column('accounts', sa.Column('networth_hidden', sa.Integer(), server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('networth_hidden')
