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


def _table(name: str, *cols: str) -> sa.TableClause:
    """The table as far as this migration needs it (not the app's models: they're whatever the schema is now). Its
    columns are untyped, so a NULL set or a value compared is sent as it is; a key or id is joined to text (_keyed) as
    text."""
    return sa.table(name, *(sa.column(c, sa.Text) if c in ("key", "id") and name in ("settings", "overrides", "accounts")
                            else sa.column(c) for c in cols))


def _orphans(t: sa.TableClause, col: str, parent: str, pcol: str):
    """Where `col` refers to a row of `parent` that isn't there."""
    p = _table(parent, pcol).alias("p")
    return sa.and_(t.c[col].is_not(None), t.c[col].not_in(sa.select(p.c[pcol]).where(p.c[pcol].is_not(None))))


def _keyed(t: sa.TableClause, prefix: str):
    """Keys '<prefix><account id>' (settings) or '<prefix><account id>:<date>' (overrides) whose account isn't there."""
    a = _table("accounts", "id").alias("a")
    starts = sa.func.substr(t.c.key, 1, len(prefix)) == sa.bindparam(None, prefix, sa.Text)
    if prefix in OVERRIDES:   # an account id can hold a colon itself ("pl:..."): whichever account's id follows the prefix
        theirs = sa.func.substr(t.c.key, 1, len(prefix) + sa.func.length(a.c.id) + 1) == (
            sa.bindparam(None, prefix, sa.Text) + a.c.id + sa.bindparam(None, ":", sa.Text))
        return sa.and_(starts, ~sa.exists().where(theirs))
    return sa.and_(starts, sa.func.substr(t.c.key, len(prefix) + 1).not_in(sa.select(a.c.id)))


def _checks():
    """Every condition a row is removed or let go under, with its table."""
    for t, col, parent, pcol, _ in KEYS:
        table = _table(t, col)
        yield table, _orphans(table, col, parent, pcol)
    settings, overrides = _table("settings", "key"), _table("overrides", "key")
    for p in SETTINGS:
        yield settings, _keyed(settings, p)
    for p in OVERRIDES:
        yield overrides, _keyed(overrides, p)


def _anything_to_remove(bind) -> bool:
    return any(bind.execute(sa.select(sa.literal(1)).select_from(t).where(cond).limit(1)).first() for t, cond in _checks())


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

    def run(stmt):
        n = bind.execute(stmt).rowcount or 0
        if n:
            how = (stmt.table.name, "removed" if isinstance(stmt, sa.Delete) else "cleared")
            done[how] = done.get(how, 0) + n

    # What deleting an account takes along with its transactions and recurring items, for the ones going now.
    tx = _table("transactions", "id", "account_id", "recurring_id")
    lost_tx = sa.select(tx.c.id).where(_orphans(tx, "account_id", "accounts", "id"))
    splits = _table("tx_splits", "tx_id")
    run(sa.delete(splits).where(splits.c.tx_id.in_(lost_tx)))
    charges = _table("retail_charges", "tx_id", "match_source", "applied")
    run(sa.update(charges).where(charges.c.tx_id.in_(lost_tx)).values(tx_id=None, match_source=None, applied=None))
    rec = _table("recurring", "id", "account_id")
    lost_rec = [r[0] for r in bind.execute(sa.select(rec.c.id).where(_orphans(rec, "account_id", "accounts", "id"))).fetchall()]
    for rid in lost_rec:
        for name in ("overrides", "recurring_dismissed"):
            keyed = _table(name, "key")
            prefix = f"rec:{rid}:"
            run(sa.delete(keyed).where(sa.func.substr(keyed.c.key, 1, len(prefix)) == sa.bindparam(None, prefix, sa.Text)))
        run(sa.update(tx).where(tx.c.recurring_id == sa.bindparam(None, rid)).values(recurring_id=None))
    for t, col, parent, pcol, ondelete in KEYS:
        table = _table(t, col)
        where = _orphans(table, col, parent, pcol)
        run(sa.delete(table).where(where) if ondelete == 'CASCADE' else sa.update(table).where(where).values({col: None}))
    for table, cond in list(_checks())[len(KEYS):]:   # the settings and overrides keyed by an account that's gone
        run(sa.delete(table).where(cond))
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
