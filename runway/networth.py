"""Net worth: every account plus things you own (home, vehicles) minus cards and loans.

Account balances come from SimpleFIN. Homes, vehicles and other assets are entered by hand (a home can also be
valued by Realie, see realie.py). An asset can carry a yearly change (say -15% for a car) so its value drifts
between your updates, and can be linked to the loan against it to show equity. A snapshot is saved each day the
numbers are looked at, which builds the history chart going forward.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from sqlalchemy import delete, insert, select, update

from . import db, equity, forecast
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


def summary(conn, today: date | None = None, save: bool = True) -> dict:
    today = today or date.today()
    accts = db.rows(conn.execute(
        select(Account.id, db.account_label_expr(Account).label("name"), Account.org, Account.kind, Account.balance,
               Account.balance_date, Account.owed_positive, Account.owner).where(Account.hidden == 0)))
    groups = {
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
        if a["kind"] in ("credit", "loan"):
            owed = round(forecast.owed(a), 2)
            owed_by_account[a["id"]] = owed
            groups[a["kind"]]["items"].append({**item, "value": owed})
        elif a["kind"] == "investment":
            groups["investments"]["items"].append({**item, "value": round(a["balance"] or 0.0, 2)})
        else:
            groups["cash"]["items"].append({**item, "value": round(a["balance"] or 0.0, 2)})
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

    def change_since(days: int):
        cutoff = (today - timedelta(days=days)).isoformat()
        older = [h for h in hist if h["date"] <= cutoff]
        return round(net - older[-1]["net"], 2) if older else None

    return {
        "today": today.isoformat(), "net": net, "assets": total_assets, "liabilities": total_liab,
        "groups": [g for g in groups.values() if g["items"]],
        "history": hist, "first_snapshot": hist[0]["date"] if hist else None,
        "change": {"30d": change_since(30), "90d": change_since(90), "1y": change_since(365)},
    }


def save_asset(conn, body: dict, asset_id: int | None = None, today: date | None = None) -> int:
    today = today or date.today()
    fields = {}
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


def _num(v, label: str) -> float:
    try:
        return db.number(str(v).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number") from None
