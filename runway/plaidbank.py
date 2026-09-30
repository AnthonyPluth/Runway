"""Banks and credit cards through Plaid, account by account alongside SimpleFIN.

Each Runway account has a provider (accounts.provider): "simplefin" (the default) or "plaid". A Plaid connection's
accounts (plaid_accounts) are matched to Runway accounts (accounts.plaid_account_id), automatically when it's clear
(the last 4 digits appear in the name, or exactly one balance agrees) and otherwise by you in Settings. Then:

  - Balances and transactions come from whichever provider the account is set to. When you switch, the history
    both providers have is matched up (same amount within 3 days) so nothing is counted twice, and your categories
    stay on the transactions you already have.
  - Card statements (balance, closing date, due date, minimum payment) come from Plaid Liabilities for every matched
    card, whatever its provider; they're how the forecast knows what each card owes and when.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, UTC
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from . import brands, db, merchants, splits
from . import settings_keys as sk
from .categorize import clean_payee
from .models import Account, CardStatement, PlaidAccount, PlaidItem, Transaction
from .plaidapi import PlaidError, call   # not plaid.py, which builds on this module

HISTORY_DAYS = 730     # transaction history to ask for when linking (Plaid's maximum)
OVERLAP_DAYS = 3       # the same transaction can post a few days apart at two providers
KINDS = {"checking": "checking", "savings": "savings", "money market": "savings", "cd": "savings",
         "credit card": "credit", "paypal": "checking", "cash management": "checking"}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def products(item) -> set[str]:
    return {p.strip() for p in (item["products"] or "investments").split(",") if p.strip()}


def is_bank_item(item) -> bool:
    return bool(products(item) & {"transactions", "liabilities"})


def runway_kind(pa) -> str | None:
    t, st = (pa["type"] or "").lower(), (pa["subtype"] or "").lower()
    if t == "credit":
        return "credit"
    if t == "loan":
        return "loan"
    if t == "depository":
        return KINDS.get(st, "checking")
    return None   # investment accounts come through the investments connection


def _compatible(kind: str | None, runway: str) -> bool:
    return kind == runway or (kind in ("checking", "savings") and runway in ("checking", "savings"))


# ------------------------------------------------------------------------------------------------ matching

GENERIC = {"card", "credit", "visa", "signature", "mastercard", "world", "elite", "infinite", "account", "checking", "savings",
           "the", "bank", "of", "and", "my", "plus", "rewards", "cash", "back"}


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " "))


def _score(pa: dict, acct: dict, institution: str | None) -> int:
    """How sure we are that Plaid account `pa` is Runway account `acct` (0 = no reason to think so)."""
    runway_text = f"{acct['name']} {acct.get('display_name') or ''}"
    plaid_names = [pa.get("official_name"), pa.get("name")]
    score = 0
    if pa.get("mask") and re.search(r"(?<!\d)" + re.escape(pa["mask"]) + r"(?!\d)", runway_text):
        score += 100                                         # "…1234" in the name
    theirs = set(_words(runway_text))
    for n in plaid_names:
        w = [x for x in _words(n) if x not in {"visa", "signature", "card", "credit", "mastercard", "infinite", "world", "elite"}]
        for skip in (0, 1):                                  # "Chase Sapphire Reserve" -> CSR; "Sapphire Reserve" -> SR
            if len(w) - skip >= 2 and "".join(x[0] for x in w[skip:]) in theirs:
                score += 60
                break
    mine = {x for n in plaid_names for x in _words(n) if x not in GENERIC and len(x) > 2}
    score += 25 * len(mine & (theirs - GENERIC))             # "Double Cash", "Premium Rewards", "Venture"
    if pa.get("current") is not None:
        diff = abs(abs(acct["balance"] or 0) - abs(pa["current"]))
        score += 40 if diff < 0.01 else 15 if diff <= max(25.0, 0.05 * abs(pa["current"])) else 0
    theirs_brand = brands.brand(acct.get("org"), acct.get("display_name"), acct["name"])
    ours_brand = brands.brand(institution, pa.get("official_name"), pa.get("name"))
    if theirs_brand and ours_brand:
        score = score + 20 if theirs_brand == ours_brand else -1000   # never a Chase card for a Citi one
    return score


def auto_match(conn, item_id: str) -> list[str]:
    """Match this connection's unmatched accounts to your existing ones where one candidate clearly fits best: the last 4
    digits in its name, its initials ("CSR" for Chase Sapphire Reserve), shared words ("Double Cash"), the same
    institution and a similar balance all count. Anything unclear waits for you in Settings → Bank connections."""
    institution = _institution(conn, item_id)
    theirs = [p for p in db.rows(conn.execute(select(PlaidAccount).where(
        PlaidAccount.item_id == item_id, PlaidAccount.ignored == 0, PlaidAccount.plaid_account_id.not_in(_matched())))) if runway_kind(p)]
    free = db.rows(conn.execute(select(Account).where(Account.plaid_account_id.is_(None), Account.id.not_like("pl:%"))))
    pairs = []
    for pa in theirs:
        kind = runway_kind(pa)
        scored = sorted(((_score(pa, a, institution), a["id"]) for a in free if _compatible(kind, a["kind"])), reverse=True)
        scored = [x for x in scored if x[0] > 0]
        if not scored:
            continue
        # A clear winner: enough evidence, and well ahead of the next candidate.
        if scored[0][0] >= 35 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 20):
            pairs.append((scored[0][0], pa["plaid_account_id"], scored[0][1]))
    matched, used_p, used_a = [], set(), set()
    for _, pid, aid in sorted(pairs, reverse=True):   # strongest first; each account matched once
        if pid in used_p or aid in used_a:
            continue
        used_p.add(pid); used_a.add(aid)
        conn.execute(update(Account).where(Account.id == aid).values(plaid_account_id=pid))
        acct = next(a for a in free if a["id"] == aid)
        matched.append(acct["display_name"] or acct["name"])
    return matched


def _institution(conn, item_id: str) -> str | None:
    """A connection's institution name (None if there's no such connection)."""
    return conn.execute(select(PlaidItem.institution_name).where(PlaidItem.item_id == item_id)).scalar()


def _matched():
    """The Plaid accounts matched to one of your accounts, for `.not_in()`."""
    return select(Account.plaid_account_id).where(Account.plaid_account_id.is_not(None))


def _plaid_account(conn, plaid_account_id: str | None):
    return conn.execute(select(PlaidAccount).where(PlaidAccount.plaid_account_id == plaid_account_id)).fetchone()


def match(conn, plaid_account_id: str, target: str, today: date | None = None) -> dict:
    """Your choice for a Plaid account: a Runway account id (it's the same account), "new" (add it as its own
    account, with Plaid as its provider) or "ignore"."""
    pa = _plaid_account(conn, plaid_account_id)
    if not pa:
        raise ValueError("That Plaid account isn't here any more.")
    if target == "pl:" + plaid_account_id:   # its own account, picked by name
        target = "new"
    if target not in ("new", "ignore", ""):
        taken = conn.execute(select(Account.display_name, Account.name, Account.plaid_account_id).where(Account.id == target)).fetchone()
        if taken and taken["plaid_account_id"] and taken["plaid_account_id"] != plaid_account_id:
            raise ValueError(f"{taken['display_name'] or taken['name']} is already linked to another Plaid account. "
                             "Unlink it there first.")
    _back_to_simplefin(conn, plaid_account_id)
    conn.execute(update(PlaidAccount).where(PlaidAccount.plaid_account_id == plaid_account_id)
                 .values(ignored=1 if target == "ignore" else 0))
    if target != "new":
        _retire_own_account(conn, plaid_account_id, target if target not in ("ignore", "") else None)
    if target in ("ignore", ""):
        return {"ok": True}
    if target == "new":
        kind = runway_kind(pa) or "checking"
        aid = "pl:" + plaid_account_id
        name = pa["name"] or pa["official_name"] or "Account"
        if pa["mask"]:
            name += f" ••{pa['mask']}"
        db.upsert(conn, Account, {"id": aid, "name": name, "org": _institution(conn, pa["item_id"]), "kind": kind,
                                  "owed_positive": 1 if kind in ("credit", "loan") else 0, "provider": "plaid",
                                  "plaid_account_id": plaid_account_id, "provider_since": (today or date.today()).isoformat()},
                  key=["id"], update=lambda ex: {"plaid_account_id": ex.plaid_account_id, "hidden": 0})
        _set_balance(conn, aid, pa)
        _reread(conn, pa["item_id"])
        return {"ok": True, "account_id": aid}
    if not conn.execute(select(Account.id).where(Account.id == target)).fetchone():
        raise ValueError("Pick one of your accounts.")
    conn.execute(update(Account).where(Account.id == target).values(plaid_account_id=plaid_account_id))
    return {"ok": True, "account_id": target}


def _retire_own_account(conn, plaid_account_id: str, into: str | None) -> None:
    """The Plaid account had been added as an account of its own ("pl:…") and is now matched to another of your
    accounts, or not used: stop syncing it and hide it, so it isn't counted next to the account it really is. Its
    history stays (hidden), and the categories you gave it go to the same transactions in `into` that have none."""
    own = "pl:" + plaid_account_id
    if not conn.execute(select(Account.id).where(Account.id == own, Account.plaid_account_id == plaid_account_id)).fetchone():
        return
    conn.execute(update(Account).where(Account.id == own).values(plaid_account_id=None, hidden=1))
    if not into:
        return
    claimed: set = set()
    for t in conn.execute(select(Transaction.posted, Transaction.amount, Transaction.category, Transaction.category_source)
                          .where(Transaction.account_id == own, Transaction.category.is_not(None))
                          .order_by(Transaction.posted)).fetchall():
        for tid in conn.execute(select(Transaction.id).where(Transaction.account_id == into, Transaction.category.is_(None),
                                                             *_near(t["posted"], t["amount"]))
                                .order_by(Transaction.posted)).scalars():
            if tid not in claimed:
                claimed.add(tid)
                conn.execute(update(Transaction).where(Transaction.id == tid)
                             .values(category=t["category"], category_source=t["category_source"], needs_review=0))
                break


def _near(posted: str, amount: float) -> tuple:
    """Conditions for the same transaction at the other provider: the same amount, posted within OVERLAP_DAYS."""
    d = date.fromisoformat(posted)
    return (Transaction.posted >= (d - timedelta(days=OVERLAP_DAYS)).isoformat(),
            Transaction.posted <= (d + timedelta(days=OVERLAP_DAYS)).isoformat(),
            Transaction.amount > amount - 0.005, Transaction.amount < amount + 0.005)


def set_provider(conn, account_id: str, provider: str, today: date | None = None) -> None:
    acct = conn.execute(select(Account).where(Account.id == account_id)).fetchone()
    if not acct:
        raise ValueError("Account not found")
    if provider not in ("simplefin", "plaid"):
        raise ValueError("Choose SimpleFIN or Plaid")
    if provider == acct["provider"]:
        return
    if provider == "plaid":
        item = _item_for(conn, acct["plaid_account_id"])
        if not item:
            raise ValueError("Match this account to a Plaid account first (Settings → Accounts).")
        if "transactions" not in products(item):
            raise ValueError("That Plaid connection only has card statements, not transactions.")
    elif account_id.startswith("pl:"):
        raise ValueError("This account only comes from Plaid.")
    conn.execute(update(Account).where(Account.id == account_id)
                 .values(provider=provider, provider_since=(today or date.today()).isoformat()))
    if provider == "plaid":
        _reread(conn, _item_for(conn, acct["plaid_account_id"])["item_id"])
        pa = _plaid_account(conn, acct["plaid_account_id"])
        if pa:
            _set_balance(conn, account_id, pa)


def _item_for(conn, plaid_account_id: str | None):
    if not plaid_account_id:
        return None
    return conn.execute(select(PlaidItem).join(PlaidAccount, PlaidAccount.item_id == PlaidItem.item_id)
                        .where(PlaidAccount.plaid_account_id == plaid_account_id)).fetchone()


def _reread(conn, item_id: str) -> None:
    """Read the connection's whole transaction history again on the next sync (an account newly uses Plaid)."""
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(cursor=None))


# ------------------------------------------------------------------------------------------------ syncing

def _set_balance(conn, account_id: str, pa) -> None:
    acct = conn.execute(select(Account.kind, Account.owed_positive).where(Account.id == account_id)).fetchone()
    if pa["current"] is None or not acct:
        return
    owes = acct["kind"] in ("credit", "loan")
    balance = pa["current"] if (not owes or acct["owed_positive"]) else -pa["current"]
    conn.execute(update(Account).where(Account.id == account_id)
                 .values(balance=balance, available=pa["available"], balance_date=date.today().isoformat()))


STATEMENT_NOTES = ("PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_READY", "NO_LIABILITY_ACCOUNTS", "ADDITIONAL_CONSENT_REQUIRED",
                   "INVALID_PRODUCT", "PRODUCTS_NOT_ENABLED", "INSTITUTION_NOT_SUPPORTED")


def sync_item(conn, item_id: str, today: date | None = None) -> dict:
    """Read the connection from Plaid first, then write it all in one short transaction: holding the database's write
    lock across Plaid's replies would make everything else that writes wait (and fail with "Runway is busy").
    A statement problem doesn't lose the transactions: they're saved, and the problem shows on the connection."""
    today = today or date.today()
    item = conn.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).fetchone()
    if not item:
        raise PlaidError("Connection not found")
    conn.commit()   # nothing of ours open across the network calls
    try:
        res = call(conn, "/accounts/get", {"access_token": item["access_token"]})
    except PlaidError as e:
        _set_error(conn, item_id, e)
        conn.commit()
        raise
    prods = products(item)
    changes = None
    if "transactions" in prods:
        try:
            changes = _fetch_changes(conn, item)
        except PlaidError as e:
            if e.code != "PRODUCT_NOT_READY":   # the first pull takes Plaid a little while; the next sync gets it
                _set_error(conn, item_id, e)
                conn.commit()
                raise
    # Card statements: ask whenever the connection has a card, even if Liabilities wasn't listed when it was linked
    # (optional products don't always show up there). What happened is kept for Settings to explain.
    has_card = any(a.get("type") == "credit" for a in res.get("accounts", []))
    stmts, stmt_error = None, None
    if "liabilities" in prods or has_card:
        try:
            stmts = call(conn, "/liabilities/get", {"access_token": item["access_token"]})
        except PlaidError as e:
            stmt_error = e

    if not item["institution_name"] and (res.get("item") or {}).get("institution_name"):
        conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(institution_name=res["item"]["institution_name"]))
    for a in res.get("accounts", []):
        bal = a.get("balances") or {}
        db.upsert(conn, PlaidAccount, {"plaid_account_id": a["account_id"], "item_id": item_id, "name": a.get("name"),
                                       "official_name": a.get("official_name"), "mask": a.get("mask"), "type": a.get("type"),
                                       "subtype": a.get("subtype"), "current": bal.get("current"), "available": bal.get("available")},
                  key=["plaid_account_id"], update=["name", "official_name", "mask", "type", "subtype", "current", "available"])
    matched = auto_match(conn, item_id)
    for acct in db.rows(conn.execute(
            select(Account.id, PlaidAccount).select_from(Account)
            .join(PlaidAccount, PlaidAccount.plaid_account_id == Account.plaid_account_id)
            .where(Account.provider == "plaid", PlaidAccount.ignored == 0, PlaidAccount.item_id == item_id))):
        _set_balance(conn, acct["id"], acct)
    out = {"accounts": len(res.get("accounts", [])), "matched": matched, "new": [], "statements": 0}
    if changes is not None:
        out["new"] = sync_transactions(conn, item, today, changes)
    error = None
    if stmts is not None:
        out["statements"] = store_statements(conn, item, stmts)
        db.set_setting(conn, sk.plaid_stmt_note(item_id), None)
        if "liabilities" not in prods:
            conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(products=",".join(sorted(prods | {"liabilities"}))))
    elif stmt_error is not None:
        if stmt_error.code in STATEMENT_NOTES:
            db.set_setting(conn, sk.plaid_stmt_note(item_id), stmt_error.code)
        else:
            error = stmt_error
            out["error"] = f"card statements: {stmt_error}"
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id)
                 .values(last_sync=_now(), error=(error.code or str(error)) if error else None))
    return out


def _set_error(conn, item_id: str, e: PlaidError) -> None:
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(error=e.code or str(e)))


def _fetch_changes(conn, item) -> tuple[list, list, list, str]:
    """Everything since the saved cursor, in one consistent set (restarted if the data changes mid-way)."""
    for _attempt in range(3):
        cursor, added, modified, removed = item["cursor"], [], [], []
        try:
            while True:
                body = {"access_token": item["access_token"], "count": 500, "options": {"days_requested": HISTORY_DAYS}}
                if cursor:
                    body["cursor"] = cursor
                res = call(conn, "/transactions/sync", body)
                added += res.get("added", [])
                modified += res.get("modified", [])
                removed += res.get("removed", [])
                cursor = res.get("next_cursor") or cursor
                if not res.get("has_more"):
                    return added, modified, removed, cursor
        except PlaidError as e:
            if e.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                raise
    raise PlaidError("Plaid kept changing the transactions while Runway read them; the next sync will try again.")


PLAID_IDS = "%|pl:%"   # LIKE pattern for the ids of transactions from Plaid ("<account>|pl:<Plaid's id>")


def duplicate(conn, account_id: str, posted: str, amount: float, from_plaid: bool, claimed: set) -> bool:
    """Whether the other provider already brought this transaction in (same account and amount, within a few
    days). Each earlier transaction stands in for one new one only (claimed)."""
    other = Transaction.id.not_like(PLAID_IDS) if from_plaid else Transaction.id.like(PLAID_IDS)
    for tid in conn.execute(select(Transaction.id).where(Transaction.account_id == account_id, *_near(posted, amount), other)
                            .order_by(Transaction.posted)).scalars():
        if tid not in claimed:
            claimed.add(tid)
            return True
    return False


def sync_transactions(conn, item, today: date, changes: tuple | None = None) -> list[str]:
    added, modified, removed, cursor = changes or _fetch_changes(conn, item)
    accts = {r["plaid_account_id"]: dict(r) for r in conn.execute(
        select(Account.id, Account.plaid_account_id, Account.provider_since)
        .join(PlaidAccount, PlaidAccount.plaid_account_id == Account.plaid_account_id)
        .where(Account.provider == "plaid", PlaidAccount.ignored == 0))}
    # Accounts that have history from SimpleFIN: take Plaid's only from when the account switched (a little before,
    # matched up), so that history isn't repeated.
    earlier = set(conn.execute(
        select(Transaction.account_id).distinct()
        .where(Transaction.id.not_like(PLAID_IDS), Transaction.account_id.in_(select(Account.id).where(Account.provider == "plaid")))
    ).scalars())
    new_ids: list[str] = []
    claimed: set[str] = set()
    for t in added + modified:
        acct = accts.get(t.get("account_id"))
        if not acct:
            continue   # an account that gets its transactions from SimpleFIN
        aid = acct["id"]
        key = f"{aid}|pl:{t['transaction_id']}"
        posted = t.get("date") or t.get("authorized_date") or today.isoformat()
        amount = -float(t.get("amount") or 0)       # Plaid: positive = money out; Runway: positive = money in
        desc = (t.get("original_description") or t.get("name") or "").strip()
        payee = clean_payee(t.get("merchant_name") or t.get("name") or desc)
        pending = 1 if t.get("pending") else 0
        merchant = merchants.note(conn, t)
        known = conn.execute(select(Transaction.is_split).where(Transaction.id == key)).fetchone()
        if known:
            conn.execute(update(Transaction).where(Transaction.id == key).values(
                posted=posted, amount=amount, description=desc, pending=pending,
                merchant_id=func.coalesce(merchant, Transaction.merchant_id)))
            if known["is_split"]:
                splits.follow_amount(conn, key, amount)
            continue
        since = acct["provider_since"]
        if aid in earlier and since:
            if posted < (date.fromisoformat(since) - timedelta(days=7)).isoformat():
                continue   # SimpleFIN has this part of the history
            if duplicate(conn, aid, posted, amount, True, claimed):
                continue
        # A posted transaction replaces its pending version: keep the category you gave it.
        prior, old = None, None
        if t.get("pending_transaction_id"):
            old = f"{aid}|pl:{t['pending_transaction_id']}"
            prior = conn.execute(select(Transaction.payee, Transaction.category, Transaction.category_source, Transaction.confidence,
                                        Transaction.needs_review, Transaction.recurring_id).where(Transaction.id == old)).fetchone()
            conn.execute(delete(Transaction).where(Transaction.id == old))
        row = {"id": key, "account_id": aid, "posted": posted, "amount": amount, "description": desc, "payee": payee, "pending": pending}
        if prior and prior["category"]:
            conn.execute(insert(Transaction).values(
                **row | {"payee": prior["payee"] or payee}, category=prior["category"], category_source=prior["category_source"],
                confidence=prior["confidence"], needs_review=prior["needs_review"], recurring_id=prior["recurring_id"]))
        else:
            conn.execute(insert(Transaction).values(**row))
            new_ids.append(key)
        if merchant:
            conn.execute(update(Transaction).where(Transaction.id == key).values(merchant_id=merchant))
        if old:
            splits.carry_over(conn, old, key, amount)
    for r in removed:
        # By key in each account it could be in (a LIKE on the id would read the whole table for each one).
        conn.execute(delete(Transaction).where(Transaction.id.in_(select(Account.id + ("|pl:" + r["transaction_id"])))))
    if not item["cursor"]:
        # The whole history was read again: a hold the bank dropped in the meantime won't be in `removed`, so any
        # pending row of this connection's that isn't in the reply is gone.
        seen = {t["transaction_id"] for t in added + modified}
        for tid in conn.execute(
                select(Transaction.id).join(Account, Account.id == Transaction.account_id)
                .join(PlaidAccount, PlaidAccount.plaid_account_id == Account.plaid_account_id)
                .where(PlaidAccount.item_id == item["item_id"], Transaction.pending == 1, Transaction.id.like(PLAID_IDS))).scalars():
            if tid.split("|pl:", 1)[1] not in seen:
                conn.execute(delete(Transaction).where(Transaction.id == tid))
    splits.prune(conn)
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item["item_id"]).values(cursor=cursor))
    return new_ids


def store_statements(conn, item, res: dict) -> int:
    n = 0
    for c in ((res.get("liabilities") or {}).get("credit") or []):
        stmt = {"last_statement_balance": c.get("last_statement_balance"), "last_statement_date": c.get("last_statement_issue_date"),
                "next_due_date": c.get("next_payment_due_date"), "minimum_payment": c.get("minimum_payment_amount"),
                "last_payment_amount": c.get("last_payment_amount"), "last_payment_date": c.get("last_payment_date"),
                "is_overdue": 1 if c.get("is_overdue") else 0, "updated": _now()}
        db.upsert(conn, CardStatement, {"plaid_account_id": c["account_id"], "item_id": item["item_id"], **stmt},
                  key=["plaid_account_id"], update=list(stmt))   # a card stays with the connection it was first seen on
        n += 1
    return n


def statement(conn, card_id: str, today: date):
    """The issuer's latest statement for a card, through Plaid."""
    r = conn.execute(select(CardStatement).join(Account, Account.plaid_account_id == CardStatement.plaid_account_id)
                     .where(Account.id == card_id)).fetchone()
    if not r or not r["last_statement_date"] or date.fromisoformat(r["last_statement_date"]) > today:
        return None
    return r


def sync_all(conn, today: date | None = None) -> dict:
    out: dict[str, Any] = {"items": 0, "new": [], "errors": []}
    for item in conn.execute(select(PlaidItem)).fetchall():
        if not is_bank_item(item):
            continue
        try:
            r = sync_item(conn, item["item_id"], today)
            out["items"] += 1
            out["new"] += r["new"]
            conn.commit()
            if r.get("error"):
                out["errors"].append(f"{item['institution_name'] or 'Plaid'}: {r['error']}")
        except PlaidError as e:
            out["errors"].append(f"{item['institution_name'] or 'Plaid'}: {e}")
    return out


def refresh_all(conn) -> list[str]:
    """Ask Plaid to fetch new transactions from each bank now (the Transactions Refresh add-on). Plaid does it in the
    background and returns nothing; the next /transactions/sync picks it up. Returns the problems, one per connection."""
    errors = []
    for item in conn.execute(select(PlaidItem)).fetchall():
        if "transactions" not in products(item):
            continue
        try:
            call(conn, "/transactions/refresh", {"access_token": item["access_token"]})
        except PlaidError as e:
            errors.append(f"{item['institution_name'] or 'Plaid'}: {e}")
    return errors


def forget_item(conn, item_id: str) -> None:
    """A removed connection: its accounts go back to SimpleFIN (the ones only Plaid had keep their history)."""
    ids = conn.execute(select(PlaidAccount.plaid_account_id).where(PlaidAccount.item_id == item_id)).scalars()
    for pid in ids:
        _back_to_simplefin(conn, pid)
        conn.execute(update(Account).where(Account.plaid_account_id == pid).values(plaid_account_id=None))
    conn.execute(delete(CardStatement).where(CardStatement.item_id == item_id))
    conn.execute(delete(PlaidAccount).where(PlaidAccount.item_id == item_id))


def _back_to_simplefin(conn, plaid_account_id: str) -> None:
    """Unmatch a Plaid account from your account that it is (not its own "pl:" one), which goes back to SimpleFIN."""
    conn.execute(update(Account).where(Account.plaid_account_id == plaid_account_id, Account.id.not_like("pl:%"))
                 .values(plaid_account_id=None, provider="simplefin"))
