"""Deleting an account, and keeping it deleted.

Runway's accounts come from the banks (SimpleFIN, Plaid), so you can't add one; but you can delete any of them. That
takes the account and everything that belongs to it: its transactions (and their splits), its card statements (Plaid's
and the ones you entered), its investment data (holdings, activity, daily values, the funds you entered, cost basis
you set), its recurring items and the rules that only apply to it, and the one-off amounts you changed on its
forecast. Whatever else pointed at it lets go: a budget paid with it, a home's loan, a churning card or bank bonus
linked to it, a card paid from it, and the forecast's account.

What stops the next sync bringing it straight back is a row in deleted_accounts (the tombstone): SimpleFIN's sync skips
the account, Plaid's skips its investment accounts, and its Plaid bank or card account is set ignored. Restoring removes
the row; the next sync then brings the account back with whatever history its bank still offers (SimpleFIN: about six
months; Plaid: up to two years). Nothing deleted comes back from Runway itself: only a backup has it.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, func, or_, select, update

from . import db, plaidbank, sfinvest
from . import settings_keys as sk
from .models import (Account, Asset, Budget, CardStatement, ChurnBankBonus, ChurnCard, CostOverride, DeletedAccount, Holding,
                     HoldingSnapshot, InvAccount, InvSnapshot, InvTransaction, ManualContribution, ManualPosition, ManualState,
                     ManualStatement, Override, PlaidAccount, Recurring, RecurringDismissed, RetailCharge, Rule, Transaction,
                     TxSplit)

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

    # Its transactions, their splits, and the retailer charges matched to them (which stay, unmatched).
    txs = _tx_ids(account_id)
    conn.execute(update(RetailCharge).where(RetailCharge.tx_id.in_(txs)).values(tx_id=None, match_source=None, applied=None))
    conn.execute(delete(TxSplit).where(TxSplit.tx_id.in_(txs)))
    conn.execute(delete(Transaction).where(Transaction.account_id == account_id))
    # Its recurring items, and what was said about their dates (amounts changed, missed payments dismissed).
    rec_ids = list(conn.execute(select(Recurring.id).where(Recurring.account_id == account_id)).scalars())
    for rid in rec_ids:
        conn.execute(delete(Override).where(Override.key.startswith(f"rec:{rid}:", autoescape=True)))
        conn.execute(delete(RecurringDismissed).where(RecurringDismissed.key.startswith(f"rec:{rid}:", autoescape=True)))
    if rec_ids:
        conn.execute(update(Transaction).where(Transaction.recurring_id.in_(rec_ids)).values(recurring_id=None))
        conn.execute(delete(Recurring).where(Recurring.id.in_(rec_ids)))
    conn.execute(delete(Rule).where(Rule.account_id == account_id))   # left without it, a rule would apply to every account
    # Its card statements, and the amounts you changed on its forecast.
    for prefix in ("card", "stmt"):
        conn.execute(delete(Override).where(Override.key.startswith(f"{prefix}:{account_id}:", autoescape=True)))
    conn.execute(delete(ManualStatement).where(ManualStatement.account_id == account_id))
    if pid:
        conn.execute(delete(CardStatement).where(CardStatement.plaid_account_id == pid))
        conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == pid).values(ignored=1))
    # Its investment data.
    for iid in inv:
        for model in INVESTMENT_DATA:
            conn.execute(delete(model).where(model.account_id == iid))
        conn.execute(delete(InvAccount).where(InvAccount.id == iid))
    db.set_setting(conn, sk.sf_raw(account_id), None)
    seen = json.loads(db.get_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}")
    if seen.pop(account_id, None) is not None:
        db.set_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN, json.dumps(seen))
    # What pointed at it lets go.
    conn.execute(update(Budget).where(Budget.pay_with == account_id).values(pay_with=None))
    conn.execute(update(Asset).where(Asset.loan_account_id == account_id).values(loan_account_id=None))
    conn.execute(update(ChurnCard).where(ChurnCard.account_id == account_id).values(account_id=None))
    conn.execute(update(ChurnBankBonus).where(ChurnBankBonus.account_id == account_id).values(account_id=None))
    conn.execute(update(Account).where(Account.pay_from == account_id).values(pay_from=None))
    if db.get_setting(conn, sk.PRIMARY_ACCOUNT) == account_id:
        db.set_setting(conn, sk.PRIMARY_ACCOUNT, None)
    conn.execute(delete(Account).where(Account.id == account_id))
    db.upsert(conn, DeletedAccount, {"id": account_id, "name": db.account_label(acct), "kind": acct["kind"],
                                     "plaid_account_id": pid, "inv_ids": json.dumps(inv) if inv else None},
              key=["id"], update=["name", "kind", "plaid_account_id", "inv_ids"])
    return {"ok": True, **gone}


def listed(conn) -> list[dict]:
    """The accounts you deleted, latest first, for Settings."""
    d = DeletedAccount
    return db.rows(conn.execute(select(d.id, d.name, d.kind, d.deleted_at).order_by(d.deleted_at.desc(), d.id)))


def restore(conn, account_id: str, today: date | None = None) -> dict:
    """Stop keeping the account deleted. The next sync brings it back with whatever history the bank still has; an
    account of its own from Plaid comes back now (empty until that sync). Raises LookupError if it isn't deleted."""
    row = conn.execute(select(DeletedAccount).where(DeletedAccount.id == account_id)).fetchone()
    if not row:
        raise LookupError("That account isn't deleted.")
    conn.execute(delete(DeletedAccount).where(DeletedAccount.id == account_id))
    pid = row["plaid_account_id"]
    if pid and conn.execute(select(PlaidAccount.plaid_account_id).where(PlaidAccount.plaid_account_id == pid)).fetchone():
        conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == pid).values(ignored=0))
        if account_id == "pl:" + pid:
            plaidbank.match(conn, pid, "new", today)   # its own account again; the next sync reads its history again
    if not account_id.startswith("pl:"):
        # SimpleFIN: read its whole history (BACKFILL_DAYS) again on the next sync, not just the last couple of weeks.
        st = json.loads(db.get_setting(conn, sk.SIMPLEFIN_BACKFILL) or "{}")
        if account_id in (st.get("done") or []):
            st["done"] = [x for x in st["done"] if x != account_id]
            db.set_setting(conn, sk.SIMPLEFIN_BACKFILL, json.dumps(st))
    return {"ok": True, "name": row["name"]}


def ids(conn) -> set[str]:
    """The accounts you deleted (accounts.id), which SimpleFIN's sync leaves out."""
    return set(conn.execute(select(DeletedAccount.id)).scalars())


def plaid_ids(conn) -> set[str]:
    """Plaid bank and card accounts that belonged to an account you deleted."""
    return set(conn.execute(select(DeletedAccount.plaid_account_id).where(DeletedAccount.plaid_account_id.is_not(None))).scalars())


def inv_ids(conn) -> set[str]:
    """Investment accounts (inv_accounts.id) that belonged to an account you deleted, which Plaid's sync leaves out."""
    out: set[str] = set()
    for v in conn.execute(select(DeletedAccount.inv_ids).where(DeletedAccount.inv_ids.is_not(None))).scalars():
        out.update(json.loads(v))
    return out
