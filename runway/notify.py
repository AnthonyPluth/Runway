"""Push notifications: which devices get them, what's worth telling you about, and not saying the same thing twice.

Checked after every sync (daily, and when you open Runway). Each alert has a key (e.g. the card and its due date), and
a key is only ever sent once.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import date, timedelta

from . import db, webpush

DEFAULTS = {
    "card_due": True, "card_due_days": 3,           # a card payment is due within N days
    "low_balance": True, "low_balance_below": 500,  # the forecast dips below $X in the next 30 days
    "missed": True,                                 # a recurring payment didn't show up
    "big_charge": True, "big_charge_over": 500,     # a single charge over $X
    "review": False,                                # transactions waiting for a category (once a day)
    "sync_failed": True,                            # syncing has failed for a day
}
LOW_BALANCE_DAYS = 30


def prefs(conn) -> dict:
    try:
        saved = json.loads(db.get_setting(conn, "notify_prefs") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_prefs(conn, body: dict) -> dict:
    p = prefs(conn)
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[k], bool):
            p[k] = bool(v)
        else:
            try:
                p[k] = max(0, db.number(v)) if k != "card_due_days" else max(0, min(14, int(v)))
            except (TypeError, ValueError):
                raise ValueError("Enter a number") from None
    db.set_setting(conn, "notify_prefs", json.dumps(p))
    return p


# ------------------------------------------------------------------------------------------------ devices

def subscriptions(conn) -> list[dict]:
    return db.rows(conn.execute("SELECT * FROM push_subscriptions ORDER BY created"))


def subscribe(conn, sub: dict, device: str, user_sub: str | None) -> None:
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("That isn't a push subscription.")
    if not push_host_allowed(endpoint):   # Runway posts to this address, so it mustn't be just any server
        raise ValueError("That push service isn't one Runway knows. Add its host to RUNWAY_PUSH_HOSTS if you trust it.")
    if not webpush.valid_public_key(keys["p256dh"]):
        raise ValueError("That isn't a push subscription.")
    conn.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, device, user_sub, created) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, device=excluded.device",
                 (endpoint, keys["p256dh"], keys["auth"], (device or "This device")[:80], user_sub, time.time()))


# The browsers' push services (Chrome and Edge through Google or Windows, Firefox through Mozilla, Safari through Apple).
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
              "notify.windows.com")


def push_host_allowed(endpoint: str) -> bool:
    host = (urllib.parse.urlsplit(endpoint).hostname or "").lower()
    extra = tuple(h.strip().lower() for h in (os.environ.get("RUNWAY_PUSH_HOSTS") or "").split(",") if h.strip())
    return bool(host) and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS + extra)


def unsubscribe(conn, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


def subject(conn) -> str:
    """Who the push services can contact about these messages (required by Apple): your email, or Runway's address."""
    row = conn.execute("SELECT email FROM users WHERE email IS NOT NULL ORDER BY last_seen DESC LIMIT 1").fetchone()
    if row and row["email"]:
        return f"mailto:{row['email']}"
    public = (os.environ.get("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    return public if public.startswith("https://") else "mailto:runway@example.com"


def send_all(conn, message: dict, only: str | None = None) -> dict:
    """Send to every device (or one endpoint). Dead subscriptions are removed."""
    vapid, _pub = webpush.vapid_keys(conn)
    sent, failed = 0, []
    for s in subscriptions(conn):
        if only and s["endpoint"] != only:
            continue
        if not push_host_allowed(s["endpoint"]):   # from a restored backup, never checked when it was saved
            unsubscribe(conn, s["endpoint"])
            continue
        try:
            webpush.send(s, message, vapid, subject(conn))
            conn.execute("UPDATE push_subscriptions SET last_ok=?, last_error=NULL WHERE endpoint=?", (time.time(), s["endpoint"]))
            sent += 1
        except webpush.Gone:
            unsubscribe(conn, s["endpoint"])
        except Exception as e:   # one bad device shouldn't stop the rest
            conn.execute("UPDATE push_subscriptions SET last_error=? WHERE endpoint=?", (str(e)[:300], s["endpoint"]))
            failed.append(f"{s['device']}: {e}")
    return {"sent": sent, "failed": failed}


# ------------------------------------------------------------------------------------------------ what to say

def _fmt(v: float) -> str:
    return f"${abs(v):,.2f}"


def _when(d: str, today: date) -> str:
    days = (date.fromisoformat(d) - today).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return date.fromisoformat(d).strftime("%A")
    return date.fromisoformat(d).strftime("%b %-d")


def alerts(conn, today: date, p: dict) -> list[dict]:
    """Everything worth saying right now, each with a key so it's only said once."""
    from . import forecast, recurring
    out = []
    fc = forecast.build(conn, today, max(LOW_BALANCE_DAYS, 30))
    if p["card_due"]:
        for c in fc["cards"]:
            days = (date.fromisoformat(c["due_date"]) - today).days
            if c["remaining"] > 0.005 and 0 <= days <= p["card_due_days"]:
                pay = next((e for e in fc["events"] if e.get("kind") == "card" and e.get("key") == f"card:{c['id']}:{c['due_date']}"
                            and e["name"].startswith(c["name"])), None)
                out.append({"key": f"card:{c['id']}:{c['due_date']}", "title": f"{c['name']} payment due {_when(c['due_date'], today)}",
                            "body": f"{_fmt(c['remaining'])}" + (f" comes out of {pay['account']}." if pay else " is left to pay on this statement."),
                            "url": "/#overview"})
    if p["low_balance"] and fc["total"]:
        horizon = fc["total"][:LOW_BALANCE_DAYS + 1]
        i = min(range(len(horizon)), key=lambda k: horizon[k])
        if horizon[i] < p["low_balance_below"]:
            d = fc["dates"][i]
            cause = sorted((e for e in fc["events"] if e["date"] == d and e["amount"] < 0), key=lambda e: e["amount"])
            what = "Checking" if len(fc["accounts"]) == 1 else "Your cash"
            out.append({"key": f"low:{d}:{int(horizon[i] // 250)}",
                        "title": f"{what} {'goes negative' if horizon[i] < 0 else 'gets low'} {_when(d, today)}",
                        "body": f"The forecast dips to {'−' if horizon[i] < 0 else ''}{_fmt(horizon[i])} on {date.fromisoformat(d):%a, %b %-d}"
                                + (f", when {cause[0]['name']} goes out." if cause else "."),
                        "url": "/#overview", "urgency": "high"})
    if p["missed"]:
        for m in recurring.missed(conn, today):
            out.append({"key": f"missed:{m['key']}", "title": f"Missed payment: {m['name']}",
                        "body": f"{_fmt(m['amount'])} was expected around {date.fromisoformat(m['date']):%b %-d} and hasn't shown up.",
                        "url": "/#recurring"})
    if p["big_charge"]:
        since = (today - timedelta(days=3)).isoformat()
        for t in conn.execute(
                "SELECT t.id, t.amount, COALESCE(t.payee, t.description) AS who, " + db.label_sql("a") + " AS acct "
                "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN categories c ON c.name=t.category "
                "WHERE t.posted>=? AND t.amount<=? AND a.kind IN ('checking','savings','credit') AND a.hidden=0 "
                "AND COALESCE(c.is_transfer, 0)=0", (since, -float(p["big_charge_over"]))).fetchall():
            out.append({"key": f"big:{t['id']}", "title": f"{_fmt(t['amount'])} at {t['who']}",
                        "body": f"On {t['acct']}.", "url": "/#transactions"})
    if p["review"]:
        n = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE needs_review=1 AND {db.NOT_INVESTMENT}").fetchone()[0]
        if n:
            out.append({"key": f"review:{today.isoformat()}", "title": f"{n} transaction{'s' if n != 1 else ''} to review",
                        "body": "They're waiting for a category.", "url": "/#review"})
    if p["sync_failed"]:
        last_ok = db.get_setting(conn, "last_sync_ok")
        log = conn.execute("SELECT ok, message FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        stale = not last_ok or (date.today() - date.fromisoformat(last_ok[:10])).days >= 1
        if log and not log["ok"] and stale and db.get_setting(conn, "simplefin_access_url"):
            out.append({"key": f"syncfail:{today.isoformat()}", "title": "Runway can't sync with your bank",
                        "body": (log["message"] or "The last sync failed.")[:160], "url": "/#setup/connections"})
    return out


def run(conn, today: date | None = None) -> dict:
    """Send anything new. Nothing is sent (or remembered) while no device is subscribed, so turning notifications on
    later doesn't bring a flood of old alerts, except what's still true that day."""
    today = today or date.today()
    if not conn.execute("SELECT 1 FROM push_subscriptions").fetchone():
        return {"sent": 0, "alerts": 0}
    p = prefs(conn)
    sent = 0
    for a in alerts(conn, today, p):
        if conn.execute("SELECT 1 FROM notify_log WHERE key=?", (a["key"],)).fetchone():
            continue
        # Saved before sending: if anything later rolled this back, the next run would send the same alert again.
        conn.execute("INSERT INTO notify_log(key, sent, title) VALUES (?,?,?)", (a["key"], time.time(), a["title"]))
        conn.commit()
        r = send_all(conn, {"title": a["title"], "body": a["body"], "url": a.get("url", "/"), "tag": a["key"]})
        sent += r["sent"]
    conn.execute("DELETE FROM notify_log WHERE sent < ?", (time.time() - 120 * 86400,))
    return {"sent": sent}
