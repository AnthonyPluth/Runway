"""Recurring items match on merchant text alone, with an amount range you set ("only amounts between $x and $y"), keep
when their amount was last set (amount_since), and transactions keep how they were linked to one (by you, or
automatically).

Until now an item only matched payments close to its amount: within 30% of a fixed amount, 60% of one learned from the
payments. Existing items get that range written down, so nothing they didn't match before starts matching (a "Prime"
item matching "amazon" doesn't take every Amazon order); you can widen or clear it in the item.

Revision ID: 0031
Revises: 0030
"""
import sqlalchemy as sa
from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c['name'] for c in insp.get_columns('recurring')}
    added = 'amount_min' not in have
    for name, kind in (('amount_min', sa.Float()), ('amount_max', sa.Float()), ('amount_since', sa.Text())):
        if name not in have:
            op.add_column('recurring', sa.Column(name, kind))
    if 'recurring_linked_by' not in {c['name'] for c in insp.get_columns('transactions')}:
        op.add_column('transactions', sa.Column('recurring_linked_by', sa.Text()))
    if not added:
        return
    # The range each item matched until now (recurring.amount_range before this): only where it had one.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, amount, amount_mode FROM recurring")).fetchall()
    for rid, amount, mode in rows:
        amount = abs(amount or 0)
        if amount < 0.005:
            continue
        tol = 0.3 if (mode or 'fixed') == 'fixed' else 0.6
        bind.execute(sa.text("UPDATE recurring SET amount_min = :lo, amount_max = :hi WHERE id = :id"),
                     {"lo": round(amount * (1 - tol), 2), "hi": round(amount * (1 + tol), 2), "id": rid})


def downgrade() -> None:
    with op.batch_alter_table('transactions') as batch:
        batch.drop_column('recurring_linked_by')
    with op.batch_alter_table('recurring') as batch:
        batch.drop_column('amount_since')
        batch.drop_column('amount_max')
        batch.drop_column('amount_min')
