"""Retirement planner: a household's savings, spending and income from now to the end of the plan.

The plan is kept whole as JSON (setting "retirement_plan") and the projection itself runs in the browser (a Monte
Carlo simulation, instant as you type: frontend/src/lib/components/investments/planner.ts). Everything is in today's
dollars, so returns are after inflation. Runway fills in what it knows: the investments you have, what you've been
saving and spending, and the homes and equity on the Net worth page, shown alongside the investments until you sell
them into the plan in a year you choose (vehicles lose value, so they aren't counted; their loans' payments are, as are
those of loans against nothing, like a student loan).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select

from . import categorize, db, equity, forecast, loans, networth, validate
from . import settings_keys as sk
from .models import Account

MAX_ROWS = 20   # incomes, events or assets: plenty for one household, and a cap on what's stored
PAYMENT_MATCH = 0.1   # money out within 10% of a loan's payment can be that payment...
PAYMENT_MONTHS = 4    # ... when it's there in at least this many of the 6 months spending is averaged over
PAYMENT_ESCROW = 1.5  # one that names the loan can be up to this much of its payment: a mortgage payment often carries
                      # escrow (property tax and insurance) on top of the principal and interest the lender reports


def payment_counted(spent: list[dict], payment: float, names: list[str], named_only: bool = False,
                    history: list[str] | None = None) -> bool:
    """Whether a loan's monthly payment is in the spending figure: money out within PAYMENT_MATCH of it (from
    portfolio.spent_outflows) in at least PAYMENT_MONTHS different months, as a real payment repeats. When some of
    those name the loan (its lender or account name in the payee), only they count, so a grocery run that happens to
    be the size of a car payment doesn't; and one that names it can be more than the payment, up to PAYMENT_ESCROW
    times it, as a mortgage paid with its escrow is. With `named_only`, only ones that name it count, within
    PAYMENT_MATCH and not a card's payment: for transfers, which repeat at fixed amounts (to savings, a brokerage),
    and whose lender is often also the bank of a card paid by transfer ("CHASE CREDIT CRD AUTOPAY").
    `history` is the full months Runway has transactions for (portfolio.history_months). With fewer than
    PAYMENT_MONTHS of them (a bank linked lately), it's found when it's in every one of them: at least two, or a
    single month's when it names the loan. None: the 6 months are taken as all there."""
    names = [n.lower() for n in names if n and len(n.strip()) >= 3]
    low = (1 - PAYMENT_MATCH) * payment
    high = (1 + PAYMENT_MATCH if named_only else PAYMENT_ESCROW) * payment
    named = [s for s in spent if low <= s["amount"] <= high and any(n in s["text"] for n in names)
             and not (named_only and categorize.is_card_payment(s["text"]))]
    near = [s for s in spent if abs(s["amount"] - payment) <= PAYMENT_MATCH * payment]
    months = {s["month"] for s in (named if named_only else named or near)}
    if history is None or len(history) >= PAYMENT_MONTHS:
        return len(months) >= PAYMENT_MONTHS
    if len(history) == 1 and not named:   # one month of a lookalike amount is too little to go on
        return False
    return bool(history) and set(history) <= months

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
        sell_year = _int(r.get("sell_year"), "The year it's sold", -10_000, 10_000)
        if sell_year < year:
            raise PlanError(f"A sale can’t be in the past: sell in {year} or later")
        if sell_year > year + 100:
            raise PlanError(f"A sale can be at most 100 years out, in {year + 100} at the latest")
        plan["assets"].append({"key": key, "sell_year": sell_year})
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
    today = today or date.today()
    plan = clean(body, today)
    vehicles = {f"asset:{a['id']}" for a in networth.assets(conn, today) if a["kind"] == "vehicle"}
    if any(s["key"] in vehicles for s in plan["assets"]):
        raise PlanError("Vehicles aren’t sold into the plan: they lose value, so the plan leaves them out")
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
    """What on the Net worth page the plan counts: homes and other assets (less the loan against them) and company
    equity (what will have vested by then), held until you sell them into the plan. Vehicles are listed too, but only
    for their loan's payment: they lose value, so the page neither counts nor sells them. So are loans against nothing
    (a student or personal loan, kind "loan", key "loan:<account id>", worth nothing): debts, there for their payment,
    paid down on their terms like the rest. Each says what it's worth and
    owes today (`value`, `owed`, a loan paid down since its last balance as on the Net worth page) and how that
    changes: `yearly_change` (a fraction; None when it's not set, and for equity, which the page takes as keeping pace
    with inflation), `owed_by_year` (the loan paid down on its terms, see loans.py) and, for equity, `value_by_year`
    (what will have vested, at today's share price), each indexed by years from today and ending once it stops
    changing.
    A loan's `loan` also says which account it is (`account_id`), the calendar year of its last payment when it's
    projected (`payoff_year`), and whether its payment is in the spending figure the plan starts from
    (`payment_counted`, see payment_counted()), so the page can take the payment
    off spending once it's paid off or sold. One found paid as a transfer instead (common when the loan account is
    synced too) wasn't in it (False), so the page adds it to spending, for as long as it's still being paid. One found
    in neither (a month without it, payments in parts, escrow under another name) is None: Runway can't tell, and the
    page leaves spending as it is, since adding a payment that's already in it would count it twice."""
    from . import portfolio   # imported here: portfolio imports this module
    accts = {a["id"]: a for a in db.rows(conn.execute(
        select(Account.id, Account.kind, Account.balance, Account.balance_date, Account.owed_positive, Account.org, Account.name,
               Account.display_name, Account.hidden, Account.networth_hidden)
        .where(Account.kind.in_(["credit", "loan"]))))}
    items = networth.assets(conn, today)
    linked = {a["loan_account_id"] for a in items if a["loan_account_id"]}
    # Loans against nothing (a student or personal loan) that net worth counts: debts, for their payment
    alone = [a for a in accts.values() if a["kind"] == "loan" and a["id"] not in linked and not a["hidden"]
             and not a["networth_hidden"]]
    terms = loans.terms(conn, today, sorted(linked) + [a["id"] for a in alone])
    found: dict[str, list] = {}

    def projected(acct: dict) -> tuple[float, list[float], dict]:
        """A loan account's balance today, paid down year by year, and its `loan` (see above)."""
        t = terms.get(acct["id"])
        owed = loans.owed_on(acct, t, today)
        by_year, loan = loans.owed_by_year(owed, t)
        counted: bool | None = None
        if loan["payment"]:
            names = [acct["org"] or "", acct["name"] or ""]
            if not found:
                found.update(spent=portfolio.spent_outflows(conn, today), moved=portfolio.transfer_outflows(conn, today),
                             history=portfolio.history_months(conn, today))
            counted = (True if payment_counted(found["spent"], loan["payment"], names, history=found["history"])
                       else False if payment_counted(found["moved"], loan["payment"], names, named_only=True,
                                                     history=found["history"])
                       else None)
        loan = {**loan, "account_id": acct["id"], "payment_counted": counted,
                "payoff_year": loans.payoff_year(owed, t, today) if loan["note"] is None else None}
        return owed, by_year, loan

    out = []
    for a in items:
        acct = accts.get(a["loan_account_id"])
        loan: dict[str, Any] | None = None
        if acct and acct["kind"] == "loan":
            owed, by_year, loan = projected(acct)
        else:   # none, or a card: what's owed today
            owed = round(forecast.owed({**acct, "balance": acct["balance"] or 0.0}), 2) if acct else 0.0
            by_year = [owed]
        yearly = a["yearly_change"]
        out.append({"key": f"asset:{a['id']}", "name": a["name"], "kind": a["kind"], "value": a["current_value"],
                    "yearly_change": None if yearly is None else yearly / 100.0, "owed": owed, "owed_by_year": by_year,
                    "loan": loan})
    for acct in sorted(alone, key=lambda a: (a["display_name"] or a["name"] or "").lower()):
        owed, by_year, loan = projected(acct)
        if owed <= 0:   # paid off: nothing to plan for
            continue
        out.append({"key": f"loan:{acct['id']}", "name": acct["display_name"] or acct["name"], "kind": "loan", "value": 0.0,
                    "yearly_change": None, "owed": owed, "owed_by_year": by_year, "loan": loan})
    for c in equity.overview(conn, today)["companies"]:
        if not c["in_networth"]:
            continue
        by_year = equity.value_by_year(c, today)
        if not any(by_year):   # nothing vested now or ever (or no share price)
            continue
        out.append({"key": f"equity:{c['id']}", "name": c["name"], "kind": "equity", "value": by_year[0],
                    "value_by_year": by_year, "yearly_change": None, "owed": 0.0, "owed_by_year": [0.0], "loan": None})
    return out


def overview(conn, current: float, computed: dict, today: date) -> dict:
    plan = saved(conn)
    assets = sellable(conn, today)
    if plan is not None:
        # A plan kept before this was recorded is taken as Runway's figure: it was saved whole whenever anything
        # changed, so its spending can't tell a typed figure from Runway's. The page records a real edit from now on.
        plan["spending_own"] = bool(plan.get("spending_own"))
        # A sale kept for a year that's now past is counted this year (`was` says when it was set for, so the page can
        # say so), and a vehicle's sale (from before vehicles were left out) isn't counted at all. Only what's shown
        # changes: the plan is written back as it is next time you change something.
        vehicles = {a["key"] for a in assets if a["kind"] == "vehicle"}
        sales = []
        for s in plan.get("assets") or []:
            if not isinstance(s, dict) or s.get("key") in vehicles:
                continue
            if isinstance(s.get("sell_year"), (int, float)) and s["sell_year"] < today.year:
                s = {**s, "sell_year": today.year, "was": s["sell_year"]}
            sales.append(s)
        plan["assets"] = sales
    return {"plan": plan or default(computed, today), "is_default": plan is None, "current": round(current, 2),
            "computed": computed, "assets": assets, "year": today.year}
