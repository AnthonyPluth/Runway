"""Churning: cards and bank bonuses you want next (and what's in the way of applying), and credit scores you entered.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

from runway.schema import now_text

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if 'churn_wishlist' not in tables:
        op.create_table('churn_wishlist',
                        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                        sa.Column('owner', sa.Text(), nullable=False),
                        sa.Column('kind', sa.Text(), server_default=sa.text("'card'")),
                        sa.Column('issuer', sa.Text()),
                        sa.Column('bank', sa.Text()),
                        sa.Column('product', sa.Text()),
                        sa.Column('family', sa.Text()),
                        sa.Column('business', sa.Integer(), server_default=sa.text('0')),
                        sa.Column('annual_fee', sa.Float()),
                        sa.Column('bonus', sa.Float()),
                        sa.Column('currency', sa.Text()),
                        sa.Column('bonus_spend', sa.Float()),
                        sa.Column('bonus_months', sa.Integer()),
                        sa.Column('account_type', sa.Text()),
                        sa.Column('requirements', sa.Text()),
                        sa.Column('repeat_months', sa.Integer()),
                        sa.Column('once_per_lifetime', sa.Integer(), server_default=sa.text('0')),
                        sa.Column('offer_expires_on', sa.Text()),
                        sa.Column('priority', sa.Integer()),
                        sa.Column('status', sa.Text(), server_default=sa.text("'wanted'")),
                        sa.Column('wait_until', sa.Text()),
                        sa.Column('min_score', sa.Integer()),
                        sa.Column('assume_prior_planned', sa.Integer(), server_default=sa.text('0')),
                        sa.Column('notes', sa.Text()),
                        sa.Column('applied_on', sa.Text()),
                        sa.Column('applied_id', sa.Integer()),
                        sa.Column('created_at', sa.Text(), server_default=now_text()),
                        sa.PrimaryKeyConstraint('id'),
                        sqlite_autoincrement=True)
    if 'churn_scores' not in tables:
        op.create_table('churn_scores',
                        sa.Column('owner', sa.Text(), nullable=False),
                        sa.Column('as_of', sa.Text(), nullable=False),
                        sa.Column('score', sa.Integer(), nullable=False),
                        sa.Column('source', sa.Text()),
                        sa.PrimaryKeyConstraint('owner', 'as_of'))


def downgrade() -> None:
    op.drop_table('churn_scores')
    op.drop_table('churn_wishlist')
