"""Investments no longer has a way to leave an account out: hide it in Settings → Accounts instead.

inv_accounts.hidden used to be the Investments page's own "leave this account out" box, and Runway itself set it on a
SimpleFIN account whose institution loosely matched a Plaid connection. Nothing reads it any more: a SimpleFIN account
that a Plaid one also is stays out through the twin test in portfolio._accounts, and one you hide in Settings → Accounts
stays out through accounts.hidden. So every row is cleared, so none stays out for a reason nothing can undo. The column
stays in the schema, unused.

Revision ID: 0033
Revises: 0032
"""
import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'inv_accounts' not in sa.inspect(bind).get_table_names():
        return
    bind.execute(sa.text("UPDATE inv_accounts SET hidden = 0 WHERE hidden = 1"))


def downgrade() -> None:
    pass   # which accounts you had left out isn't kept
