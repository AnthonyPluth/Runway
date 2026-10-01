"""v4 made the daily "everyday spending" drain opt-in and switched it off for existing accounts once, at start-up (db.init),
noting it had in the migrated_daily_spend_off setting. That one-time switch-off is done here instead: accounts in a
database that never had it are switched off, and a database that did keeps what's been chosen since. The note goes.

Revision ID: 0026
Revises: 0025
"""
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None

# db.get_setting's test: a value that's there and not empty
DONE = "SELECT 1 FROM settings WHERE key='migrated_daily_spend_off' AND value IS NOT NULL AND value <> ''"


def upgrade() -> None:
    op.execute(f"UPDATE accounts SET daily_spend=0 WHERE NOT EXISTS ({DONE})")
    op.execute("DELETE FROM settings WHERE key='migrated_daily_spend_off'")


def downgrade() -> None:
    # The switch-off has been done: say so, or the version this goes back to would do it again at start-up
    op.execute("INSERT INTO settings(key, value) SELECT 'migrated_daily_spend_off', '1' "
               "WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key='migrated_daily_spend_off')")
