"""Equity compensation: companies you hold stock or options in, and your grants (entered by hand or from Carta).

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

from runway.schema import now_text

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('equity_companies',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('share_price', sa.Float(), nullable=True),
    sa.Column('price_as_of', sa.Text(), nullable=True),
    sa.Column('in_networth', sa.Integer(), server_default=sa.text('1'), nullable=True),
    sa.Column('source', sa.Text(), server_default=sa.text("'manual'"), nullable=True),
    sa.Column('raw', sa.Text(), nullable=True),
    sa.Column('updated', sa.Text(), server_default=now_text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('equity_grants',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('company_id', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('granted_on', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('strike', sa.Float(), nullable=True),
    sa.Column('vest_start', sa.Text(), nullable=True),
    sa.Column('vest_months', sa.Integer(), nullable=True),
    sa.Column('cliff_months', sa.Integer(), nullable=True),
    sa.Column('vest_every', sa.Integer(), server_default=sa.text('1'), nullable=True),
    sa.Column('exercised', sa.Float(), server_default=sa.text('0'), nullable=True),
    sa.Column('vested_reported', sa.Float(), nullable=True),
    sa.Column('vested_reported_on', sa.Text(), nullable=True),
    sa.Column('expires_on', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), server_default=sa.text("'manual'"), nullable=True),
    sa.Column('raw', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('equity_grants_company', 'equity_grants', ['company_id'], unique=False)


def downgrade() -> None:
    op.drop_index('equity_grants_company', table_name='equity_grants')
    op.drop_table('equity_grants')
    op.drop_table('equity_companies')
