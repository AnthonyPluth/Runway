"""Merchant logos: choosing one, and the Logo.dev keys and background fetch."""
from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import update

from ... import db, merchants, monitoring
from ... import settings_keys as sk
from ...models import Merchant
from ..common import ApiError


def api_merchant_logo_options(conn, q, _b):
    """For choosing a merchant's logo: what you chose, and the brands Logo.dev's Brand Search finds for its name."""
    name = (q.get("name", [""])[0] or "").strip()
    out: dict[str, Any] = {"choice": merchants.choice(conn, name), "searchable": merchants.searchable(conn),
           "configured": merchants.configured(conn), "candidates": [], "error": None}
    if name and out["searchable"]:
        found = merchants.search(conn, name)
        if found is None:
            out["error"] = merchants._why
        else:
            out["candidates"] = found[:6]
    return out


def api_merchant_logo(conn, _q, body):
    """Choose the logo for every transaction from a merchant: a website's, none, or (neither) Runway's own pick."""
    try:
        merchants.choose(conn, body.get("name"), (body.get("website") or "").strip() or None, bool(body.get("hidden")))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_holding_logo_options(conn, q, _b):
    """For choosing a holding's logo on Investments: what you chose, and the brands Logo.dev's Brand Search finds for
    its name. A holding is identified by its `group`, which Investments gives each row."""
    try:
        choice = merchants.holding_choice(conn, q.get("group", [""])[0])
    except ValueError as e:
        raise ApiError(str(e)) from e
    name = (q.get("name", [""])[0] or "").strip()
    out: dict[str, Any] = {"choice": choice, "searchable": merchants.searchable(conn),
                           "configured": merchants.configured(conn), "candidates": [], "error": None}
    if name and out["searchable"]:
        found = merchants.search(conn, name)
        if found is None:
            out["error"] = merchants._why
        else:
            out["candidates"] = found[:6]
    return out


def api_holding_logo(conn, _q, body):
    """Choose a holding's logo: a website's, none (its letter), or (neither) Runway's own pick, by ticker or fund family."""
    try:
        merchants.choose_holding(conn, body.get("group"), (body.get("website") or "").strip() or None, bool(body.get("hidden")))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_logodev_status(conn, _q, _b):
    return {**merchants.status(conn), "fetching": _logo_lock.locked()}


def api_logodev_fetch(conn, _q, _b):
    """Fetch every merchant logo that's waiting now, in the background, instead of a batch at each sync, and try again
    the ones Logo.dev had none for."""
    merchants.retry_unknown(conn)
    start_logo_backfill()
    return {"ok": True}


def api_logodev_settings(conn, _q, body):
    """The Logo.dev publishable key, for merchant logos Plaid doesn't have."""
    secret = (body.get("secret") or "").strip()
    if body.get("clear_secret"):
        db.set_setting(conn, sk.LOGODEV_SECRET, None)
    elif secret:
        if not re.fullmatch(r"sk_[A-Za-z0-9_-]{8,200}", secret):
            raise ApiError("That isn't a Logo.dev secret key: it starts with sk_.")
        db.set_setting(conn, sk.LOGODEV_SECRET, secret)
        # look up by name again, now with Brand Search: the ones without a logo, and ones whose logo came from the
        # plain name lookup (which can be the wrong brand)
        # (a logo stays until Brand Search answers: replaced by a clear match, or dropped when there's none)
        conn.execute(update(Merchant).where(Merchant.id.like(merchants.BRAND + "%")).values(logo_checked=None))
        conn.commit()
        start_logo_backfill()
        return {"ok": True, "configured": merchants.configured(conn)}
    key = (body.get("token") or "").strip()
    if body.get("clear"):
        db.set_setting(conn, sk.LOGODEV_TOKEN, None)
    elif key:
        if not re.fullmatch(r"pk_[A-Za-z0-9_-]{8,200}", key):
            raise ApiError("That isn't a Logo.dev publishable key: it starts with pk_ (the secret sk_ key isn't needed).")
        db.set_setting(conn, sk.LOGODEV_TOKEN, key)
        merchants.retry_unknown(conn)
        conn.commit()               # so the fetch below (on its own connection) sees the key
        start_logo_backfill()
    return {"ok": True, "configured": merchants.configured(conn)}


_logo_lock = threading.Lock()


def start_logo_backfill() -> None:
    """Fetch the past year's merchant logos now, in the background, rather than a few at each sync from here on."""
    def run():
        if not _logo_lock.acquire(blocking=False):   # one at a time
            return
        try:
            with db.session() as conn:
                merchants.backfill(conn)
        except Exception:   # logos are a nice-to-have: syncs carry on where this stopped
            monitoring.report()
        finally:
            _logo_lock.release()
    threading.Thread(target=run, daemon=True).start()
