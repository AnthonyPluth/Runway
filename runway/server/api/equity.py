"""Equity (companies and grants you enter) and the Carta connection that can fill it in."""
from __future__ import annotations

import os
import urllib.parse

from sqlalchemy import select

from ... import carta, equity
from ...models import EquityGrant
from ..common import ApiError, host_allowed, text


def carta_redirect_uri(origin: str | None = None) -> str:
    """Where Carta sends you back: Runway's public address (or the one you're using) + /carta/callback. It must be
    registered as a redirect URI for your app in Carta's developer portal."""
    base = (os.environ.get("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    if not base and origin and host_allowed(urllib.parse.urlsplit(origin).netloc):
        base = origin.rstrip("/")
    return (base or "http://localhost:8765") + "/carta/callback"


def api_equity(conn, _q, _b):
    out = equity.overview(conn)
    out["carta"] = carta.settings(conn)
    return out


def _equity(fn, *args):
    try:
        return fn(*args)
    except equity.EquityError as e:
        raise ApiError(str(e)) from e


def api_equity_company_add(conn, _q, body):
    return {"id": _equity(equity.save_company, conn, body)}


def api_equity_company_update(conn, _q, body, cid):
    return {"id": _equity(equity.save_company, conn, body, cid)}


def api_equity_company_remove(conn, _q, _b, cid):
    equity.remove_company(conn, cid)
    return {"ok": True}


def api_equity_grant_add(conn, _q, body, cid):
    return {"id": _equity(equity.save_grant, conn, cid, body)}


def api_equity_grant_update(conn, _q, body, gid):
    row = conn.execute(select(EquityGrant.company_id).where(EquityGrant.id == gid)).fetchone()
    if not row:
        raise ApiError("Grant not found", 404)
    return {"id": _equity(equity.save_grant, conn, row["company_id"], body, gid)}


def api_equity_grant_remove(conn, _q, _b, gid):
    equity.remove_grant(conn, gid)
    return {"ok": True}


def api_carta_settings(conn, _q, body):
    if any(body.get(k) is not None and not isinstance(body[k], str) for k in ("env", "client_id", "client_secret", "origin")):
        raise ApiError("Send Carta's settings as text")
    try:
        carta.save_settings(conn, body)
    except carta.CartaError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "redirect_uri": carta_redirect_uri(body.get("origin"))}


def api_carta_connect(conn, _q, body):
    """Where to send you to approve Runway at Carta."""
    try:
        return {"url": carta.authorize_url(conn, carta_redirect_uri(text(body.get("origin"), "origin") or None))}
    except carta.CartaError as e:
        raise ApiError(str(e)) from e


def api_carta_sync(conn, _q, _b):
    try:
        return carta.sync(conn)
    except carta.CartaError as e:
        raise ApiError(str(e), 502) from e


def api_carta_disconnect(conn, _q, _b):
    carta.disconnect(conn)
    return {"ok": True}
