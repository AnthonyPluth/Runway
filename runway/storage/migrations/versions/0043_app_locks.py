"""App lock (Face ID / Touch ID): app_locks, one row per signed-in browser that turned it on, with its passkey's public
key and until when it's unlocked. It refers to the browser's sign-in (auth_sessions) and goes with it. Nothing else
changes.

Revision ID: 0043
Revises: 0042
"""
import sqlalchemy as sa
from alembic import op

revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'app_locks' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('app_locks',
                    sa.Column('session', sa.Text(), nullable=False),
                    sa.Column('credential_id', sa.Text(), nullable=False),
                    sa.Column('public_key', sa.Text(), nullable=False),
                    sa.Column('alg', sa.Integer(), nullable=False),
                    sa.Column('sign_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
                    sa.Column('idle', sa.Integer(), nullable=False),
                    sa.Column('unlocked_at', sa.Float(), nullable=True),
                    sa.Column('unlocked_until', sa.Float(), nullable=True),
                    sa.Column('created', sa.Float(), nullable=False),
                    sa.ForeignKeyConstraint(['session'], ['auth_sessions.token_hash'], name='fk_app_locks_session',
                                            ondelete='CASCADE', deferrable=True, initially='IMMEDIATE'),
                    sa.PrimaryKeyConstraint('session'))


def downgrade() -> None:
    op.drop_table('app_locks')
