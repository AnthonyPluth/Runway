"""Churning: what you plan to do about each card (keep, downgrade, close) and hiding one from Upcoming; portal-only
earning rates; card benefits and their use; snoozing a to-do; a kind for currencies you added.

A card may now earn two rates in one category (its own and a higher one through the issuer's portal), so
churn_rates' primary key gains portal_only.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

from runway.schema import now_text

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

CARD_COLUMNS: list[sa.Column] = [
    sa.Column('portal_name', sa.Text()),
    sa.Column('plan', sa.Text(), server_default=sa.text("'undecided'")),
    sa.Column('plan_target', sa.Text()),
    sa.Column('plan_date', sa.Text()),
    sa.Column('plan_remind_days', sa.Integer(), server_default=sa.text('14')),
    sa.Column('plan_done_on', sa.Text()),
    sa.Column('plan_new_id', sa.Integer()),
    sa.Column('hide_upcoming', sa.Integer(), server_default=sa.text('0')),
]


def _columns(table: str) -> set[str]:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _columns('churn_cards')
    for col in CARD_COLUMNS:
        if col.name not in have:
            op.add_column('churn_cards', col.copy())
    if 'kind' not in _columns('churn_currencies'):
        op.add_column('churn_currencies', sa.Column('kind', sa.Text()))
    if 'snooze_until' not in _columns('churn_tasks'):
        op.add_column('churn_tasks', sa.Column('snooze_until', sa.Text()))

    if 'portal_only' not in _columns('churn_rates'):
        if bind.dialect.name == 'postgresql':
            op.add_column('churn_rates', sa.Column('portal_only', sa.Integer(), nullable=False, server_default=sa.text('0')))
            pk = sa.inspect(bind).get_pk_constraint('churn_rates')['name'] or 'churn_rates_pkey'
            op.drop_constraint(pk, 'churn_rates', type_='primary')
            op.create_primary_key(pk, 'churn_rates', ['card_id', 'category', 'portal_only'])
        else:
            # SQLite can't change a primary key in place: the table is rebuilt, its rows kept (all non-portal rates).
            old = sa.Table('churn_rates', sa.MetaData(),
                           sa.Column('card_id', sa.Integer(), nullable=False),
                           sa.Column('category', sa.Text(), nullable=False),
                           sa.Column('multiplier', sa.Float(), nullable=False),
                           sa.PrimaryKeyConstraint('card_id', 'category'))
            with op.batch_alter_table('churn_rates', recreate='always', copy_from=old) as b:
                b.add_column(sa.Column('portal_only', sa.Integer(), nullable=False, server_default=sa.text('0')))
                b.create_primary_key('pk_churn_rates', ['card_id', 'category', 'portal_only'])

    tables = sa.inspect(bind).get_table_names()
    if 'churn_benefits' not in tables:
        op.create_table('churn_benefits',
                        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                        sa.Column('card_id', sa.Integer(), nullable=False),
                        sa.Column('name', sa.Text(), nullable=False),
                        sa.Column('kind', sa.Text(), server_default=sa.text("'credit'")),
                        sa.Column('amount', sa.Float()),
                        sa.Column('period', sa.Text(), server_default=sa.text("'annual'")),
                        sa.Column('basis', sa.Text(), server_default=sa.text("'calendar'")),
                        sa.Column('annual_value', sa.Float()),
                        sa.Column('counts', sa.Integer(), server_default=sa.text('1')),
                        sa.Column('remind', sa.Integer(), server_default=sa.text('1')),
                        sa.Column('remind_days', sa.Integer()),
                        sa.Column('expires_on', sa.Text()),
                        sa.Column('preset', sa.Text()),
                        sa.Column('notes', sa.Text()),
                        sa.Column('active', sa.Integer(), server_default=sa.text('1')),
                        sa.Column('created_at', sa.Text(), server_default=now_text()),
                        sa.PrimaryKeyConstraint('id'),
                        sqlite_autoincrement=True)
        op.create_index('churn_benefits_card', 'churn_benefits', ['card_id'], unique=False)
    if 'churn_benefit_uses' not in tables:
        op.create_table('churn_benefit_uses',
                        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
                        sa.Column('benefit_id', sa.Integer(), nullable=False),
                        sa.Column('period_start', sa.Text(), nullable=False),
                        sa.Column('amount_used', sa.Float()),
                        sa.Column('used_on', sa.Text(), nullable=False),
                        sa.PrimaryKeyConstraint('id'),
                        sqlite_autoincrement=True)
        op.create_index('churn_benefit_uses_benefit', 'churn_benefit_uses', ['benefit_id'], unique=False)


def downgrade() -> None:
    op.drop_index('churn_benefit_uses_benefit', table_name='churn_benefit_uses')
    op.drop_table('churn_benefit_uses')
    op.drop_index('churn_benefits_card', table_name='churn_benefits')
    op.drop_table('churn_benefits')
    bind = op.get_bind()
    op.execute(sa.text('DELETE FROM churn_rates WHERE portal_only = 1'))
    if bind.dialect.name == 'postgresql':
        pk = sa.inspect(bind).get_pk_constraint('churn_rates')['name'] or 'churn_rates_pkey'
        op.drop_constraint(pk, 'churn_rates', type_='primary')
        op.drop_column('churn_rates', 'portal_only')
        op.create_primary_key(pk, 'churn_rates', ['card_id', 'category'])
    else:
        old = sa.Table('churn_rates', sa.MetaData(),
                       sa.Column('card_id', sa.Integer(), nullable=False),
                       sa.Column('category', sa.Text(), nullable=False),
                       sa.Column('multiplier', sa.Float(), nullable=False),
                       sa.Column('portal_only', sa.Integer(), nullable=False, server_default=sa.text('0')),
                       sa.PrimaryKeyConstraint('card_id', 'category', 'portal_only'))
        with op.batch_alter_table('churn_rates', recreate='always', copy_from=old) as b:
            b.drop_column('portal_only')
            b.create_primary_key('pk_churn_rates', ['card_id', 'category'])
    with op.batch_alter_table('churn_tasks') as b:
        b.drop_column('snooze_until')
    with op.batch_alter_table('churn_currencies') as b:
        b.drop_column('kind')
    with op.batch_alter_table('churn_cards') as b:
        for col in CARD_COLUMNS:
            b.drop_column(col.name)
