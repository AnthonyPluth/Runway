"""Deleting an account, and keeping it deleted.

Runway's accounts come from the banks (SimpleFIN, Plaid), so you can't add one; but you can delete any of them. That
takes the account and everything that belongs to it: its transactions (and their splits), its card statements (Plaid's
and the ones you entered), its investment data (holdings, activity, daily values, the funds you entered, cost basis
you set), its recurring items and the rules that only apply to it, the one-off amounts you changed on its forecast,
and its settings (how a card is paid, its APR). Whatever else pointed at it lets go: a budget paid with it, a home's
loan, a churning card or bank bonus linked to it, a card paid from it, and the forecast's account.

The database's foreign keys (schema.py, migration 0039) do part of it when the account's row goes: its transactions,
recurring items, rules and entered statements go with it (ON DELETE CASCADE), and the columns pointing at it let go
(SET NULL). The rest is kept by id in places a foreign key can't reach (settings keys, forecast overrides, Plaid and
investment data, a transaction's splits) and is done here by hand.

What stops the next sync bringing it straight back is a row in deleted_accounts (the tombstone): SimpleFIN's sync skips
the account, Plaid's skips its investment accounts, and its Plaid bank or card account is set ignored. Restoring removes
the row; the next sync then brings the account back with whatever history its bank still offers (SimpleFIN: about six
months; Plaid: up to two years). Nothing deleted comes back from Runway itself: only a backup has it.

A SimpleFIN account that was linked to a Plaid account (a card whose statements come from Plaid) keeps its row when
restored, marked restored_at: the Plaid account stays ignored (its statements held back, nothing waiting in "New from
Plaid") until SimpleFIN brings the account back, and then it's linked again (relink) and the row goes.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, func, or_, select, update

from . import db, plaidbank, sfinvest
from . import settings_keys as sk
from .schema import now_text
from .models import (Account, CardStatement, CostOverride, DeletedAccount, Holding, HoldingSnapshot, InvAccount, InvSnapshot,
                     InvTransaction, ManualContribution, ManualPosition, ManualState, ManualStatement, Override, PlaidAccount,
                     Recurring, RecurringDismissed, RetailCharge, Rule, Setting, Transaction, TxSplit)

INVESTMENT_DATA = (Holding, InvTransaction, InvSnapshot, HoldingSnapshot, ManualPosition, ManualContribution, ManualState,
                   CostOverride)


def _inv_ids(conn, acct) -> list[str]:
    """The investment accounts (inv_accounts.id) that are this account: SimpleFIN's "sf:<id>", and a Plaid one linked to it
    (or, for an account of its own from Plaid, "pl:<id>", the one it's made from)."""
    aid = acct["id"]
    cands = select(InvAccount.id).where(or_(InvAccount.id == sfinvest.inv_id(aid), InvAccount.account_id == aid,
                                            InvAccount.id == (aid[3:] if aid.startswith("pl:") else None)))
    return sorted(set(conn.execute(cands).scalars()))


def _tx_ids(account_id: str):
    return select(Transaction.id).where(Transaction.account_id == account_id)


def impact(conn, account_id: str) -> dict | None:
    """What deleting the account takes with it, for the confirmation (None: no such account)."""
    acct = conn.execute(select(Account).where(Account.id == account_id)).fetchone()
    if not acct:
        return None
    count = lambda q: conn.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    inv = _inv_ids(conn, acct)
    return {
        "name": db.account_label(acct),
        "transactions": count(_tx_ids(account_id)),
        "recurring": count(select(Recurring.id).where(Recurring.account_id == account_id)),
        "rules": count(select(Rule.id).where(Rule.account_id == account_id)),
        "statements": count(select(ManualStatement.statement_date).where(ManualStatement.account_id == account_id)),
        "holdings": count(select(Holding.security_id).where(Holding.account_id.in_(inv))) if inv else 0,
        "plaid": bool(acct["plaid_account_id"]) or account_id.startswith("pl:"),
    }


def remove(conn, account_id: str) -> dict:
    """Delete the account and everything that belongs to it, and keep it deleted (see above). Raises LookupError if
    there's no such account."""
    acct = conn.execute(select(Account).where(Account.id == account_id)).fetchone()
    if not acct:
        raise LookupError("Account not found")
    gone = impact(conn, account_id) or {}
    inv = _inv_ids(conn, acct)
    pid = acct["plaid_account_id"] or (account_id[3:] if account_id.startswith("pl:") and conn.execute(
        select(PlaidAccount.plaid_account_id).where(PlaidAccount.plaid_account_id == account_id[3:])).fetchone() else None)

    # Its transactions' splits, and the retailer charges matched to them (which stay, unmatched); the transactions
    # themselves go with the account (as do its rules: left without it, a rule would apply to every account).
    txs = _tx_ids(account_id)
    conn.execute(update(RetailCharge).where(RetailCharge.tx_id.in_(txs)).values(tx_id=None, match_source=None, applied=None))
    conn.execute(delete(TxSplit).where(TxSplit.tx_id.in_(txs)))
    # Its recurring items (they go with it too): what was said about their dates (amounts changed, missed payments
    # dismissed), and any other account's transactions matched to them.
    rec_ids = list(conn.execute(select(Recurring.id).where(Recurring.account_id == account_id)).scalars())
    for rid in rec_ids:
        conn.execute(delete(Override).where(Override.key.startswith(f"rec:{rid}:", autoescape=True)))
        conn.execute(delete(RecurringDismissed).where(RecurringDismissed.key.startswith(f"rec:{rid}:", autoescape=True)))
    if rec_ids:
        conn.execute(update(Transaction).where(Transaction.recurring_id.in_(rec_ids)).values(recurring_id=None))
    # Its card statements (the ones you entered go with it), and the amounts you changed on its forecast.
    for prefix in ("card", "stmt"):
        conn.execute(delete(Override).where(Override.key.startswith(f"{prefix}:{account_id}:", autoescape=True)))
    if pid:
        conn.execute(delete(CardStatement).where(CardStatement.plaid_account_id == pid))
        conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == pid).values(ignored=1))
    # Its investment data.
    for iid in inv:
        for model in INVESTMENT_DATA:
            conn.execute(delete(model).where(model.account_id == iid))
        conn.execute(delete(InvAccount).where(InvAccount.id == iid))
    # Its settings, and the forecast's account if it was this one.
    conn.execute(delete(Setting).where(Setting.key.in_([key(account_id) for key in sk.PER_ACCOUNT])))
    seen = json.loads(db.get_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}")
    if seen.pop(account_id, None) is not None:
        db.set_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN, json.dumps(seen))
    if db.get_setting(conn, sk.PRIMARY_ACCOUNT) == account_id:
        db.set_setting(conn, sk.PRIMARY_ACCOUNT, None)
    # The account, and with it (foreign keys) its transactions, recurring items, rules and entered statements; whatever
    # else pointed at it lets go.
    conn.execute(delete(Account).where(Account.id == account_id))
    db.upsert(conn, DeletedAccount, {"id": account_id, "name": db.account_label(acct), "kind": acct["kind"],
                                     "plaid_account_id": pid, "inv_ids": json.dumps(inv) if inv else None, "restored_at": None},
              key=["id"], update=["name", "kind", "plaid_account_id", "inv_ids", "restored_at"])
    return {"ok": True, **gone}


def listed(conn) -> list[dict]:
    """The accounts you deleted, latest first, for Settings."""
    d = DeletedAccount
    return db.rows(conn.execute(select(d.id, d.name, d.kind, d.deleted_at).where(d.restored_at.is_(None))
                                .order_by(d.deleted_at.desc(), d.id)))


def restore(conn, account_id: str, today: date | None = None) -> dict:
    """Stop keeping the account deleted. The next sync brings it back with whatever history the bank still has; an
    account of its own from Plaid comes back now (empty until that sync). Raises LookupError if it isn't deleted."""
    row = conn.execute(select(DeletedAccount).where(DeletedAccount.id == account_id, DeletedAccount.restored_at.is_(None))).fetchone()
    if not row:
        raise LookupError("That account isn't deleted.")
    pid = row["plaid_account_id"]
    plaid_there = bool(pid and conn.execute(select(PlaidAccount.plaid_account_id).where(PlaidAccount.plaid_account_id == pid)).fetchone())
    if plaid_there and not account_id.startswith("pl:"):
        # Linked to Plaid: kept, marked restored, until SimpleFIN brings it back and relink() links it again.
        conn.execute(update(DeletedAccount).where(DeletedAccount.id == account_id).values(restored_at=now_text()))
    else:
        conn.execute(delete(DeletedAccount).where(DeletedAccount.id == account_id))
    if plaid_there and account_id == "pl:" + pid:
        conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == pid).values(ignored=0))
        plaidbank.match(conn, pid, "new", today)   # its own account again; the next sync reads its history again
    if not account_id.startswith("pl:"):
        # SimpleFIN: read its whole history (BACKFILL_DAYS) again on the next sync, not just the last couple of weeks.
        st = json.loads(db.get_setting(conn, sk.SIMPLEFIN_BACKFILL) or "{}")
        if account_id in (st.get("done") or []):
            st["done"] = [x for x in st["done"] if x != account_id]
            db.set_setting(conn, sk.SIMPLEFIN_BACKFILL, json.dumps(st))
    return {"ok": True, "name": row["name"]}


def relink(conn, account_id: str) -> None:
    """SimpleFIN brought back an account you restored: link it to the Plaid account it was linked to before (unless
    that's been given to another account since), and stop keeping that one ignored."""
    row = conn.execute(select(DeletedAccount.plaid_account_id)
                       .where(DeletedAccount.id == account_id, DeletedAccount.restored_at.is_not(None))).fetchone()
    if not row:
        return
    conn.execute(delete(DeletedAccount).where(DeletedAccount.id == account_id))
    pid = row["plaid_account_id"]
    if not pid or not conn.execute(select(PlaidAccount.plaid_account_id).where(PlaidAccount.plaid_account_id == pid)).fetchone() \
            or conn.execute(select(Account.id).where(Account.plaid_account_id == pid)).fetchone():
        return
    conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == pid).values(ignored=0))
    conn.execute(update(Account).where(Account.id == account_id).values(plaid_account_id=pid))


def ids(conn) -> set[str]:
    """The accounts you deleted (accounts.id), which SimpleFIN's sync leaves out (not the ones you restored)."""
    return set(conn.execute(select(DeletedAccount.id).where(DeletedAccount.restored_at.is_(None))).scalars())


def inv_ids(conn) -> set[str]:
    """Investment accounts (inv_accounts.id) that belonged to an account you deleted, which Plaid's sync leaves out."""
    out: set[str] = set()
    for v in conn.execute(select(DeletedAccount.inv_ids)
                          .where(DeletedAccount.inv_ids.is_not(None), DeletedAccount.restored_at.is_(None))).scalars():
        out.update(json.loads(v))
    return out
