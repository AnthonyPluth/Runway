"""Accounts: the list, and the changes you make to one (its name, type, owner, provider, logo, and how a card is paid)."""
from __future__ import annotations

from sqlalchemy import func, select, update

from ... import brands, db, forecast, merchants, plaidbank
from ... import settings_keys as sk
from ...models import Account, CardStatement, PlaidAccount, PlaidItem
from ..common import ApiError


ACCOUNT_FIELDS = {
    "display_name": str, "kind": str, "pay_from": str,
    "in_forecast": int, "daily_spend": int, "hidden": int, "networth_hidden": int, "owed_positive": int, "owner": str,
}
KINDS = {"checking", "savings", "credit", "loan", "investment"}
# How a card's statements are paid, for the forecast (forecast.payment_plan): kept in settings, not on the account.
PAY_FIELDS = {"pay_mode": sk.card_pay_mode, "pay_amount": sk.card_pay_amount, "apr": sk.card_apr}


def api_accounts(conn, _q, _b):
    accts = db.rows(conn.execute(
        select(Account).order_by(Account.hidden, Account.kind, func.coalesce(Account.display_name, Account.name))))
    p, s = PlaidAccount, CardStatement
    items = {r["plaid_account_id"]: r for r in db.rows(conn.execute(
        select(p.plaid_account_id, p.mask, p.item_id, PlaidItem.products, PlaidItem.institution_name, s.last_statement_date,
               s.next_due_date)
        .join(PlaidItem, PlaidItem.item_id == p.item_id).outerjoin(s, s.plaid_account_id == p.plaid_account_id)))}
    for a in accts:   # which providers this account can use, and (cards) its latest statement dates
        it = items.get(a.get("plaid_account_id") or "")
        a["plaid_link"] = ({"institution": it["institution_name"], "mask": it["mask"],
                            "transactions": "transactions" in (it["products"] or ""),
                            "closed": it["last_statement_date"], "due": it["next_due_date"],
                            "statement_note": db.get_setting(conn, sk.plaid_stmt_note(it['item_id']))} if it else None)
        if a["kind"] == "credit":
            a.update(forecast.payment_plan(conn, a["id"]))
    return accts


def _pay_settings(body: dict) -> dict[str, str | None]:
    """The card payment fields in an update, checked, as the settings values to save (None: back to the default)."""
    out: dict[str, str | None] = {}
    for k in PAY_FIELDS.keys() & body.keys():
        v = body[k]
        if v in ("", None):
            out[k] = None
        elif k == "pay_mode":
            if v not in forecast.PAY_MODES:
                raise ApiError("Unknown payment mode")
            out[k] = None if v == "full" else v
        else:
            try:
                n = db.number(v)
            except (TypeError, ValueError):
                raise ApiError("Enter an amount" if k == "pay_amount" else "Enter the APR as a percentage") from None
            if n < 0 or (k == "apr" and n > 100):
                raise ApiError("Enter an amount of zero or more" if k == "pay_amount" else "Enter an APR between 0 and 100")
            out[k] = str(n)
    return out


def api_account_update(conn, _q, body, acct_id):
    if not conn.execute(select(Account.id).where(Account.id == acct_id)).fetchone():
        raise ApiError("Account not found", 404)
    pay = _pay_settings(body)   # checked before anything is saved
    sets = {}
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
        sets[k] = v   # only ACCOUNT_FIELDS' columns
    if sets:
        conn.execute(update(Account).where(Account.id == acct_id).values(**sets))
    for k, v in pay.items():
        db.set_setting(conn, PAY_FIELDS[k](acct_id), v)
    if body.get("provider"):
        try:
            plaidbank.set_provider(conn, acct_id, body["provider"])
        except ValueError as e:
            raise ApiError(str(e)) from e
    return {"ok": True}


def api_account_logo_options(conn, _q, _b, acct_id):
    """For choosing an account's logo: what you chose, and the brands Logo.dev's Brand Search finds for its institution."""
    row = conn.execute(select(Account.logo).where(Account.id == acct_id)).fetchone()
    if not row:
        raise ApiError("Account not found", 404)
    name = brands.account_brands(conn).get(acct_id, {}).get("institution") or ""
    choice = None if not row["logo"] else {"website": None, "hidden": True} if row["logo"] == brands.NO_LOGO else {
        "website": row["logo"], "hidden": False}
    out: dict = {"choice": choice, "searchable": merchants.searchable(conn), "configured": merchants.configured(conn),
                 "candidates": [], "error": None}
    if name and out["searchable"]:
        found = merchants.search(conn, name)
        if found is None:
            out["error"] = merchants._why
        else:
            out["candidates"] = found[:6]
    return out


def api_account_logo(conn, _q, body, acct_id):
    """Choose an account's logo: a website's (fetched from Logo.dev now), none (its letter), or (neither) its institution's."""
    if not conn.execute(select(Account.id).where(Account.id == acct_id)).fetchone():
        raise ApiError("Account not found", 404)
    website = (body.get("website") or "").strip()
    try:
        logo = merchants.fetch_site(conn, website) if website else brands.NO_LOGO if body.get("hidden") else None
    except ValueError as e:
        raise ApiError(str(e)) from e
    conn.execute(update(Account).where(Account.id == acct_id).values(logo=logo))
    return {"ok": True}
