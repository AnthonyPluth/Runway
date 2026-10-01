"""Retirement planner: a household's savings, spending and income from now to the end of the plan.

The plan is kept whole as JSON (setting "retirement_plan") and the projection itself runs in the browser (a Monte
Carlo simulation, instant as you type: frontend/src/lib/components/investments/planner.ts). Everything is in today's
dollars, so returns are after inflation. Runway fills in what it knows: the investments you have, what you've been
saving and spending, and the homes and equity on the Net worth page, which can be sold into the plan in a year you
choose.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select

from . import db, equity, forecast, loans, networth, validate
from . import settings_keys as sk
from .models import Account

MAX_ROWS = 20   # incomes, events or assets: plenty for one household, and a cap on what's stored
PAYMENT_MATCH = 0.1   # money out within 10% of a loan's payment can be that payment...
PAYMENT_MONTHS = 4    # ... when it's there in at least this many of the 6 months spending is averaged over


def payment_counted(spent: list[dict], payment: float, names: list[str]) -> bool:
    """Whether a loan's monthly payment is in the spending figure: money out within PAYMENT_MATCH of it (from
    portfolio.spent_outflows) in at least PAYMENT_MONTHS different months, as a real payment repeats. When some of
    those name the loan (its lender or account name in the payee), only they count, so a grocery run that happens to
    be the size of a car payment doesn't."""
    near = [s for s in spent if abs(s["amount"] - payment) <= PAYMENT_MATCH * payment]
    names = [n.lower() for n in names if n and len(n.strip()) >= 3]
    named = [s for s in near if any(n in s["text"] for n in names)]
    return len({s["month"] for s in (named or near)}) >= PAYMENT_MONTHS

# Numbers the plan keeps: (lowest, highest).
RATES = {"return_before": (-0.2, 0.2), "return_after": (-0.2, 0.2), "volatility": (0.0, 0.5), "inflation": (-0.05, 0.2)}


class PlanError(ValueError):
    pass


# Numbers go to db.number as sent (no "$" or "," dropped, and true is 1); left out, one isn't a number.
_v = validate.Validator(PlanError, drop="", missing="{label} must be a number", not_number="{label} must be a number",
                        out_of_range="{label} is out of range")


def _num(v, label: str, low: float, high: float) -> float:
    return _v.number(v, label, low, high, required=True)


def _int(v, label: str, low: int, high: int) -> int:
    return round(_num(v, label, low, high))


def _text(v, fallback: str) -> str:
    return (str(v).strip() if v is not None else "")[:60] or fallback


def _rows(v, label: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise PlanError(f"{label} must be a list")
    if len(v) > MAX_ROWS:
        raise PlanError(f"At most {MAX_ROWS} {label.lower()}")
    if not all(isinstance(r, dict) for r in v):
        raise PlanError(f"{label} must be a list")
    return v


def clean(body: dict, today: date) -> dict:
    """The plan as sent by the page, checked and tidied. Raises PlanError for anything that doesn't make sense."""
    if not isinstance(body, dict):
        raise PlanError("The plan must be an object")
    year = today.year
    people_in = _rows(body.get("people"), "People")
    if not 1 <= len(people_in) <= 2:
        raise PlanError("A plan is for one or two people")
    people = []
    for i, p in enumerate(people_in):
        who = _text(p.get("name"), "You" if i == 0 else "Partner")
        people.append({
            "name": who,
            "birth_year": _int(p.get("birth_year"), f"{who}'s birth year", year - 100, year - 14),
            "retire_age": _int(p.get("retire_age"), f"{who}'s retirement age", 30, 90),
            "savings": _num(p.get("savings") or 0, f"{who}'s yearly savings", 0, 1e8),
        })
    plan: dict[str, Any] = {
        "people": people,
        "plan_to_age": _int(body.get("plan_to_age"), "Plan until age", 60, 110),
        "spending": _num(body.get("spending"), "Spending in retirement", 0, 1e8),
        # True once you've typed your own spending figure: loan payments aren't taken off it when they end, as it
        # probably leaves them out already. False while it's Runway's figure from your history, which has them in.
        "spending_own": validate.flag(body.get("spending_own")) == 1,
        **{k: _num(body.get(k), k.replace("_", " ").capitalize(), lo, hi) for k, (lo, hi) in RATES.items()},
        "income": [], "events": [], "assets": [],
    }
    for r in _rows(body.get("income"), "Income"):
        name = _text(r.get("name"), "Income")
        person = _int(r.get("person") or 0, f"{name}: person", 0, len(people) - 1)
        start = _int(r.get("start_age"), f"{name}: starting age", 0, 120)
        end = None if r.get("end_age") in (None, "") else _int(r.get("end_age"), f"{name}: ending age", start, 120)
        plan["income"].append({"name": name, "amount": _num(r.get("amount") or 0, f"{name}: amount", 0, 1e8),
                               "person": person, "start_age": start, "end_age": end})
    for r in _rows(body.get("events"), "Events"):
        name = _text(r.get("name"), "Event")
        plan["events"].append({"name": name, "year": _int(r.get("year"), f"{name}: year", year, year + 100),
                               "amount": _num(r.get("amount") or 0, f"{name}: amount", -1e9, 1e9)})
    seen = set()
    for r in _rows(body.get("assets"), "Assets"):
        key = str(r.get("key") or "")
        if not key.startswith(("asset:", "equity:")) or key in seen:
            raise PlanError("Unknown asset")
        seen.add(key)
        plan["assets"].append({"key": key, "sell_year": _int(r.get("sell_year"), "The year it's sold", year, year + 100)})
    return plan


def saved(conn) -> dict | None:
    raw = db.get_setting(conn, sk.RETIREMENT_PLAN)
    if not raw:
        return None
    try:
        plan = json.loads(raw)
    except ValueError:
        return None
    return plan if isinstance(plan, dict) else None


def save(conn, body: dict | None, today: date | None = None) -> dict | None:
    """Keep the plan; None forgets it (back to Runway's own figures)."""
    if body is None:
        db.set_setting(conn, sk.RETIREMENT_PLAN, None)
        return None
    plan = clean(body, today or date.today())
    db.set_setting(conn, sk.RETIREMENT_PLAN, json.dumps(plan, separators=(",", ":")))
    return plan


def default(computed: dict, today: date) -> dict:
    """A starting plan from Runway's own figures (and the old financial-independence card's, if you'd changed them)."""
    return {
        "people": [{"name": "You", "birth_year": today.year - 40, "retire_age": 65, "savings": computed["yearly_savings"]}],
        "plan_to_age": 95, "spending": computed["annual_spending"], "spending_own": False,
        "return_before": computed["expected_return"], "return_after": 0.04, "volatility": 0.12, "inflation": 0.025,
        "income": [], "events": [], "assets": [],
    }


def sellable(conn, today: date) -> list[dict]:
    """What on the Net worth page can be sold into the plan: homes, vehicles and other assets (less the loan against
    them), and company equity (what will have vested by then). Each says what it's worth and owes today (`value`,
    `owed`, a loan paid down since its last balance as on the Net worth page) and how that changes: `owed_by_year`
    (the loan paid down on its terms, see loans.py) and, for equity, `value_by_year` (what will have vested), each
    indexed by years from today and ending once it stops changing.
    A loan's `loan` also says which account it is (`account_id`), the calendar year of its last payment when it's
    projected (`payoff_year`), and whether its payment is in the spending figure the plan starts from
    (`payment_counted`, see payment_counted()), so the page can take the payment
    off spending once it's paid off or sold. One categorized as a transfer (common when the loan account is synced
    too) wasn't in it, so there's nothing to take off when it ends."""
    from . import portfolio   # imported here: portfolio imports this module
    accts = {a["id"]: a for a in db.rows(conn.execute(
        select(Account.id, Account.kind, Account.balance, Account.balance_date, Account.owed_positive, Account.org, Account.name)
        .where(Account.kind.in_(["credit", "loan"]))))}
    items = networth.assets(conn, today)
    terms = loans.terms(conn, today, [a["loan_account_id"] for a in items if a["loan_account_id"]])
    spent: list[dict] | None = None
    out = []
    for a in items:
        acct = accts.get(a["loan_account_id"])
        loan: dict[str, Any] | None = None
        if acct and acct["kind"] == "loan":
            t = terms.get(acct["id"])
            owed = loans.owed_on(acct, t, today)
            by_year, loan = loans.owed_by_year(owed, t)
            counted = False
            if loan["payment"]:
                spent = portfolio.spent_outflows(conn, today) if spent is None else spent
                counted = payment_counted(spent, loan["payment"], [acct["org"] or "", acct["name"] or ""])
            loan = {**loan, "account_id": acct["id"], "payment_counted": counted,
                    "payoff_year": loans.payoff_year(owed, t, today) if loan["note"] is None else None}
        else:   # none, or a card: what's owed today
            owed = round(forecast.owed({**acct, "balance": acct["balance"] or 0.0}), 2) if acct else 0.0
            by_year = [owed]
        out.append({"key": f"asset:{a['id']}", "name": a["name"], "kind": a["kind"], "value": a["current_value"],
                    "yearly_change": (a["yearly_change"] or 0) / 100.0, "owed": owed, "owed_by_year": by_year, "loan": loan})
    for c in equity.overview(conn, today)["companies"]:
        if not c["in_networth"]:
            continue
        by_year = equity.value_by_year(c, today)
        if not any(by_year):   # nothing vested now or ever (or no share price)
            continue
        out.append({"key": f"equity:{c['id']}", "name": c["name"], "kind": "equity", "value": by_year[0],
                    "value_by_year": by_year, "yearly_change": 0.0, "owed": 0.0, "owed_by_year": [0.0], "loan": None})
    return out


def spending_own(plan: dict, computed: dict) -> bool:
    """Whether a saved plan's spending is a figure you typed. Plans kept before this was recorded are yours when their
    spending is more than a dollar off Runway's figure, else Runway's."""
    if "spending_own" in plan:
        return bool(plan["spending_own"])
    try:
        return abs(float(plan.get("spending") or 0) - computed["annual_spending"]) > 1
    except (TypeError, ValueError):
        return True


def overview(conn, current: float, computed: dict, today: date) -> dict:
    plan = saved(conn)
    if plan is not None and "spending_own" not in plan:
        # Settled once and kept, so Runway's figure moving month to month doesn't change the answer later
        plan["spending_own"] = spending_own(plan, computed)
        db.set_setting(conn, sk.RETIREMENT_PLAN, json.dumps(plan, separators=(",", ":")))
    return {"plan": plan or default(computed, today), "is_default": plan is None, "current": round(current, 2),
            "computed": computed, "assets": sellable(conn, today), "year": today.year}
