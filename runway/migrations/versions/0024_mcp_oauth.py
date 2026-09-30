"""MCP over OAuth: the apps registered to connect, each approval (grant), and their codes and tokens. The MCP key it
replaces (the rwm_ bearer key, kept as a hash in settings) is deleted.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'oauth_clients' not in tables:
        op.create_table('oauth_clients',
                        sa.Column('id', sa.Text(), nullable=False),
                        sa.Column('name', sa.Text()),
                        sa.Column('redirect_uris', sa.Text(), nullable=False),
                        sa.Column('auth_method', sa.Text(), nullable=False),
                        sa.Column('secret_hash', sa.Text()),
                        sa.Column('kind', sa.Text(), nullable=False, server_default=sa.text("'dcr'")),
                        sa.Column('metadata_url', sa.Text()),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('last_used', sa.Float()),
                        sa.PrimaryKeyConstraint('id'))
    if 'oauth_grants' not in tables:
        op.create_table('oauth_grants',
                        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                        sa.Column('client_id', sa.Text(), nullable=False),
                        sa.Column('sub', sa.Text()),
                        sa.Column('email', sa.Text()),
                        sa.Column('scope', sa.Text(), nullable=False),
                        sa.Column('resource', sa.Text(), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('last_used', sa.Float()),
                        sa.Column('revoked', sa.Float()),
                        sa.Column('revoked_reason', sa.Text()),
                        sa.PrimaryKeyConstraint('id'),
                        sqlite_autoincrement=True)
        op.create_index('oauth_grants_client', 'oauth_grants', ['client_id'], unique=False)
    if 'oauth_codes' not in tables:
        op.create_table('oauth_codes',
                        sa.Column('code_hash', sa.Text(), nullable=False),
                        sa.Column('client_id', sa.Text(), nullable=False),
                        sa.Column('grant_id', sa.Integer(), nullable=False),
                        sa.Column('redirect_uri', sa.Text(), nullable=False),
                        sa.Column('code_challenge', sa.Text(), nullable=False),
                        sa.Column('resource', sa.Text(), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('used', sa.Float()),
                        sa.PrimaryKeyConstraint('code_hash'))
        op.create_index('oauth_codes_grant', 'oauth_codes', ['grant_id'], unique=False)
    if 'oauth_tokens' not in tables:
        op.create_table('oauth_tokens',
                        sa.Column('token_hash', sa.Text(), nullable=False),
                        sa.Column('kind', sa.Text(), nullable=False),
                        sa.Column('grant_id', sa.Integer(), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('expires', sa.Float(), nullable=False),
                        sa.Column('consumed', sa.Float()),
                        sa.Column('replaced_by', sa.Text()),
                        sa.PrimaryKeyConstraint('token_hash'))
        op.create_index('oauth_tokens_grant', 'oauth_tokens', ['grant_id'], unique=False)
    if 'oauth_consents' not in tables:
        op.create_table('oauth_consents',
                        sa.Column('token_hash', sa.Text(), nullable=False),
                        sa.Column('params', sa.Text(), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.PrimaryKeyConstraint('token_hash'))
    settings = sa.table('settings', sa.column('key'))
    op.execute(sa.delete(settings).where(settings.c.key.in_(['mcp_token_hash', 'mcp_token_created'])))


def downgrade() -> None:
    op.drop_table('oauth_consents')
    op.drop_index('oauth_tokens_grant', table_name='oauth_tokens')
    op.drop_table('oauth_tokens')
    op.drop_index('oauth_codes_grant', table_name='oauth_codes')
    op.drop_table('oauth_codes')
    op.drop_index('oauth_grants_client', table_name='oauth_grants')
    op.drop_table('oauth_grants')
    op.drop_table('oauth_clients')
