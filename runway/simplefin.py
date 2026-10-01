"""SimpleFIN client: claim a setup token, fetch accounts and transactions, store them."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, UTC

from sqlalchemy import delete, insert, select, update

from . import db, deleted_accounts, plaidbank, sfinvest, splits
from . import settings_keys as sk
from .categorize import clean_payee
from .models import Account, Transaction

CHUNK_DAYS = 85          # bridge limit is 90 days per request
BACKFILL_DAYS = 180      # history pulled on the first sync
REFRESH_DAYS = 14        # window re-read on routine syncs (catches pending -> posted)
STALE_PENDING_DAYS = 30  # a hold this much older than a routine sync's window that still hasn't posted is gone


class SimpleFinError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:  # python.org builds on macOS ship without system certs; use certifi when present
        import certifi

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):   # certifi is optional; without it (or its bundle) the system certs still apply
        pass
    return ctx


USER_AGENT = "Runway/0.1 (personal cash-flow app; +https://www.simplefin.org)"


def _public_ip(address: str) -> bool:
    return ipaddress.ip_address(address.split("%")[0]).is_global


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    """An https connection that hangs up before saying anything if the address it reached isn't on the internet: the
    name may have been checked a moment ago (check_address) and point somewhere else now (DNS rebinding), or the
    address may come from a restored backup, which was never checked."""
    def connect(self):
        http.client.HTTPConnection.connect(self)   # just the TCP connection (and a proxy's tunnel, if one is set)
        # _tunnel_host and _context are http.client/urllib internals (not in their type stubs).
        if not self._tunnel_host and not _public_ip(self.sock.getpeername()[0]):  # type: ignore[attr-defined]  # through a proxy, it decides
            self.sock.close()
            raise SimpleFinError("That SimpleFIN address points at a private network address, which Runway won't contact.")
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)  # type: ignore[attr-defined]


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]  # urllib internal


class _NoPlainHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        raise SimpleFinError("A SimpleFIN address must start with https://. Reconnect SimpleFIN in Settings.")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """SimpleFIN answers where it's asked. A redirect would carry the access credentials (urllib keeps the
    Authorization header) to wherever it points, so none is followed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SimpleFinError(f"SimpleFIN answered HTTP {code}, sending Runway to another address, which it doesn't follow.")


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_PublicHTTPSHandler(context=_ssl_context()), _NoPlainHTTP(), _NoRedirects())


def _open(req: urllib.request.Request, timeout: float):
    """Every request to SimpleFIN: public https addresses only, no redirects."""
    return _opener().open(req, timeout=timeout)


def _describe_http_error(e: urllib.error.HTTPError) -> str:
    """A short, human-readable account of what the server sent back, for diagnosing failures."""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    server = e.headers.get("Server", "") if e.headers else ""
    blocked = "cloudflare" in server.lower() or "cf-ray" in {k.lower() for k in (e.headers or {})}
    text = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:160]
    parts = [f"HTTP {e.code}"]
    if blocked:
        parts.append("from Cloudflare (looks like a bot block, not a token problem)")
    if text:
        parts.append(f'server said: "{text}"')
    return "; ".join(parts)


def check_address(url: str) -> None:
    """SimpleFIN addresses come from what you paste, and Runway fetches them and shows what comes back, so they must
    be on the internet, not this machine or your network (where other services would answer)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SimpleFinError("That doesn't look like a SimpleFIN address.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        raise SimpleFinError(f"Couldn't reach SimpleFIN: can't find {parts.hostname}.") from e
    for info in infos:
        if not _public_ip(str(info[4][0])):   # an IPv4 or IPv6 address (the port is info[4][1])
            raise SimpleFinError("That SimpleFIN address points at a private network address, which Runway won't contact.")


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time setup token for a long-lived access URL."""
    token = setup_token.strip()
    try:
        claim_url = base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8").strip()
    except Exception as e:
        raise SimpleFinError("That doesn't look like a SimpleFIN setup token.") from e
    if not claim_url.startswith("https://"):
        raise SimpleFinError("That doesn't look like a SimpleFIN setup token.")
    check_address(claim_url)
    req = urllib.request.Request(claim_url, data=b"", method="POST",
                                 headers={"Content-Length": "0", "User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with _open(req, timeout=30) as resp:
            access_url = resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        detail = _describe_http_error(e)
        if e.code == 403 and "cloudflare" not in detail.lower():
            raise SimpleFinError(
                f"SimpleFIN refused the token ({detail}). A setup token works only once, and pasting it into "
                "any other app first uses it up. Create a new one in SimpleFIN Bridge and paste it only here."
            ) from e
        raise SimpleFinError(f"Couldn't claim the SimpleFIN token ({detail}).") from e
    except urllib.error.URLError as e:
        raise SimpleFinError(f"Couldn't reach SimpleFIN: {e.reason}") from e
    except (OSError, http.client.HTTPException, ValueError) as e:   # a timeout or a dropped connection mid-reply
        raise SimpleFinError(f"Couldn't reach SimpleFIN: {str(e) or type(e).__name__}") from e
    if not access_url.startswith("http"):
        raise SimpleFinError("SimpleFIN returned an unexpected response when claiming the token.")
    return access_url


def _split_access_url(access_url: str) -> tuple[str, str]:
    """Return (base_url_without_credentials, basic_auth_header)."""
    parts = urllib.parse.urlsplit(access_url)
    if not parts.username:
        raise SimpleFinError("Access URL is missing credentials.")
    user = urllib.parse.unquote(parts.username)
    pwd = urllib.parse.unquote(parts.password or "")
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    base = urllib.parse.urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
    auth = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return base, auth


def fetch_accounts(access_url: str, start: date, end: date | None = None) -> dict:
    base, auth = _split_access_url(access_url)
    params = {"start-date": str(int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())),
              "pending": "1"}
    if end is not None:
        end_dt = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
        params["end-date"] = str(int(end_dt.timestamp()))
    url = f"{base}/accounts?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with _open(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _describe_http_error(e)
        if e.code == 403 and "cloudflare" not in detail.lower():
            raise SimpleFinError(f"SimpleFIN rejected the access credentials ({detail}). Reconnect in Settings.") from e
        raise SimpleFinError(f"SimpleFIN request failed ({detail}).") from e
    except urllib.error.URLError as e:
        raise SimpleFinError(f"Couldn't reach SimpleFIN: {e.reason}") from e
    except (OSError, http.client.HTTPException) as e:   # a timeout or a dropped connection mid-reply
        raise SimpleFinError(f"Couldn't reach SimpleFIN: {str(e) or type(e).__name__}") from e
    except ValueError as e:   # not JSON: an error page, or a reply cut short
        raise SimpleFinError("SimpleFIN sent back a reply Runway couldn't read.") from e
    return payload


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts_to_date(ts) -> str | None:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    # Bank dates are calendar days, which banks stamp at midnight UTC (or midnight where they are: still that day in UTC).
    # Read a stamp in the morning hours of UTC as that UTC day, so a server in the US (TZ=America/Chicago is the default)
    # doesn't call Monday's midnight-UTC stamp Sunday evening. Later in the day it carries a real time (a card swipe): the
    # server's own time zone is the best guess at the day it happened.
    moment = datetime.fromtimestamp(ts, UTC)
    return (moment if moment.hour < 12 else datetime.fromtimestamp(ts)).date().isoformat()


CREDIT_WORDS = re.compile(r"card|visa|mastercard|amex|american express|sapphire|venture|credit|discover|aadvantage|freedom|quicksilver|strata|premier|rewards", re.I)
LOAN_WORDS = re.compile(r"loan|mortgage|auto loan|heloc", re.I)
INVEST_WORDS = re.compile(r"brokerage|ira|401k|roth|invest", re.I)


def guess_kind(name: str) -> str:
    if LOAN_WORDS.search(name):
        return "loan"
    if CREDIT_WORDS.search(name):
        return "credit"
    if INVEST_WORDS.search(name):
        return "investment"
    if re.search(r"saving", name, re.I):
        return "savings"
    return "checking"


def store_payload(conn, payload: dict, window_start: date) -> list[str]:
    """Upsert accounts and transactions. Returns ids of newly inserted transactions."""
    new_ids: list[str] = []
    claimed: set = set()
    deleted = deleted_accounts.ids(conn)
    for acct in payload.get("accounts", []):
        acct_id = str(acct["id"])
        if acct_id in deleted:
            continue   # you deleted it: it stays deleted until you restore it (deleted_accounts.py)
        setup = conn.execute(select(Account.provider, Account.provider_since).where(Account.id == acct_id)).fetchone()
        if setup and setup["provider"] == "plaid":
            continue   # this account's balance and transactions come from Plaid
        since = _plaid_overlap_since(conn, acct_id, setup)
        org, balance, existing = _upsert_account(conn, acct, acct_id)
        if not existing:
            deleted_accounts.relink(conn, acct_id)   # one you restored: linked to its Plaid account again
        sfinvest.capture(conn, acct, acct_id, org, balance, is_new=not existing)
        carried = _clear_pending(conn, acct_id, window_start)
        for tx in acct.get("transactions", []) or []:
            key = _store_transaction(conn, acct_id, tx, carried, since, claimed)
            if key:
                new_ids.append(key)
    splits.prune(conn)
    return new_ids


def _plaid_overlap_since(conn, acct_id: str, setup) -> str | None:
    """Just switched back from Plaid: the last few days may already be here from Plaid. The day from which new
    transactions are checked against Plaid's, or None when there's nothing to check."""
    overlap = (setup and setup["provider_since"] and conn.execute(
        select(Transaction.id).where(Transaction.account_id == acct_id, Transaction.id.like("%|pl:%")).limit(1)).fetchone())
    return (date.fromisoformat(setup["provider_since"]) - timedelta(days=REFRESH_DAYS)).isoformat() if overlap else None


def _upsert_account(conn, acct: dict, acct_id: str) -> tuple[str | None, float, bool]:
    """Save the account's name and balance. Returns its institution, its balance, and whether it was already here."""
    name = acct.get("name") or acct_id
    org = (acct.get("org") or {}).get("name") or (acct.get("org") or {}).get("domain")
    balance = _to_float(acct.get("balance")) or 0.0
    available = _to_float(acct.get("available-balance"))
    bal_date = _ts_to_date(acct.get("balance-date")) or date.today().isoformat()
    existing = conn.execute(select(Account.id).where(Account.id == acct_id)).fetchone()
    if existing:
        conn.execute(update(Account).where(Account.id == acct_id).values(
            name=name, org=org, currency=acct.get("currency", "USD"), balance=balance, available=available,
            balance_date=bal_date))
    else:
        conn.execute(insert(Account).values(
            id=acct_id, name=name, org=org, currency=acct.get("currency", "USD"), balance=balance, available=available,
            balance_date=bal_date, kind=guess_kind(name)))
    return org, balance, existing is not None


def _clear_pending(conn, acct_id: str, window_start: date) -> dict[tuple, list]:
    """Pending items often come back with new ids once they post. Replace this window's pending items wholesale,
    but remember their categories (by description and amount) so the replacements don't go back through review."""
    carried: dict[tuple, list] = {}
    in_window = (Transaction.account_id == acct_id, Transaction.pending == 1, Transaction.posted >= window_start.isoformat())
    for old in conn.execute(
        select(Transaction.id, Transaction.description, Transaction.payee, Transaction.amount, Transaction.category,
               Transaction.category_source, Transaction.confidence, Transaction.needs_review, Transaction.is_split)
        .where(*in_window)
    ).fetchall():
        if old["category"] or old["is_split"]:
            carried.setdefault((old["description"], round(old["amount"], 2)), []).append(dict(old))
    conn.execute(delete(Transaction).where(*in_window))
    # Older than any window re-read: a hold that dropped off without posting would otherwise stay forever.
    conn.execute(delete(Transaction).where(
        Transaction.account_id == acct_id, Transaction.pending == 1,
        Transaction.posted < (window_start - timedelta(days=STALE_PENDING_DAYS - REFRESH_DAYS)).isoformat()))
    return carried


def _store_transaction(conn, acct_id: str, tx: dict, carried: dict[tuple, list], since: str | None, claimed: set) -> str | None:
    """Save one transaction. Returns its id if it's new and needs categorizing; None if it was already here, is a
    copy of one Plaid brought in, or took over a pending item's category."""
    pending = 1 if tx.get("pending") else 0
    posted = _ts_to_date(tx.get("posted")) or _ts_to_date(tx.get("transacted_at")) or date.today().isoformat()
    amount = _to_float(tx.get("amount")) or 0.0
    desc = (tx.get("description") or tx.get("payee") or tx.get("memo") or "").strip()
    payee = clean_payee(tx.get("payee") or desc)
    key = f"{acct_id}|{tx['id']}"
    row = conn.execute(select(Transaction.id, Transaction.pending, Transaction.is_split).where(Transaction.id == key)).fetchone()
    if row:
        conn.execute(update(Transaction).where(Transaction.id == key).values(
            posted=posted, amount=amount, description=desc, pending=pending))
        if row["is_split"]:
            splits.follow_amount(conn, key, amount)
        return None
    if since and posted >= since and plaidbank.duplicate(conn, acct_id, posted, amount, False, claimed):
        return None
    prior = carried.get((desc, round(amount, 2)))
    if prior:
        p = prior.pop(0)
        conn.execute(insert(Transaction).values(
            id=key, account_id=acct_id, posted=posted, amount=amount, description=desc,
            payee=p["payee"] or payee, pending=pending,   # a rule may have renamed it
            category=p["category"], category_source=p["category_source"], confidence=p["confidence"],
            needs_review=p["needs_review"]))
        if p["is_split"]:
            splits.carry_over(conn, p["id"], key, amount)
        return None
    conn.execute(insert(Transaction).values(
        id=key, account_id=acct_id, posted=posted, amount=amount, description=desc, payee=payee, pending=pending))
    return key


def _backfill_state(conn) -> tuple[set, set]:
    """SimpleFIN accounts ever seen, and those whose BACKFILL_DAYS of history were read in full."""
    st = json.loads(db.get_setting(conn, sk.SIMPLEFIN_BACKFILL) or "{}")
    return set(st.get("seen") or []), set(st.get("done") or [])


def _save_backfill_state(conn, seen: set, done: set) -> None:
    db.set_setting(conn, sk.SIMPLEFIN_BACKFILL, json.dumps({"seen": sorted(seen), "done": sorted(done)}))


def sync(conn, access_url: str, today: date | None = None, fetch=fetch_accounts) -> dict:
    """Pull recent data. Backfills BACKFILL_DAYS in CHUNK_DAYS windows until every account SimpleFIN has shown has had
    its history read in full: on the first run, after a backfill that stopped part way, and for a bank added later."""
    today = today or date.today()
    seen, done = _backfill_state(conn)
    backfill = not seen or bool(seen - done)
    span = BACKFILL_DAYS if backfill else REFRESH_DAYS
    start = today - timedelta(days=span)
    new_ids: list[str] = []
    errors: list[str] = []
    got: set = set()
    chunk_start = start
    while chunk_start <= today:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), today)
        payload = fetch(access_url, chunk_start, chunk_end)
        errors.extend(str(e) for e in payload.get("errors", []) or [])
        new_ids.extend(store_payload(conn, payload, chunk_start))
        got |= {str(a["id"]) for a in payload.get("accounts", []) or []}
        seen |= got
        _save_backfill_state(conn, seen, done)
        conn.commit()  # release the write lock before the next network call
        chunk_start = chunk_end + timedelta(days=1)
        if chunk_start <= today:
            time.sleep(0.5)
    if backfill:   # every window came back: those accounts' history is complete
        done |= got
        _save_backfill_state(conn, seen, done)
    return {"new": new_ids, "errors": errors, "backfill": backfill}
