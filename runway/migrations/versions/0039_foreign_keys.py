"""Foreign keys: the columns that refer to an account (and a few others: an order's items and charges, a company's
grants, a card's rates, tasks and benefits, a benefit's uses, a home's values) now must refer to one that's there.
Removing it takes along what belongs to it (ON DELETE CASCADE: an account's transactions, recurring items, rules and
statements) and makes the rest let go (SET NULL: a card paid from it, a budget category paid with it, a home's loan,
a churning card or bank bonus linked to it), as deleting an account in Settings did by hand (deleted_accounts.remove).

Rows that already refer to something that's gone (left by a version that didn't clean up after itself, or by hand)
can't stay: they're removed, or let go, the same way, with what deleting the account would have taken along with them
(a transaction's splits, a recurring item's changed dates, a card's payment settings). Before anything is removed, a
backup of the whole database is saved in the data directory (runway-before-migration-0039-<time>.json.gz); the log
says how many rows went from each table, and nothing more.

On SQLite each table is made again with its keys (batch mode), with foreign keys off while migrating (db.migrate).

Revision ID: 0039
Revises: 0038
"""
import os

import sqlalchemy as sa
from alembic import context, op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None

# (table, column, the table it refers to, its column, ON DELETE), each after the ones it depends on.
KEYS = [
    ('accounts', 'pay_from', 'accounts', 'id', 'SET NULL'),
    ('transactions', 'account_id', 'accounts', 'id', 'CASCADE'),
    ('recurring', 'account_id', 'accounts', 'id', 'CASCADE'),
    ('rules', 'account_id', 'accounts', 'id', 'CASCADE'),
    ('manual_statements', 'account_id', 'accounts', 'id', 'CASCADE'),
    ('categories', 'pay_with', 'accounts', 'id', 'SET NULL'),
    ('assets', 'loan_account_id', 'accounts', 'id', 'SET NULL'),
    ('churn_cards', 'account_id', 'accounts', 'id', 'SET NULL'),
    ('churn_cards', 'changed_from', 'churn_cards', 'id', 'SET NULL'),
    ('churn_cards', 'plan_new_id', 'churn_cards', 'id', 'SET NULL'),
    ('churn_bank_bonuses', 'account_id', 'accounts', 'id', 'SET NULL'),
    ('churn_rates', 'card_id', 'churn_cards', 'id', 'CASCADE'),
    ('churn_tasks', 'card_id', 'churn_cards', 'id', 'CASCADE'),
    ('churn_benefits', 'card_id', 'churn_cards', 'id', 'CASCADE'),
    ('churn_benefit_uses', 'benefit_id', 'churn_benefits', 'id', 'CASCADE'),
    ('asset_values', 'asset_id', 'assets', 'id', 'CASCADE'),
    ('equity_grants', 'company_id', 'equity_companies', 'id', 'CASCADE'),
    ('retail_items', 'order_id', 'retail_orders', 'id', 'CASCADE'),
    ('retail_charges', 'order_id', 'retail_orders', 'id', 'CASCADE'),
]
# Settings and forecast changes kept by account id (settings_keys.PER_ACCOUNT as it was, and overrides' keys).
SETTINGS = ('card_pay_mode:', 'card_pay_amount:', 'card_apr:')
OVERRIDES = ('card:', 'stmt:')


def _orphans(table, col, parent, pcol):
    return f"{col} IS NOT NULL AND {col} NOT IN (SELECT {pcol} FROM {parent} WHERE {pcol} IS NOT NULL)"


def _keyed(prefix: str) -> str:
    """Keys '<prefix><account id>' (settings) or '<prefix><account id>:<date>' (overrides) whose account isn't there."""
    starts = f"substr(key, 1, {len(prefix)}) = '{prefix}'"
    if prefix in OVERRIDES:   # an account id can hold a colon itself ("pl:..."): whichever account's id follows the prefix
        return (f"{starts} AND NOT EXISTS (SELECT 1 FROM accounts a WHERE "
                f"substr(key, 1, {len(prefix)} + length(a.id) + 1) = '{prefix}' || a.id || ':')")
    return f"{starts} AND substr(key, {len(prefix) + 1}) NOT IN (SELECT id FROM accounts)"


def _anything_to_remove(bind) -> bool:
    checks = [f"SELECT 1 FROM {t} WHERE {_orphans(t, c, p, pc)}" for t, c, p, pc, _ in KEYS]
    checks += [f"SELECT 1 FROM settings WHERE {_keyed(p)}" for p in SETTINGS]
    checks += [f"SELECT 1 FROM overrides WHERE {_keyed(p)}" for p in OVERRIDES]
    return any(bind.execute(sa.text(q + " LIMIT 1")).first() for q in checks)


def _save_copy(bind) -> None:
    """A backup of the whole database before anything is removed (not of a backup being restored: that's the copy)."""
    if context.config.attributes.get("restoring"):
        return
    # Runway's own backup, as a migration that removes data needs one; it reads whatever tables there are now.
    from runway import backup, db, monitoring
    where = os.path.dirname(bind.engine.url.database or "") if bind.dialect.name == "sqlite" else None
    path = backup.save_copy(db.Connection(bind), "runway-before-migration-0039", where or None)
    monitoring.log(f"Migration 0039: saved a backup of the database first, to {path}", "warning",
                   remote="Migration 0039: saved a backup of the database first")


def _remove_orphans(bind) -> dict[tuple[str, str], int]:
    """Returns how many rows were removed ("removed") or let go ("cleared"), by table."""
    done: dict[tuple[str, str], int] = {}

    def run(table, sql, params=None):
        n = bind.execute(sa.text(sql), params or {}).rowcount or 0
        if n:
            how = (table, "removed" if sql.startswith("DELETE") else "cleared")
            done[how] = done.get(how, 0) + n

    # What deleting an account takes along with its transactions and recurring items, for the ones going now.
    lost_tx = "SELECT id FROM transactions WHERE " + _orphans('transactions', 'account_id', 'accounts', 'id')
    run('tx_splits', f"DELETE FROM tx_splits WHERE tx_id IN ({lost_tx})")
    run('retail_charges', f"UPDATE retail_charges SET tx_id = NULL, match_source = NULL, applied = NULL WHERE tx_id IN ({lost_tx})")
    lost_rec = [r[0] for r in bind.execute(sa.text(
        "SELECT id FROM recurring WHERE " + _orphans('recurring', 'account_id', 'accounts', 'id'))).fetchall()]
    for rid in lost_rec:
        for t in ('overrides', 'recurring_dismissed'):
            run(t, f"DELETE FROM {t} WHERE substr(key, 1, :n) = :prefix", {"n": len(f"rec:{rid}:"), "prefix": f"rec:{rid}:"})
        run('transactions', "UPDATE transactions SET recurring_id = NULL WHERE recurring_id = :rid", {"rid": rid})
    for t, col, parent, pcol, ondelete in KEYS:
        if ondelete == 'CASCADE':
            run(t, f"DELETE FROM {t} WHERE {_orphans(t, col, parent, pcol)}")
        else:
            run(t, f"UPDATE {t} SET {col} = NULL WHERE {_orphans(t, col, parent, pcol)}")
    for p in SETTINGS:
        run('settings', f"DELETE FROM settings WHERE {_keyed(p)}")
    for p in OVERRIDES:
        run('overrides', f"DELETE FROM overrides WHERE {_keyed(p)}")
    return done


def upgrade() -> None:
    bind = op.get_bind()
    if _anything_to_remove(bind):
        _save_copy(bind)
        done = _remove_orphans(bind)
        if not context.config.attributes.get("restoring"):
            from runway import monitoring
            for (t, how), n in sorted(done.items()):   # counts only: never ids or values
                rows = f"{n} row{'s' if n != 1 else ''}"
                monitoring.log(f"Migration 0039: removed {rows} from {t} that referred to something no longer there." if how == "removed"
                               else f"Migration 0039: cleared what {rows} in {t} referred to: it's no longer there.", "warning")
    for t in dict.fromkeys(k[0] for k in KEYS):   # on SQLite, each table made again once, with all its keys
        with op.batch_alter_table(t) as batch:
            for _, col, parent, pcol, ondelete in (k for k in KEYS if k[0] == t):
                batch.create_foreign_key(f"fk_{t}_{col}", parent, [col], [pcol], ondelete=ondelete,
                                         deferrable=True, initially="IMMEDIATE")


def downgrade() -> None:
    # The keys go; rows removed on the way up don't come back (the backup saved then has them).
    for t in reversed(dict.fromkeys(k[0] for k in KEYS)):
        with op.batch_alter_table(t) as batch:
            for _, col, *_rest in (k for k in KEYS if k[0] == t):
                batch.drop_constraint(f"fk_{t}_{col}", type_="foreignkey")
