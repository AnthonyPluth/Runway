"""App lock: app_locks.key_share, the device's share of its encrypted cache's key (#357), kept encrypted (secretbox) on
the device's own row, so it goes wherever the row goes (signing out, the session ending, turning the lock off or on
again). Empty until the device first asks for it. Nothing else changes.

Revision ID: 0044
Revises: 0043
"""
import sqlalchemy as sa
from alembic import op

revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'key_share' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('app_locks')}:
        op.add_column('app_locks', sa.Column('key_share', sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table('app_locks') as batch:
        batch.drop_column('key_share')
