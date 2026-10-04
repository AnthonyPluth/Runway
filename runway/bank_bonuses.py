"""Bank account bonuses: checking and savings accounts opened for their sign-up bonus, on the Churning page.

Each offer asks for some of: direct deposits (a total, a number of them, or both) by a deadline, debit card
purchases, and a balance kept until a day. For an account linked to a Runway account, progress is followed from its
transactions: money in that's categorized as income (a paycheck, not a refund), or that looks like payroll from its
description, counts as a direct deposit; purchases count as debit transactions; its balance is compared with the
minimum. Otherwise you enter the progress yourself.

Also worked out: when the bonus should post, the first day it's safe to close the account without an early-closing
fee or a clawback (and never before the bonus has posted), a reminder to keep the monthly fee waived, when the
bank's rule lets you earn the bonus again, and the bonus money each person received per year: banks report bonuses
as interest (a 1099-INT), so it's usually taxable income. That's information for your records, not tax advice.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, insert, select

# churning imports this module too; its helpers are read when called, so either can be imported first.
from . import categories, churning, dates, db, validate
from .models import Account, ChurnBankBonus, Transaction
from .money import CENT

TYPES = ("checking", "savings", "business")
STATUSES = ("open", "pending", "received", "closed")
DEADLINE_WARN_DAYS = 14
# Descriptions that look like a paycheck from an employer or a payroll company, for deposits not categorized yet.
PAYROLL = re.compile(r"payroll|direct dep|dir dep|\bdd\b|salary|paycheck|\bppd\b|\badp\b|gusto|paychex|\bpayrl|\bsal\b",
                     re.I)


def deadline(b: dict) -> date:
    if b.get("deadline"):
        return date.fromisoformat(b["deadline"])
    return date.fromisoformat(b["opened_on"]) + timedelta(days=b.get("deadline_days") or 90)


def expected_on(b: dict) -> date:
    """The latest the bonus should post: the deadline plus the days the bank takes."""
    days = b.get("post_days")
    return deadline(b) + timedelta(days=60 if days is None else int(days))


def safe_close_on(b: dict) -> date:
    """The first day it can be closed without an early-closing fee or losing the bonus: after the days the bank
    asks it to stay open, after any balance you have to hold, and not before the bonus has posted."""
    opened = date.fromisoformat(b["opened_on"])
    days = [opened + timedelta(days=b.get("keep_open_days") or 0)]
    if b.get("hold_until"):
        days.append(date.fromisoformat(b["hold_until"]) + timedelta(days=1))
    days.append(date.fromisoformat(b["received_on"]) if b.get("received_on") else expected_on(b))
    return max(days)


def is_direct_deposit(row: dict, cats: dict[str, dict]) -> bool:
    """Money in that counts as a direct deposit: a paycheck or other income (not a refund or a transfer between your
    own accounts), or, if it isn't categorized as either, a description that looks like payroll."""
    if row["amount"] <= 0:
        return False
    c = cats.get(row.get("category") or "")
    if c is not None and c["is_transfer"]:
        return False
    if c is not None and c["is_income"] and c["top"] != "Refunds":
        return True
    return bool(PAYROLL.search(f"{row.get('payee') or ''} {row.get('description') or ''}"))


def progress(b: dict, rows: list[dict], cats: dict[str, dict], balance: float | None) -> dict:
    """Direct deposits (total and count), debit purchases and the balance so far, against the requirements.
    `rows`: the linked account's transactions (None when it isn't linked: what you entered counts)."""
    end = deadline(b).isoformat()
    linked = bool(b.get("account_id"))
    if linked:
        window = [r for r in rows if b["opened_on"] <= r["posted"] <= end]
        dds = [r for r in window if is_direct_deposit(r, cats)]
        dd_sum, dd_n = round(sum(r["amount"] for r in dds), 2), len(dds)
        debits = sum(1 for r in window if r["amount"] < 0 and not (cats.get(r.get("category") or "") or {}).get("is_transfer"))
    else:
        dd_sum, dd_n, debits = round(b.get("manual_dd") or 0, 2), None, b.get("manual_debits") or 0
        balance = None
    checks: list[bool] = []
    if b.get("dd_total"):
        checks.append(dd_sum >= b["dd_total"] - CENT)
    if b.get("dd_count") and dd_n is not None:
        checks.append(dd_n >= b["dd_count"])
    if b.get("debit_count"):
        checks.append(debits >= b["debit_count"])
    balance_ok = None if not b.get("min_balance") or balance is None else balance >= b["min_balance"] - CENT
    if balance_ok is not None:
        checks.append(balance_ok)
    return {"dd_total": dd_sum, "dd_count": dd_n, "debits": debits, "balance": balance, "balance_ok": balance_ok,
            "met": bool(checks) and all(checks), "source": "account" if linked else "manual"}


def state(b: dict, p: dict, today: date) -> str:
    """received | closed | met (the requirements look done: waiting for the bonus) | missed | active."""
    status = b.get("status") or "open"
    if status == "received" or b.get("received_on"):
        return "received"
    if status == "closed":
        return "closed"
    if status == "pending" or p["met"]:
        return "met"
    return "missed" if deadline(b) < today else "active"


def eligibility(b: dict, bonuses: list[dict], today: date) -> dict:
    """When this bank's bonus can be earned again, by the rule you entered for it (banks write it in the offer's
    terms: 'not if you had a bonus in the last 12 months', 'once per lifetime')."""
    if b.get("eligible_on"):
        on = date.fromisoformat(b["eligible_on"])
        return {"status": "now" if on <= today else "later", "on": on.isoformat(), "override": True, "why": "The date you entered"}
    same = [x for x in bonuses if x["owner"] == b["owner"] and x["bank"].strip().lower() == b["bank"].strip().lower()]
    if not any(x["id"] == b["id"] for x in same):
        same.append(b)
    got = sorted(x["received_on"] for x in same if x.get("received_on"))
    if any((x.get("status") or "open") in ("open", "pending") and not x.get("received_on") for x in same):
        return {"status": "in_progress", "on": None, "override": False, "why": "A bonus from this bank is still being earned"}
    if not got:
        return {"status": "now", "on": today.isoformat(), "override": False, "why": "No bonus from this bank yet"}
    if b.get("once_per_lifetime"):
        return {"status": "never", "on": None, "override": False, "why": f"Once per lifetime; received {got[-1]}"}
    if b.get("repeat_months"):
        on = max(today, dates.add_months(date.fromisoformat(got[-1]), b["repeat_months"]))
        return {"status": "now" if on <= today else "later", "on": on.isoformat(), "override": False,
                "why": f"{b['repeat_months']} months after the last bonus ({got[-1]})"}
    return {"status": "unknown", "on": None, "override": False, "why": "Add the bank's rule from the offer's terms"}


def next_fee_reminder(b: dict, today: date) -> date | None:
    """The next monthly statement day (the day of the month it was opened) while there's a fee to keep waived."""
    if not b.get("monthly_fee") or (b.get("status") or "open") == "closed":
        return None
    opened = date.fromisoformat(b["opened_on"])
    for n in range(0, 600):
        d = dates.add_months(opened, n)
        if d >= today and d > opened:
            return d
    return None


# ------------------------------------------------------------------------------------------------ from the database

def overview(conn, today: date) -> tuple[list[dict], dict[str, dict[str, float]]]:
    """Every bank bonus with its progress and dates, and the bonus money each person received per year."""
    rows = db.rows(conn.execute(select(ChurnBankBonus).order_by(ChurnBankBonus.owner, ChurnBankBonus.opened_on, ChurnBankBonus.id)))
    ids = sorted({b["account_id"] for b in rows if b.get("account_id")})
    txs: dict[str, list[dict]] = {}
    balances: dict[str, float | None] = {}
    if ids:
        since = min(b["opened_on"] for b in rows if b.get("account_id"))
        t = Transaction
        for r in db.rows(conn.execute(select(t.account_id, t.posted, t.amount, t.category, t.payee, t.description)
                                      .where(t.account_id.in_(ids), t.posted >= since))):
            txs.setdefault(r["account_id"], []).append(r)
        balances = {r["id"]: r["balance"] for r in conn.execute(select(Account.id, Account.balance).where(Account.id.in_(ids)))}
    cats = {c["name"]: c for c in categories.all_categories(conn)}
    out = []
    for b in rows:
        p = progress(b, txs.get(b.get("account_id") or "", []), cats, balances.get(b.get("account_id") or ""))
        st = state(b, p, today)
        fee = next_fee_reminder(b, today)
        out.append({**b, "progress": p, "state": st, "due": deadline(b).isoformat(),
                    "expected_on": expected_on(b).isoformat() if st in ("active", "met") else None,
                    "safe_close_on": safe_close_on(b).isoformat() if (b.get("status") or "open") != "closed" else None,
                    "fee_reminder": fee.isoformat() if fee else None,
                    "eligibility": eligibility(b, rows, today)})
    income: dict[str, dict[str, float]] = {}
    for b in rows:
        if b.get("received_on"):
            got = b["received_amount"] if b.get("received_amount") is not None else b["bonus"]
            year = income.setdefault(b["owner"], {})
            year[b["received_on"][:4]] = round(year.get(b["received_on"][:4], 0) + got, 2)
    return out, income


def upcoming(bonuses: list[dict], today: date, end: date) -> list[dict]:
    """Upcoming items (churning.upcoming's shape): requirements due, the balance to hold, the bonus posting, safe to
    close, fee-waiver reminders, and a bonus available again."""
    items: list[dict] = []

    def add(day: str, kind: str, b: dict, title: str, detail: str, warn: bool) -> None:
        if date.fromisoformat(day) <= end:
            items.append({"date": day, "kind": kind, "bank_id": b["id"], "card_id": None, "owner": b["owner"],
                          "title": title, "detail": detail, "warn": warn})

    for b in bonuses:
        name = f"{b['bank']} {b.get('account_type') or 'checking'}"
        if b["state"] == "active" and b["due"] >= today.isoformat():
            p, left = b["progress"], []
            if b.get("dd_total") and p["dd_total"] < b["dd_total"]:
                left.append(f"${b['dd_total'] - p['dd_total']:,.0f} more direct deposits")
            if b.get("dd_count") and p["dd_count"] is not None and p["dd_count"] < b["dd_count"]:
                left.append(f"{b['dd_count'] - p['dd_count']} more deposits")
            if b.get("debit_count") and p["debits"] < b["debit_count"]:
                left.append(f"{b['debit_count'] - p['debits']} more debit purchases")
            add(b["due"], "bank_due", b, f"{name}: requirements due", "; ".join(left) or (b.get("other_reqs") or "Check the offer"),
                (date.fromisoformat(b["due"]) - today).days <= DEADLINE_WARN_DAYS)
        if b.get("min_balance") and b.get("hold_until") and b["hold_until"] >= today.isoformat() and b["state"] != "closed":
            add(b["hold_until"], "bank_hold", b, f"{name}: keep ${b['min_balance']:,.0f} in until today", "Then it can come out", False)
        if b["expected_on"] and b["expected_on"] >= today.isoformat():
            add(b["expected_on"], "bank_post", b, f"{name}: ${b['bonus']:,.0f} bonus should have posted",
                "If it hasn't, contact the bank", False)
        if b["safe_close_on"] and b["safe_close_on"] >= today.isoformat() and b["state"] != "closed":
            fee = f" (early-closing fee ${b['early_close_fee']:,.0f} before)" if b.get("early_close_fee") else ""
            add(b["safe_close_on"], "bank_close", b, f"{name}: safe to close", f"From this day{fee}", False)
        if b["fee_reminder"]:
            add(b["fee_reminder"], "bank_fee", b, f"{name}: keep the ${b['monthly_fee']:,.0f} monthly fee waived",
                b.get("fee_waiver") or "Check what waives it", False)
        e = b["eligibility"]
        if e["status"] == "later" and e["on"]:
            add(e["on"], "bank_eligible", b, f"{b['bank']} bonus available again", "By the rule you entered; check the new offer's terms", False)
    return items


def alerts(bonuses: list[dict], today: date) -> list[dict]:
    """A push alert when requirements are due within 14 days and not met yet (with the card-bonus alert's pref)."""
    return [{"key": f"bankbonus:{b['id']}:{b['due']}", "title": f"{b['bank']} bonus requirements due {date.fromisoformat(b['due']):%b %-d}",
             "body": f"For {b['owner']}'s ${b['bonus']:,.0f} bonus.", "url": "/#churning/bank"}
            for b in bonuses if b["state"] == "active" and 0 <= (date.fromisoformat(b["due"]) - today).days <= DEADLINE_WARN_DAYS]


# ------------------------------------------------------------------------------------------------ changes

def save(conn, body: dict, bonus_id: int | None = None) -> int:
    """Add a bank bonus, or change the fields given of one."""
    new = bonus_id is None
    f: dict[str, Any] = {}
    if new or "owner" in body:
        f["owner"] = churning._owner(body.get("owner"), conn)
    if new or "bank" in body:
        f["bank"] = churning._text(body.get("bank"), "bank", 60, required=True)
    if new or "account_type" in body:
        kind = str(body.get("account_type") or "checking")
        if kind not in TYPES:
            raise churning.ChurnError("The account is checking, savings or business")
        f["account_type"] = kind
    if new or "opened_on" in body:
        f["opened_on"] = churning._date(body.get("opened_on"), "day it was opened", required=True)
    if new or "bonus" in body:
        n = churning._num(body.get("bonus"), "bonus", 0, 1e6)
        if n is None:
            raise churning.ChurnError("Enter the bonus")
        f["bonus"] = n
    for key, label, high in (("dd_total", "direct deposit total", 1e7), ("min_balance", "minimum balance", 1e8),
                             ("manual_dd", "direct deposits so far", 1e8), ("received_amount", "amount received", 1e6),
                             ("early_close_fee", "early-closing fee", 1e4)):
        if key in body:
            f[key] = churning._num(body.get(key), label, 0, high)
    if "monthly_fee" in body or new:
        f["monthly_fee"] = churning._num(body.get("monthly_fee"), "monthly fee", 0, 1000) or 0.0
    for key, label, high in (("dd_count", "number of direct deposits", 100), ("debit_count", "number of debit purchases", 1000),
                             ("manual_debits", "debit purchases so far", 10000), ("keep_open_days", "days to keep it open", 3650),
                             ("repeat_months", "months between bonuses", 240)):
        if key in body:
            f[key] = churning._int(body.get(key), label, 0, high)
    if "deadline_days" in body or new:
        f["deadline_days"] = churning._int(body.get("deadline_days"), "days to meet the requirements", 1, 730) or 90
    if "post_days" in body or new:
        n = churning._int(body.get("post_days"), "days for the bonus to post", 0, 365)
        f["post_days"] = 60 if n is None else n
    for key, label in (("hold_until", "day to hold the balance until"), ("deadline", "deadline"), ("received_on", "day it posted"),
                       ("closed_on", "day it was closed"), ("eligible_on", "eligible-again day")):
        if key in body:
            f[key] = churning._date(body.get(key), label)
    for key, label, limit in (("other_reqs", "other requirements", 300), ("fee_waiver", "fee waiver", 200), ("notes", "notes", 2000)):
        if key in body:
            f[key] = churning._text(body.get(key), label, limit)
    if "status" in body or new:
        status = str(body.get("status") or "open")
        if status not in STATUSES:
            raise churning.ChurnError("Status must be open, pending, received or closed")
        f["status"] = status
    if "once_per_lifetime" in body or new:
        f["once_per_lifetime"] = validate.flag(body.get("once_per_lifetime"))
    if "account_id" in body:
        acct = str(body.get("account_id") or "") or None
        if acct and not conn.execute(select(Account.id).where(Account.id == acct, Account.kind.in_(["checking", "savings"]))).fetchone():
            raise churning.ChurnError("Pick one of your checking or savings accounts")
        f["account_id"] = acct
    row = None
    if not new:
        row = conn.orm.get(ChurnBankBonus, bonus_id)
        if row is None:
            raise churning.ChurnError("Bank bonus not found")
    merged = {**(db.as_dict(row) if row else {}), **f}
    if merged.get("closed_on") and merged["closed_on"] < merged["opened_on"]:
        raise churning.ChurnError("It can't be closed before it was opened")
    if merged.get("received_on") and merged["received_on"] < merged["opened_on"]:
        raise churning.ChurnError("The bonus can't post before the account was opened")
    if f.get("received_on") and "status" not in body and merged.get("status") in ("open", "pending"):
        f["status"] = "received"   # a day it posted means it did
    if row is None:
        return int(conn.execute(insert(ChurnBankBonus).values(**f)).lastrowid)
    for k, v in f.items():
        setattr(row, k, v)
    return int(row.id)


def remove(conn, bonus_id: int) -> None:
    conn.execute(delete(ChurnBankBonus).where(ChurnBankBonus.id == bonus_id))
