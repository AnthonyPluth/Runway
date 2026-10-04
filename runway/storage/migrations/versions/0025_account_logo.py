"""Accounts: the logo you chose for one (a website's, or none), instead of the one Runway finds for its institution.

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'logo' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('accounts')}:
        op.add_column('accounts', sa.Column('logo', sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('logo')
