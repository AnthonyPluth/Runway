"""Leftovers nothing reads any more go: accounts.daily_spend (the forecast stopped taking out everyday spending),
churn_cards.fee_month (a card's annual fee posts on its anniversary), and on Postgres the instr() SQL function the
baseline made (queries compile db.instr to Postgres's own strpos).

Revision ID: 0037
Revises: 0036
"""
import sqlalchemy as sa
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None

POSTGRES_INSTR = ("CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer AS 'SELECT strpos($1, $2)' "
                  "LANGUAGE sql IMMUTABLE")


def _has(table: str, column: str) -> bool:
    return column in {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _has('accounts', 'daily_spend'):
        with op.batch_alter_table('accounts') as batch:
            batch.drop_column('daily_spend')
    if _has('churn_cards', 'fee_month'):
        with op.batch_alter_table('churn_cards') as batch:
            batch.drop_column('fee_month')
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS instr(text, text)")


def downgrade() -> None:
    op.add_column('accounts', sa.Column('daily_spend', sa.Integer(), server_default=sa.text('0')))
    op.add_column('churn_cards', sa.Column('fee_month', sa.Integer()))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(POSTGRES_INSTR)
