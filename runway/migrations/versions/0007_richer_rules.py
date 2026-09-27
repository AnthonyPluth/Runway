"""Richer rules: conditions on amount, direction and account besides the text, and actions besides a category
(rename the merchant, split, mark for review). Several rules may now share the same text.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

from runway.schema import now_text

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

NEW = [
    sa.Column('match_mode', sa.Text(), server_default=sa.text("'contains'"), nullable=True),
    sa.Column('amount_min', sa.Float(), nullable=True),
    sa.Column('amount_max', sa.Float(), nullable=True),
    sa.Column('direction', sa.Text(), nullable=True),
    sa.Column('account_id', sa.Text(), nullable=True),
    sa.Column('rename', sa.Text(), nullable=True),
    sa.Column('review', sa.Integer(), server_default=sa.text('0'), nullable=True),
    sa.Column('split', sa.Text(), nullable=True),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for uc in sa.inspect(bind).get_unique_constraints('rules'):
            op.drop_constraint(uc['name'], 'rules', type_='unique')
        op.alter_column('rules', 'category', existing_type=sa.Text(), nullable=True)
        for col in NEW:
            op.add_column('rules', col)
        return
    # SQLite can't drop a constraint in place: the table is rebuilt, without the one-rule-per-text constraint.
    old = sa.Table('rules', sa.MetaData(),
                   sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                   sa.Column('match', sa.Text(), nullable=False),
                   sa.Column('category', sa.Text(), nullable=False),
                   sa.Column('created_at', sa.Text(), server_default=now_text()),
                   sqlite_autoincrement=True)
    with op.batch_alter_table('rules', recreate='always', copy_from=old) as batch_op:
        batch_op.alter_column('category', existing_type=sa.Text(), nullable=True)
        for col in NEW:
            batch_op.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table('rules', schema=None) as batch_op:
        for col in NEW:
            batch_op.drop_column(col.name)
