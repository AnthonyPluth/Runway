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

from . import db, equity, forecast, networth
from . import settings_keys as sk

MAX_ROWS = 20   # incomes, events or assets: plenty for one household, and a cap on what's stored

# Numbers the plan keeps: (lowest, highest).
RATES = {"return_before": (-0.2, 0.2), "return_after": (-0.2, 0.2), "volatility": (0.0, 0.5), "inflation": (-0.05, 0.2)}


class PlanError(ValueError):
    pass


def _num(v, label: str, low: float, high: float) -> float:
    try:
        n = db.number(v)
    except (TypeError, ValueError):
        raise PlanError(f"{label} must be a number") from None
    if not low <= n <= high:
        raise PlanError(f"{label} is out of range")
    return n


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
        "plan_to_age": 95, "spending": computed["annual_spending"],
        "return_before": computed["expected_return"], "return_after": 0.04, "volatility": 0.12, "inflation": 0.025,
        "income": [], "events": [], "assets": [],
    }


def sellable(conn, today: date) -> list[dict]:
    """What on the Net worth page can be sold into the plan: homes, vehicles and other assets (less the loan against
    them), and vested company equity."""
    owed = {a["id"]: forecast.owed(a) for a in db.rows(conn.execute(
        "SELECT id, kind, balance, owed_positive FROM accounts WHERE kind IN ('credit','loan')"))}
    out = []
    for a in networth.assets(conn, today):
        out.append({"key": f"asset:{a['id']}", "name": a["name"], "kind": a["kind"], "value": a["current_value"],
                    "yearly_change": (a["yearly_change"] or 0) / 100.0, "owed": round(owed.get(a["loan_account_id"], 0.0), 2)})
    for c in equity.networth_items(conn, today):
        out.append({"key": f"equity:{c['id']}", "name": c["name"], "kind": "equity", "value": c["value"],
                    "yearly_change": 0.0, "owed": 0.0})
    return out


def overview(conn, current: float, computed: dict, today: date) -> dict:
    plan = saved(conn)
    return {"plan": plan or default(computed, today), "is_default": plan is None, "current": round(current, 2),
            "computed": computed, "assets": sellable(conn, today), "year": today.year}
