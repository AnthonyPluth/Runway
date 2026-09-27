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
from datetime import date, datetime, timedelta, timezone

from . import db, splits
from .categorize import clean_payee
from .plaid import PlaidError, call

HISTORY_DAYS = 730     # transaction history to ask for when linking (Plaid's maximum)
OVERLAP_DAYS = 3       # the same transaction can post a few days apart at two providers
KINDS = {"checking": "checking", "savings": "savings", "money market": "savings", "cd": "savings",
         "credit card": "credit", "paypal": "checking", "cash management": "checking"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def _compatible(kind: str, runway: str) -> bool:
    return kind == runway or (kind in ("checking", "savings") and runway in ("checking", "savings"))


# ------------------------------------------------------------------------------------------------ matching

GENERIC = {"card", "credit", "visa", "signature", "mastercard", "world", "elite", "infinite", "account", "checking", "savings",
           "the", "bank", "of", "and", "my", "plus", "rewards", "cash", "back"}


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " "))


def _score(pa: dict, acct: dict, institution: str | None) -> int:
    """How sure we are that Plaid account `pa` is Runway account `acct` (0 = no reason to think so)."""
    from . import brands
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
    institution and a similar balance all count. Anything unclear waits for you in Settings → Connections."""
    item = conn.execute("SELECT institution_name FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    institution = item["institution_name"] if item else None
    theirs = [p for p in db.rows(conn.execute(
        "SELECT * FROM plaid_accounts WHERE item_id=? AND ignored=0 AND plaid_account_id NOT IN "
        "(SELECT plaid_account_id FROM accounts WHERE plaid_account_id IS NOT NULL)", (item_id,))) if runway_kind(p)]
    free = db.rows(conn.execute("SELECT * FROM accounts WHERE plaid_account_id IS NULL AND id NOT LIKE 'pl:%'"))
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
    for _score_, pid, aid in sorted(pairs, reverse=True):   # strongest first; each account matched once
        if pid in used_p or aid in used_a:
            continue
        used_p.add(pid); used_a.add(aid)
        conn.execute("UPDATE accounts SET plaid_account_id=? WHERE id=?", (pid, aid))
        acct = next(a for a in free if a["id"] == aid)
        matched.append(acct["display_name"] or acct["name"])
    return matched


def match(conn, plaid_account_id: str, target: str, today: date | None = None) -> dict:
    """Your choice for a Plaid account: a Runway account id (it's the same account), "new" (add it as its own
    account, with Plaid as its provider) or "ignore"."""
    pa = conn.execute("SELECT * FROM plaid_accounts WHERE plaid_account_id=?", (plaid_account_id,)).fetchone()
    if not pa:
        raise ValueError("That Plaid account isn't here any more.")
    conn.execute("UPDATE accounts SET plaid_account_id=NULL, provider='simplefin' WHERE plaid_account_id=? AND id NOT LIKE 'pl:%'",
                 (plaid_account_id,))
    conn.execute("UPDATE plaid_accounts SET ignored=? WHERE plaid_account_id=?", (1 if target == "ignore" else 0, plaid_account_id))
    if target in ("ignore", ""):
        return {"ok": True}
    if target == "new":
        kind = runway_kind(pa) or "checking"
        aid = "pl:" + plaid_account_id
        item = conn.execute("SELECT institution_name FROM plaid_items WHERE item_id=?", (pa["item_id"],)).fetchone()
        name = pa["name"] or pa["official_name"] or "Account"
        if pa["mask"]:
            name += f" ••{pa['mask']}"
        conn.execute("INSERT INTO accounts(id, name, org, kind, owed_positive, provider, plaid_account_id, provider_since) "
                     "VALUES (?,?,?,?,?, 'plaid', ?, ?) ON CONFLICT(id) DO UPDATE SET plaid_account_id=excluded.plaid_account_id, hidden=0",
                     (aid, name, item["institution_name"] if item else None, kind, 1 if kind in ("credit", "loan") else 0,
                      plaid_account_id, (today or date.today()).isoformat()))
        _set_balance(conn, aid, pa)
        _reread(conn, pa["item_id"])
        return {"ok": True, "account_id": aid}
    acct = conn.execute("SELECT * FROM accounts WHERE id=?", (target,)).fetchone()
    if not acct:
        raise ValueError("Pick one of your accounts.")
    conn.execute("UPDATE accounts SET plaid_account_id=? WHERE id=?", (plaid_account_id, target))
    return {"ok": True, "account_id": target}


def set_provider(conn, account_id: str, provider: str, today: date | None = None) -> None:
    acct = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not acct:
        raise ValueError("Account not found")
    if provider not in ("simplefin", "plaid"):
        raise ValueError("Choose SimpleFIN or Plaid")
    if provider == acct["provider"]:
        return
    if provider == "plaid":
        item = _item_for(conn, acct["plaid_account_id"])
        if not item:
            raise ValueError("Match this account to a Plaid account first (Settings → Connections).")
        if "transactions" not in products(item):
            raise ValueError("That Plaid connection only has card statements, not transactions.")
    elif account_id.startswith("pl:"):
        raise ValueError("This account only comes from Plaid.")
    conn.execute("UPDATE accounts SET provider=?, provider_since=? WHERE id=?", (provider, (today or date.today()).isoformat(), account_id))
    if provider == "plaid":
        _reread(conn, _item_for(conn, acct["plaid_account_id"])["item_id"])
        pa = conn.execute("SELECT * FROM plaid_accounts WHERE plaid_account_id=?", (acct["plaid_account_id"],)).fetchone()
        if pa:
            _set_balance(conn, account_id, pa)


def _item_for(conn, plaid_account_id: str | None):
    if not plaid_account_id:
        return None
    return conn.execute("SELECT i.* FROM plaid_items i JOIN plaid_accounts p ON p.item_id=i.item_id WHERE p.plaid_account_id=?",
                        (plaid_account_id,)).fetchone()


def _reread(conn, item_id: str) -> None:
    """Read the connection's whole transaction history again on the next sync (an account newly uses Plaid)."""
    conn.execute("UPDATE plaid_items SET cursor=NULL WHERE item_id=?", (item_id,))


# ------------------------------------------------------------------------------------------------ syncing

def _set_balance(conn, account_id: str, pa) -> None:
    acct = conn.execute("SELECT kind, owed_positive FROM accounts WHERE id=?", (account_id,)).fetchone()
    if pa["current"] is None or not acct:
        return
    owes = acct["kind"] in ("credit", "loan")
    balance = pa["current"] if (not owes or acct["owed_positive"]) else -pa["current"]
    conn.execute("UPDATE accounts SET balance=?, available=?, balance_date=? WHERE id=?",
                 (balance, pa["available"], date.today().isoformat(), account_id))


def sync_item(conn, item_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    item = conn.execute("SELECT * FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    if not item:
        raise PlaidError("Connection not found")
    try:
        res = call(conn, "/accounts/get", {"access_token": item["access_token"]})
    except PlaidError as e:
        conn.execute("UPDATE plaid_items SET error=? WHERE item_id=?", (e.code or str(e), item_id))
        conn.commit()
        raise
    if not item["institution_name"] and (res.get("item") or {}).get("institution_name"):
        conn.execute("UPDATE plaid_items SET institution_name=? WHERE item_id=?", (res["item"]["institution_name"], item_id))
    for a in res.get("accounts", []):
        bal = a.get("balances") or {}
        conn.execute(
            "INSERT INTO plaid_accounts(plaid_account_id, item_id, name, official_name, mask, type, subtype, current, available) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(plaid_account_id) DO UPDATE SET name=excluded.name, "
            "official_name=excluded.official_name, mask=excluded.mask, type=excluded.type, subtype=excluded.subtype, "
            "current=excluded.current, available=excluded.available",
            (a["account_id"], item_id, a.get("name"), a.get("official_name"), a.get("mask"), a.get("type"), a.get("subtype"),
             bal.get("current"), bal.get("available")))
    matched = auto_match(conn, item_id)
    for acct in db.rows(conn.execute(
            "SELECT a.id, p.* FROM accounts a JOIN plaid_accounts p ON p.plaid_account_id=a.plaid_account_id "
            "WHERE a.provider='plaid' AND p.item_id=?", (item_id,))):
        _set_balance(conn, acct["id"], acct)
    out = {"accounts": len(res.get("accounts", [])), "matched": matched, "new": [], "statements": 0}
    prods = products(item)
    if "transactions" in prods:
        try:
            out["new"] = sync_transactions(conn, item, today)
        except PlaidError as e:
            if e.code != "PRODUCT_NOT_READY":   # the first pull takes Plaid a little while; the next sync gets it
                raise
    # Card statements: ask whenever the connection has a card, even if Liabilities wasn't listed when it was linked
    # (optional products don't always show up there). What happened is kept for Settings to explain.
    has_card = conn.execute("SELECT 1 FROM plaid_accounts WHERE item_id=? AND type='credit'", (item_id,)).fetchone()
    if "liabilities" in prods or has_card:
        try:
            out["statements"] = sync_statements(conn, item, today)
            db.set_setting(conn, f"plaid_stmt_note:{item_id}", None)
            if "liabilities" not in prods:
                conn.execute("UPDATE plaid_items SET products=? WHERE item_id=?", (",".join(sorted(prods | {"liabilities"})), item_id))
        except PlaidError as e:
            if e.code in ("PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_READY", "NO_LIABILITY_ACCOUNTS", "ADDITIONAL_CONSENT_REQUIRED",
                          "INVALID_PRODUCT", "PRODUCTS_NOT_ENABLED", "INSTITUTION_NOT_SUPPORTED"):
                db.set_setting(conn, f"plaid_stmt_note:{item_id}", e.code)
            else:
                raise
    conn.execute("UPDATE plaid_items SET last_sync=?, error=NULL WHERE item_id=?", (_now(), item_id))
    return out


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


def duplicate(conn, account_id: str, posted: str, amount: float, from_plaid: bool, claimed: set) -> bool:
    """Whether the other provider already brought this transaction in (same account and amount, within a few
    days). Each earlier transaction stands in for one new one only (claimed)."""
    d = date.fromisoformat(posted)
    lo, hi = (d - timedelta(days=OVERLAP_DAYS)).isoformat(), (d + timedelta(days=OVERLAP_DAYS)).isoformat()
    other = "id NOT LIKE ?" if from_plaid else "id LIKE ?"
    for (tid,) in conn.execute(
            f"SELECT id FROM transactions WHERE account_id=? AND posted>=? AND posted<=? AND amount>? AND amount<? AND {other} "
            "ORDER BY posted", (account_id, lo, hi, amount - 0.005, amount + 0.005, "%|pl:%")).fetchall():
        if tid not in claimed:
            claimed.add(tid)
            return True
    return False


def sync_transactions(conn, item, today: date) -> list[str]:
    added, modified, removed, cursor = _fetch_changes(conn, item)
    accts = {r["plaid_account_id"]: dict(r) for r in conn.execute(
        "SELECT id, plaid_account_id, provider_since FROM accounts WHERE provider='plaid' AND plaid_account_id IS NOT NULL")}
    # Accounts that have history from SimpleFIN: take Plaid's only from when the account switched (a little before,
    # matched up), so that history isn't repeated.
    earlier = {aid for (aid,) in conn.execute(
        "SELECT DISTINCT account_id FROM transactions WHERE id NOT LIKE ? AND account_id IN (SELECT id FROM accounts WHERE provider='plaid')",
        ("%|pl:%",)).fetchall()}
    new_ids, claimed = [], set()
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
        if conn.execute("SELECT 1 FROM transactions WHERE id=?", (key,)).fetchone():
            conn.execute("UPDATE transactions SET posted=?, amount=?, description=?, pending=? WHERE id=?",
                         (posted, amount, desc, pending, key))
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
            prior = conn.execute("SELECT payee, category, category_source, confidence, needs_review, recurring_id FROM transactions WHERE id=?",
                                 (old,)).fetchone()
            conn.execute("DELETE FROM transactions WHERE id=?", (old,))
        if prior and prior["category"]:
            conn.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, pending, category, "
                         "category_source, confidence, needs_review, recurring_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (key, aid, posted, amount, desc, prior["payee"] or payee, pending, prior["category"], prior["category_source"],
                          prior["confidence"], prior["needs_review"], prior["recurring_id"]))
        else:
            conn.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, pending) VALUES (?,?,?,?,?,?,?)",
                         (key, aid, posted, amount, desc, payee, pending))
            new_ids.append(key)
        if old:
            splits.carry_over(conn, old, key, amount)
    for r in removed:
        conn.execute("DELETE FROM transactions WHERE id LIKE ?", (f"%|pl:{r['transaction_id']}",))
    splits.prune(conn)
    conn.execute("UPDATE plaid_items SET cursor=? WHERE item_id=?", (cursor, item["item_id"]))
    return new_ids


def sync_statements(conn, item, today: date) -> int:
    res = call(conn, "/liabilities/get", {"access_token": item["access_token"]})
    n = 0
    for c in ((res.get("liabilities") or {}).get("credit") or []):
        conn.execute(
            "INSERT INTO card_statements(plaid_account_id, item_id, last_statement_balance, last_statement_date, next_due_date, "
            "minimum_payment, last_payment_amount, last_payment_date, is_overdue, updated) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(plaid_account_id) DO UPDATE SET last_statement_balance=excluded.last_statement_balance, "
            "last_statement_date=excluded.last_statement_date, next_due_date=excluded.next_due_date, "
            "minimum_payment=excluded.minimum_payment, last_payment_amount=excluded.last_payment_amount, "
            "last_payment_date=excluded.last_payment_date, is_overdue=excluded.is_overdue, updated=excluded.updated",
            (c["account_id"], item["item_id"], c.get("last_statement_balance"), c.get("last_statement_issue_date"),
             c.get("next_payment_due_date"), c.get("minimum_payment_amount"), c.get("last_payment_amount"),
             c.get("last_payment_date"), 1 if c.get("is_overdue") else 0, _now()))
        n += 1
    return n


def statement(conn, card_id: str, today: date):
    """The issuer's latest statement for a card, through Plaid."""
    r = conn.execute("SELECT s.* FROM card_statements s JOIN accounts a ON a.plaid_account_id=s.plaid_account_id WHERE a.id=?",
                     (card_id,)).fetchone()
    if not r or not r["last_statement_date"] or date.fromisoformat(r["last_statement_date"]) > today:
        return None
    return r


def sync_all(conn, today: date | None = None) -> dict:
    out = {"items": 0, "new": [], "errors": []}
    for item in conn.execute("SELECT * FROM plaid_items").fetchall():
        if not is_bank_item(item):
            continue
        try:
            r = sync_item(conn, item["item_id"], today)
            out["items"] += 1
            out["new"] += r["new"]
            conn.commit()
        except PlaidError as e:
            out["errors"].append(f"{item['institution_name'] or 'Plaid'}: {e}")
    return out


def forget_item(conn, item_id: str) -> None:
    """A removed connection: its accounts go back to SimpleFIN (the ones only Plaid had keep their history)."""
    ids = [r[0] for r in conn.execute("SELECT plaid_account_id FROM plaid_accounts WHERE item_id=?", (item_id,)).fetchall()]
    for pid in ids:
        conn.execute("UPDATE accounts SET plaid_account_id=NULL, provider='simplefin' WHERE plaid_account_id=? AND id NOT LIKE 'pl:%'", (pid,))
        conn.execute("UPDATE accounts SET plaid_account_id=NULL WHERE plaid_account_id=?", (pid,))
    conn.execute("DELETE FROM card_statements WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM plaid_accounts WHERE item_id=?", (item_id,))
