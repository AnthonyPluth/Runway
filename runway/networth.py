"""Net worth: every account plus things you own (home, vehicles) minus cards and loans.

Account balances come from SimpleFIN. Homes, vehicles and other assets are entered by hand (a home can also be
valued by Realie, see realie.py). An asset can carry a yearly change (say -15% for a car) so its value drifts
between your updates, and can be linked to the loan against it to show equity. A loan whose monthly payment you've
entered (Settings → Accounts) is paid down month by month from its last balance, synced or not. A snapshot is saved each day the
numbers are looked at, which builds the history chart going forward.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, insert, select, update

from . import db, equity, forecast, validate
from .models import Account, Asset, AssetValue, NetworthSnapshot

ASSET_KINDS = {"home": "Real estate", "vehicle": "Vehicles", "other": "Other assets"}


def current_value(a: dict, today: date) -> float:
    """The asset's value today: the last value you set, moved by its yearly change since then."""
    v = a["value"] or 0.0
    rate = a.get("yearly_change")
    if rate and a.get("as_of"):
        years = max(0, (today - date.fromisoformat(a["as_of"])).days) / 365.25
        v *= (1 + rate / 100.0) ** years
    return round(v, 2)


def assets(conn, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = db.rows(conn.execute(select(Asset).order_by(Asset.kind, Asset.name)))
    history: dict[int, list[dict]] = {}   # every asset's values in one query, not one per asset
    for v in conn.execute(select(AssetValue.asset_id, AssetValue.date, AssetValue.value, AssetValue.source)
                          .order_by(AssetValue.asset_id, AssetValue.date)):
        history.setdefault(v["asset_id"], []).append({"date": v["date"], "value": v["value"], "source": v["source"]})
    for a in out:
        a["current_value"] = current_value(a, today)
        a["history"] = history.get(a["id"], [])
    return out


def months_between(start: date, end: date) -> int:
    """Monthly payments made after `start` up to `end` (one a month, on start's day of the month); 0 if end is earlier."""
    return max(0, (end.year - start.year) * 12 + end.month - start.month - (1 if end.day < start.day else 0))


def amortize(owed: float, rate: float | None, payment: float | None, months: int) -> float:
    """What's left of `owed` after `months` monthly payments, at `rate` percent a year (none: straight-line). A
    payment that doesn't cover the interest leaves it where it is, and it never goes below zero."""
    if owed <= 0 or not payment or months <= 0:
        return owed
    r = (rate or 0) / 1200
    if payment <= owed * r:
        return owed
    if r == 0:
        return max(0.0, owed - payment * months)
    growth = (1 + r) ** months
    return max(0.0, owed * growth - payment * (growth - 1) / r)


def loan_balance(account: dict, on: date) -> float:
    """What's owed on a loan account on `on`, as a positive amount: its balance, paid down month by month since the
    balance's date when you've entered its monthly payment. Without a payment (or a date), the balance as it is."""
    owed = forecast.owed({**account, "balance": account.get("balance") or 0.0})
    if account.get("kind") != "loan" or not account.get("loan_payment") or not account.get("balance_date"):
        return round(owed, 2)
    try:
        start = date.fromisoformat(str(account["balance_date"])[:10])
    except ValueError:
        return round(owed, 2)
    return round(amortize(owed, account.get("loan_rate"), account["loan_payment"], months_between(start, on)), 2)


def _presented(a: dict, today: date) -> float:
    """An account's value the way the groups show it: credit and loan accounts as a positive amount owed, a loan paid
    down to today when its payment is known."""
    if a["kind"] == "loan":
        return loan_balance(a, today)
    if a["kind"] == "credit":
        return round(forecast.owed({**a, "balance": a["balance"] or 0.0}), 2)
    return round(a["balance"] or 0.0, 2)


def summary(conn, today: date | None = None, save: bool = True) -> dict:
    today = today or date.today()
    accts = db.rows(conn.execute(
        select(Account.id, db.account_label_expr(Account).label("name"), Account.org, Account.kind, Account.balance,
               Account.balance_date, Account.owed_positive, Account.owner, Account.networth_hidden, Account.loan_rate,
               Account.loan_payment).where(Account.hidden == 0)))
    left_out = [a for a in accts if a["networth_hidden"]]   # you left these out of Net worth: listed at the bottom, to bring back
    accts = [a for a in accts if not a["networth_hidden"]]
    groups: dict[str, dict[str, Any]] = {
        "cash": {"key": "cash", "label": "Cash", "side": "asset", "items": []},
        "investments": {"key": "investments", "label": "Investments", "side": "asset", "items": []},
        "equity": {"key": "equity", "label": "Equity (vested)", "side": "asset", "items": []},
        "home": {"key": "home", "label": "Real estate", "side": "asset", "items": []},
        "vehicle": {"key": "vehicle", "label": "Vehicles", "side": "asset", "items": []},
        "other": {"key": "other", "label": "Other assets", "side": "asset", "items": []},
        "credit": {"key": "credit", "label": "Credit cards", "side": "liability", "items": []},
        "loan": {"key": "loan", "label": "Loans", "side": "liability", "items": []},
    }
    owed_by_account = {}
    for a in accts:
        item = {"type": "account", "id": a["id"], "name": a["name"], "org": a["org"], "as_of": a["balance_date"], "owner": a["owner"]}
        value = _presented(a, today)
        if a["kind"] in ("credit", "loan"):
            owed_by_account[a["id"]] = value
            synced = round(forecast.owed({**a, "balance": a["balance"] or 0.0}), 2)
            if value != synced:   # paid down since the balance's date: what it was then, too
                item["synced"] = synced
            groups[a["kind"]]["items"].append({**item, "value": value})
        else:
            groups["investments" if a["kind"] == "investment" else "cash"]["items"].append({**item, "value": value})
    names = {a["id"]: a["name"] for a in accts}
    for a in assets(conn, today):
        item = {"type": "asset", "id": a["id"], "name": a["name"], "value": a["current_value"], "as_of": a["as_of"],
                "kind": a["kind"], "source": a["source"]}
        if a["loan_account_id"] in owed_by_account:
            item["loan"] = {"account_id": a["loan_account_id"], "name": names.get(a["loan_account_id"]),
                            "owed": owed_by_account[a["loan_account_id"]]}
            item["equity"] = round(a["current_value"] - owed_by_account[a["loan_account_id"]], 2)
        groups[a["kind"] if a["kind"] in groups else "other"]["items"].append(item)
    groups["equity"]["items"] = equity.networth_items(conn, today)
    for g in groups.values():
        g["items"].sort(key=lambda i: -abs(i["value"]))
        g["total"] = round(sum(i["value"] for i in g["items"]), 2)
    total_assets = round(sum(g["total"] for g in groups.values() if g["side"] == "asset"), 2)
    total_liab = round(sum(g["total"] for g in groups.values() if g["side"] == "liability"), 2)
    net = round(total_assets - total_liab, 2)
    if save:
        db.upsert(conn, NetworthSnapshot, {"date": today.isoformat(), "assets": total_assets, "liabilities": total_liab,
                                           "net": net, "detail": json.dumps({k: g["total"] for k, g in groups.items()})},
                  key=["date"])
    hist = db.rows(conn.execute(select(NetworthSnapshot).order_by(NetworthSnapshot.date)))
    for h in hist:
        h["detail"] = json.loads(h["detail"] or "{}")

    def change_since(days: int) -> tuple[float | None, str | None]:
        """The change since the latest snapshot at least `days` old, and that snapshot's date: snapshots are only saved
        on days the numbers are looked at, so "30 days" can really be longer."""
        cutoff = (today - timedelta(days=days)).isoformat()
        older = [h for h in hist if h["date"] <= cutoff]
        return (round(net - older[-1]["net"], 2), older[-1]["date"]) if older else (None, None)

    changes = {k: change_since(days) for k, days in (("30d", 30), ("90d", 90), ("1y", 365))}

    return {
        "today": today.isoformat(), "net": net, "assets": total_assets, "liabilities": total_liab,
        "groups": [g for g in groups.values() if g["items"]],
        "excluded": [{"id": a["id"], "name": a["name"], "org": a["org"], "kind": a["kind"], "balance": _presented(a, today)} for a in left_out],
        "history": hist, "first_snapshot": hist[0]["date"] if hist else None,
        "change": {k: amount for k, (amount, _) in changes.items()},
        "change_since": {k: since for k, (_, since) in changes.items()},   # the snapshot each change is measured from
    }


def save_asset(conn, body: dict, asset_id: int | None = None, today: date | None = None) -> int:
    today = today or date.today()
    fields: dict[str, Any] = {}
    if "name" in body or asset_id is None:
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("Give it a name")
        fields["name"] = name[:80]
    if "kind" in body or asset_id is None:
        kind = body.get("kind") or "other"
        if kind not in ASSET_KINDS:
            raise ValueError("Kind must be home, vehicle or other")
        fields["kind"] = kind
    if "auto_update" in body:
        fields["auto_update"] = 1 if body.get("auto_update") else 0
    for key in ("url", "address", "notes"):
        if key in body:
            fields[key] = (body.get(key) or "").strip() or None
    if fields.get("url") and not re.match(r"https?://", fields["url"], re.I):   # it's a link in the app: never javascript:
        raise ValueError("The link must be a web address starting with https://")
    if "loan_account_id" in body:
        lid = body.get("loan_account_id") or None
        if lid and not conn.execute(select(Account.id).where(Account.id == lid, Account.kind == "loan")).fetchone():
            raise ValueError("Pick one of your loan accounts")
        fields["loan_account_id"] = lid
    if "yearly_change" in body:
        yc = body.get("yearly_change")
        fields["yearly_change"] = None if yc in (None, "") else _num(yc, "Yearly change")
        if fields["yearly_change"] is not None and not -60 <= fields["yearly_change"] <= 60:
            raise ValueError("Yearly change should be between -60% and 60%")
    new_value = None
    if "value" in body or asset_id is None:
        new_value = _num(body.get("value"), "Value")
        if new_value < 0:
            raise ValueError("Value can't be negative")
    if asset_id is None:
        cur = conn.execute(insert(Asset).values(name=fields.pop("name"), kind=fields.pop("kind"), value=new_value,
                                                as_of=today.isoformat(), source=body.get("source") or "manual"))
        asset_id = cur.lastrowid
    elif not conn.execute(select(Asset.id).where(Asset.id == asset_id)).fetchone():
        raise ValueError("Not found")
    if fields:
        conn.execute(update(Asset).where(Asset.id == asset_id).values(**fields))
    if new_value is not None:
        set_value(conn, asset_id, new_value, body.get("source") or "manual", today)
    return asset_id


def set_value(conn, asset_id: int, value: float, source: str, today: date, low=None, high=None) -> None:
    conn.execute(update(Asset).where(Asset.id == asset_id)
                 .values(value=round(value, 2), as_of=today.isoformat(), source=source, low=low, high=high))
    db.upsert(conn, AssetValue, {"asset_id": asset_id, "date": today.isoformat(), "value": round(value, 2), "source": source},
              key=["asset_id", "date"])


def remove_asset(conn, asset_id: int) -> None:
    conn.execute(delete(AssetValue).where(AssetValue.asset_id == asset_id))
    conn.execute(delete(Asset).where(Asset.id == asset_id))


_v = validate.Validator(ValueError, drop=",$%", missing="{label} must be a number", not_number="{label} must be a number")


def _num(v, label: str) -> float:
    return _v.number(v, label, required=True)
