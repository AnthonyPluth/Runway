"""A Plaid account added as an account of its own ("pl:…") and later matched to another of your accounts kept syncing
next to it, and net worth counted it twice. The "pl:" copy stops syncing and is hidden (its history stays), and each
Plaid account can now belong to only one of your accounts.

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE accounts SET plaid_account_id=NULL, hidden=1 WHERE id LIKE 'pl:%' AND plaid_account_id IN "
               "(SELECT plaid_account_id FROM accounts WHERE id NOT LIKE 'pl:%' AND plaid_account_id IS NOT NULL)")
    # Anything else still shared (it shouldn't be): the first account keeps it.
    op.execute("UPDATE accounts SET plaid_account_id=NULL WHERE plaid_account_id IS NOT NULL AND id <> "
               "(SELECT MIN(b.id) FROM accounts b WHERE b.plaid_account_id=accounts.plaid_account_id)")
    op.create_index('accounts_plaid_account', 'accounts', ['plaid_account_id'], unique=True)


def downgrade() -> None:
    op.drop_index('accounts_plaid_account', table_name='accounts')
