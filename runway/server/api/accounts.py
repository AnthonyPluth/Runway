"""Accounts: the list, and the changes you make to one (its name, type, owner, provider)."""
from __future__ import annotations

from ... import db, plaidbank
from ... import settings_keys as sk
from ..common import ApiError


ACCOUNT_FIELDS = {
    "display_name": str, "kind": str, "pay_from": str,
    "in_forecast": int, "daily_spend": int, "hidden": int, "owed_positive": int, "owner": str,
}
KINDS = {"checking", "savings", "credit", "loan", "investment"}


def api_accounts(conn, _q, _b):
    accts = db.rows(conn.execute("SELECT * FROM accounts ORDER BY hidden, kind, COALESCE(display_name, name)"))
    items = {r["plaid_account_id"]: r for r in db.rows(conn.execute(
        "SELECT p.plaid_account_id, p.mask, p.item_id, i.products, i.institution_name, s.last_statement_date, s.next_due_date "
        "FROM plaid_accounts p JOIN plaid_items i ON i.item_id=p.item_id "
        "LEFT JOIN card_statements s ON s.plaid_account_id=p.plaid_account_id"))}
    for a in accts:   # which providers this account can use, and (cards) its latest statement dates
        it = items.get(a.get("plaid_account_id") or "")
        a["plaid_link"] = ({"institution": it["institution_name"], "mask": it["mask"],
                            "transactions": "transactions" in (it["products"] or ""),
                            "closed": it["last_statement_date"], "due": it["next_due_date"],
                            "statement_note": db.get_setting(conn, sk.plaid_stmt_note(it['item_id']))} if it else None)
    return accts


def api_account_update(conn, _q, body, acct_id):
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct_id,)).fetchone():
        raise ApiError("Account not found", 404)
    sets, vals = [], []
    for k, v in body.items():
        if k not in ACCOUNT_FIELDS:
            continue
        if v in ("", None):
            v = None
        elif ACCOUNT_FIELDS[k] is int:
            v = int(v)
        else:
            v = str(v).strip()
        if k == "kind" and v not in KINDS:
            raise ApiError("Unknown account type")
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", (*vals, acct_id))
    if body.get("provider"):
        try:
            plaidbank.set_provider(conn, acct_id, body["provider"])
        except ValueError as e:
            raise ApiError(str(e)) from e
    return {"ok": True}
