"""Push notifications: which devices get them, what's worth telling you about, and not saying the same thing twice.

Checked after every sync (daily, and when you open Runway). Each alert has a key (e.g. the card and its due date), and
a key is only ever sent once.

With sign-in (OIDC), notifications are each person's own: their devices, what they want to hear about, and what they've
been sent. Nobody sees or changes anyone else's. Without it there's one person, and every device is theirs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, or_, select, update

from . import banks, churning, dates, db, forecast, oidc, recurring, validate, webpush
from . import settings_keys as sk
from .models import Account, Category, NotifyLog, PushSubscription, SyncLog, Transaction, User

DEFAULTS = {
    "card_due": True, "card_due_days": 3,           # a card payment is due within N days
    "low_balance": True, "low_balance_below": 500,  # the forecast dips below $X in the next 30 days
    "missed": True,                                 # a recurring payment didn't show up
    "big_charge": True, "big_charge_over": 500,     # a single charge over $X
    "review": False,                                # transactions waiting for a category (once a day)
    "sync_failed": True,                            # syncing has failed for a day
    "churn_fee": True,                              # a churning card's annual fee is due within 30 days
    "churn_bonus": True,                            # a sign-up bonus deadline is within 14 days, with spending left
    "churn_plan": True,                             # time to downgrade, close or change a card, as you planned
    "churn_benefit": True,                          # a card credit with money left resets soon
    "churn_apply": True,                            # a planned card or bank bonus can be applied for, or its offer ends soon
}
LOW_BALANCE_DAYS = 30


def prefs(conn, user_sub: str | None = None) -> dict:
    """What a person wants to be told about (everyone's, without sign-in). Someone who hasn't chosen yet has what was
    chosen for everyone before notifications were each person's own."""
    raw = db.get_setting(conn, sk.notify_prefs(user_sub)) if user_sub else None
    try:
        saved = json.loads(raw or db.get_setting(conn, sk.NOTIFY_PREFS) or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


_check = validate.Validator(ValueError, drop="", missing="Enter a number", not_number="Enter a number")


def save_prefs(conn, body: dict, user_sub: str | None = None) -> dict:
    p = prefs(conn, user_sub)
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[k], bool):
            p[k] = bool(validate.flag(v))   # a switch: "false" is off
        else:
            n = _check.number(v, k, required=True)
            p[k] = max(0, n) if k != "card_due_days" else max(0, min(14, int(n)))
    db.set_setting(conn, sk.notify_prefs(user_sub) if user_sub else sk.NOTIFY_PREFS, json.dumps(p))
    return p


# ------------------------------------------------------------------------------------------------ devices

def subscriptions(conn) -> list[dict]:
    """Every device, oldest first (after dropping those whose person was taken off the sign-in list: see lapsed)."""
    prune_lapsed(conn)
    return db.rows(conn.execute(select(PushSubscription).order_by(PushSubscription.created)))


def person(user: dict | None) -> str | None:
    """Whose notifications a request is about: the signed-in person's sub, or None without sign-in (one person)."""
    return (user or {}).get("sub") if oidc.enabled() else None


def devices(conn, user_sub: str | None) -> list[dict]:
    """The devices a person sees under Settings → Notifications: theirs, and any turned on before there was sign-in
    (nobody's: they get nothing until turned on again from the device, which makes them that person's). Without
    sign-in, every device."""
    subs = subscriptions(conn)
    if not oidc.enabled():
        return subs
    return [s for s in subs if (user_sub and s["user_sub"] == user_sub) or not s["user_sub"]]


def lapsed(conn, sub: dict, now: float | None = None) -> str | None:
    """Why a device gets nothing for now, or None: the person who turned notifications on can no longer sign in
    (oidc.access_lapsed), as with their sessions. "user_removed" (taken off OIDC_ALLOWED_EMAILS) is for good, and the
    device is dropped (prune_lapsed). "sign_in_lapsed" (with OIDC_ALLOWED_GROUPS, nobody has seen them sign in for
    RUNWAY_SESSION_DAYS) may be someone who just hasn't opened Runway lately: the device is kept and only skipped,
    and their alerts come back on their own when they next sign in. A device subscribed without sign-in has no person
    and never lapses."""
    if not sub.get("user_sub"):
        return None
    email = conn.execute(select(User.email).where(User.sub == sub["user_sub"])).scalar()
    return oidc.access_lapsed(conn, sub["user_sub"], email, now)


def prune_lapsed(conn, now: float | None = None) -> int:
    """Remove the devices whose person was taken off the sign-in list. Returns how many."""
    gone = [s["endpoint"] for s in db.rows(conn.execute(select(PushSubscription))) if lapsed(conn, s, now) == "user_removed"]
    for endpoint in gone:
        unsubscribe(conn, endpoint)
    return len(gone)


def subscribe(conn, sub: dict, device: str, user_sub: str | None) -> None:
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("That isn't a push subscription.")
    if not push_host_allowed(endpoint):   # Runway posts to this address, so it mustn't be just any server
        raise ValueError("That push service isn't one Runway knows. Add its host to RUNWAY_PUSH_HOSTS if you trust it.")
    if not webpush.valid_public_key(keys["p256dh"]):
        raise ValueError("That isn't a push subscription.")
    # The same browser subscribing again is whoever is signed in there now: the device becomes theirs. Only that
    # browser can give it away (it sends the keys Runway has), not someone who merely knows its address, whether
    # it's someone else's or nobody's yet (turned on before sign-in).
    had = conn.execute(select(PushSubscription.user_sub, PushSubscription.p256dh, PushSubscription.auth)
                       .where(PushSubscription.endpoint == endpoint)).fetchone()
    if had and (had["user_sub"] or None) != user_sub and (had["p256dh"], had["auth"]) != (keys["p256dh"], keys["auth"]):
        raise ValueError("That device gets someone else's notifications. Turn them off there first.")
    db.upsert(conn, PushSubscription, {"endpoint": endpoint, "p256dh": keys["p256dh"], "auth": keys["auth"],
                                       "device": (device or "This device")[:80], "user_sub": user_sub, "created": time.time()},
              key=["endpoint"], update=["p256dh", "auth", "device", "user_sub"])


# The browsers' push services (Chrome and Edge through Google or Windows, Firefox through Mozilla, Safari through Apple).
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
              "notify.windows.com")


_AUTHORITY = re.compile(r"([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*)(?::\d{1,5})?")


def push_host_allowed(endpoint: str) -> bool:
    r"""Whether an endpoint is on a push service Runway knows: the browsers' (PUSH_HOSTS, https only) or one you named
    in RUNWAY_PUSH_HOSTS (yours: http and a port are allowed, as in a test). The address has to be a plain one,
    scheme://host[:port]/path, with no user name, backslash or stray character before the path: the host is read
    here with urllib, and pywebpush connects with requests, whose parser reads a trickier address differently
    (`https://evil\@fcm.googleapis.com/` names fcm.googleapis.com to one and evil to the other). A plain address
    reads the same to both."""
    if not isinstance(endpoint, str) or not endpoint.isascii() or "\\" in endpoint or any(ord(ch) <= 32 or ord(ch) == 127 for ch in endpoint):
        return False
    p = urllib.parse.urlsplit(endpoint)
    m = _AUTHORITY.fullmatch(p.netloc.lower())   # the whole authority: anything else in it is refused, not read around
    if not m:
        return False
    host = m.group(1)
    extra = tuple(h.strip().lower() for h in (os.environ.get("RUNWAY_PUSH_HOSTS") or "").split(",") if h.strip())
    if p.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
        return True
    return p.scheme in ("https", "http") and any(host == h or host.endswith("." + h) for h in extra)


def unsubscribe(conn, endpoint: str) -> None:
    conn.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))


def subject(conn) -> str:
    """Who the push services can contact about these messages (required by Apple): your email, or Runway's address."""
    row = conn.execute(select(User.email).where(User.email.is_not(None)).order_by(User.last_seen.desc()).limit(1)).fetchone()
    if row and row["email"]:
        return f"mailto:{row['email']}"
    public = (os.environ.get("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    return public if public.startswith("https://") else "mailto:runway@example.com"


EVERYONE = object()   # send_all: every device, whoever's


def send_all(conn, message: dict, only: str | None = None, to: object = EVERYONE) -> dict:
    """Send to every device, or one person's (`to`: their sub, or None for the devices of nobody in particular), or one
    endpoint of those. Dead subscriptions are removed."""
    vapid, _pub = webpush.vapid_keys(conn)
    sent, failed = 0, []
    for s in subscriptions(conn):
        if (only and s["endpoint"] != only) or (to is not EVERYONE and (s["user_sub"] or None) != to):
            continue
        if not push_host_allowed(s["endpoint"]):   # from a restored backup, never checked when it was saved
            unsubscribe(conn, s["endpoint"])
            continue
        why = lapsed(conn, s)
        if why == "user_removed":   # subscriptions() pruned these, but a person may have been taken off since
            unsubscribe(conn, s["endpoint"])
            continue
        if why:   # not seen signing in lately: kept, and skipped until they do
            continue
        try:
            webpush.send(s, message, vapid, subject(conn))
            conn.execute(update(PushSubscription).where(PushSubscription.endpoint == s["endpoint"])
                         .values(last_ok=time.time(), last_error=None))
            sent += 1
        except webpush.Gone:
            unsubscribe(conn, s["endpoint"])
        except Exception as e:   # one bad device shouldn't stop the rest
            conn.execute(update(PushSubscription).where(PushSubscription.endpoint == s["endpoint"])
                         .values(last_error=str(e)[:300]))
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
    out = []
    fc = forecast.build(conn, today, max(LOW_BALANCE_DAYS, 30))
    if p["card_due"]:
        for c in fc["cards"]:
            days = (date.fromisoformat(c["due_date"]) - today).days
            if c["payment"] > 0.005 and 0 <= days <= p["card_due_days"]:   # what the forecast pays (all that's left, paid in full)
                pay = next((e for e in fc["events"] if e.get("kind") == "card" and e.get("key") == f"cardclose:{c['id']}:{c['last_close']}"
                            and e["name"].startswith(c["name"])), None)
                out.append({"key": f"card:{c['id']}:{c['due_date']}", "title": f"{c['name']} payment due {_when(c['due_date'], today)}",
                            "body": f"{_fmt(c['payment'])}" + (f" comes out of {pay['account']}." if pay else " is left to pay on this statement."),
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
        T = Transaction
        for t in conn.execute(
                select(T.id, T.amount, func.coalesce(T.payee, T.description).label("who"), db.account_label_expr().label("acct"))
                .join(Account, Account.id == T.account_id).outerjoin(Category, Category.name == T.category)
                .where(T.posted >= since, T.amount <= -float(p["big_charge_over"]),
                       *db.SPENDING_ACCOUNTS,
                       func.coalesce(Category.is_transfer, 0) == 0)).fetchall():
            out.append({"key": f"big:{t['id']}", "title": f"{_fmt(t['amount'])} at {t['who']}",
                        "body": f"On {t['acct']}.", "url": "/#transactions"})
    if p["review"]:
        n = conn.execute(select(func.count()).select_from(Transaction)
                         .where(Transaction.needs_review == 1, db.not_investment())).fetchone()[0]
        if n:
            out.append({"key": f"review:{today.isoformat()}", "title": f"{n} transaction{'s' if n != 1 else ''} to review",
                        "body": "They're waiting for a category.", "url": "/#review"})
    if p["sync_failed"]:
        last_ok = db.get_setting(conn, sk.LAST_SYNC_OK)
        log = conn.execute(select(SyncLog.ok, SyncLog.message).order_by(SyncLog.id.desc()).limit(1)).fetchone()
        stale = not last_ok or (date.today() - dates.parse_day(last_ok)).days >= 1
        if log and not log["ok"] and stale and banks.bank_configured(conn):   # SimpleFIN or Plaid
            # Not what the bank said: a notification shows on a lock screen, and goes through a push service.
            out.append({"key": f"syncfail:{today.isoformat()}", "title": "Runway can't sync with your bank",
                        "body": "The last sync failed. Open Settings → Connections to see what it said.", "url": "/#setup/connections"})
    out += churning.alerts(conn, today, p["churn_fee"], p["churn_bonus"], p["churn_plan"], p["churn_benefit"],
                            p["churn_apply"])
    return out


def _log_key(user_sub: str | None, key: str) -> str:
    """notify_log's key for an alert sent to a person ("@<their sub, hashed>:card:..."), or to everyone without sign-in."""
    return f"@{hashlib.sha256(user_sub.encode()).hexdigest()[:16]}:{key}" if user_sub else key


def recent(conn, user_sub: str | None, limit: int = 8) -> list[dict]:
    """What was last sent to a person (without sign-in, to everyone), newest first."""
    # (and what went to everyone before notifications were each person's own)
    mine = or_(NotifyLog.key.like(_log_key(user_sub, "") + "%"), NotifyLog.key.not_like("@%")) if user_sub else NotifyLog.key.not_like("@%")
    return db.rows(conn.execute(select(NotifyLog.title, NotifyLog.sent).where(mine).order_by(NotifyLog.sent.desc()).limit(limit)))


def run(conn, today: date | None = None) -> dict:
    """Send anything new, to each person's devices by what they want to hear about. Nothing is sent (or remembered)
    while someone has no device subscribed, so turning notifications on later doesn't bring a flood of old alerts,
    except what's still true that day. With sign-in, devices turned on before it (nobody's) get nothing."""
    today = today or date.today()
    owners = {r["user_sub"] or None for r in conn.execute(select(PushSubscription.user_sub).distinct())}
    people = sorted((o for o in owners if o), key=str) if oidc.enabled() else [None] if owners else []
    sent = 0
    for who in people:
        for a in alerts(conn, today, prefs(conn, who)):
            key = _log_key(who, a["key"])
            # (an alert sent before notifications were each person's own went to everyone: it counts as theirs)
            if conn.execute(select(NotifyLog.key).where(NotifyLog.key.in_({key, a["key"]}))).fetchone():
                continue
            # Saved before sending: if anything later rolled this back, the next run would send the same alert again.
            conn.execute(insert(NotifyLog).values(key=key, sent=time.time(), title=a["title"]))
            conn.commit()
            r = send_all(conn, {"title": a["title"], "body": a["body"], "url": a.get("url", "/"), "tag": a["key"]},
                         to=who if oidc.enabled() else EVERYONE)
            sent += r["sent"]
    conn.execute(delete(NotifyLog).where(NotifyLog.sent < time.time() - 120 * 86400))
    return {"sent": sent}
