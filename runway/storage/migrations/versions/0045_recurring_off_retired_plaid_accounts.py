"""Recurring items left on a Plaid account's own account ("pl:…") after it was matched to another of your accounts
(hidden then, its Plaid id moving to that one: 0012, plaidbank.match) move to that account, where their payments come in
now; matching does this itself from now on. What's tied to an item (linked payments, edits, skips, dismissals) is by
the item, so it comes along. Nothing else changes.

Revision ID: 0045
Revises: 0044
"""
from alembic import op

revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The account a hidden "pl:<Plaid id>" account is now: the one of yours, not hidden, that has its Plaid id.
    op.execute("UPDATE recurring SET account_id = (SELECT a.id FROM accounts a WHERE a.plaid_account_id = "
               "substr(recurring.account_id, 4) AND a.id NOT LIKE 'pl:%' AND a.hidden = 0) "
               "WHERE account_id IN (SELECT id FROM accounts WHERE id LIKE 'pl:%' AND hidden = 1) "
               "AND EXISTS (SELECT 1 FROM accounts a WHERE a.plaid_account_id = substr(recurring.account_id, 4) "
               "AND a.id NOT LIKE 'pl:%' AND a.hidden = 0)")


def downgrade() -> None:
    pass   # which items were moved isn't kept, and they're right where they are
