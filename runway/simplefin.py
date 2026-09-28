"""SimpleFIN client: claim a setup token, fetch accounts and transactions, store them."""
from __future__ import annotations

import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import sfinvest, splits
from .categorize import clean_payee

CHUNK_DAYS = 85          # bridge limit is 90 days per request
BACKFILL_DAYS = 180      # history pulled on the first sync
REFRESH_DAYS = 14        # window re-read on routine syncs (catches pending -> posted)


class SimpleFinError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:  # python.org builds on macOS ship without system certs; use certifi when present
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


USER_AGENT = "Runway/0.1 (personal cash-flow app; +https://www.simplefin.org)"


def _describe_http_error(e: urllib.error.HTTPError) -> str:
    """A short, human-readable account of what the server sent back, for diagnosing failures."""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    server = e.headers.get("Server", "") if e.headers else ""
    blocked = "cloudflare" in server.lower() or "cf-ray" in {k.lower() for k in (e.headers or {}).keys()}
    text = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:160]
    parts = [f"HTTP {e.code}"]
    if blocked:
        parts.append("from Cloudflare (looks like a bot block, not a token problem)")
    if text:
        parts.append(f'server said: "{text}"')
    return "; ".join(parts)


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time setup token for a long-lived access URL."""
    token = setup_token.strip()
    try:
        claim_url = base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8").strip()
    except Exception as e:
        raise SimpleFinError("That doesn't look like a SimpleFIN setup token.") from e
    if not claim_url.startswith("https://"):
        raise SimpleFinError("That doesn't look like a SimpleFIN setup token.")
    req = urllib.request.Request(claim_url, data=b"", method="POST",
                                 headers={"Content-Length": "0", "User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
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
    params = {"start-date": str(int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())),
              "pending": "1"}
    if end is not None:
        end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
        params["end-date"] = str(int(end_dt.timestamp()))
    url = f"{base}/accounts?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _describe_http_error(e)
        if e.code == 403 and "cloudflare" not in detail.lower():
            raise SimpleFinError(f"SimpleFIN rejected the access credentials ({detail}). Reconnect in Settings.") from e
        raise SimpleFinError(f"SimpleFIN request failed ({detail}).") from e
    except urllib.error.URLError as e:
        raise SimpleFinError(f"Couldn't reach SimpleFIN: {e.reason}") from e
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
    # Bank dates are calendar days; read them in local time so a midnight-UTC stamp isn't shifted a day.
    return datetime.fromtimestamp(ts).date().isoformat()


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
    from . import plaidbank
    new_ids: list[str] = []
    claimed: set = set()
    for acct in payload.get("accounts", []):
        acct_id = str(acct["id"])
        setup = conn.execute("SELECT provider, provider_since FROM accounts WHERE id=?", (acct_id,)).fetchone()
        if setup and setup["provider"] == "plaid":
            continue   # this account's balance and transactions come from Plaid
        # Just switched back from Plaid: the last few days may already be here from Plaid.
        overlap = (setup and setup["provider_since"] and conn.execute(
            "SELECT 1 FROM transactions WHERE account_id=? AND id LIKE ? LIMIT 1", (acct_id, "%|pl:%")).fetchone())
        since = (date.fromisoformat(setup["provider_since"]) - timedelta(days=REFRESH_DAYS)).isoformat() if overlap else None
        name = acct.get("name") or acct_id
        org = (acct.get("org") or {}).get("name") or (acct.get("org") or {}).get("domain")
        balance = _to_float(acct.get("balance")) or 0.0
        available = _to_float(acct.get("available-balance"))
        bal_date = _ts_to_date(acct.get("balance-date")) or date.today().isoformat()
        existing = conn.execute("SELECT id FROM accounts WHERE id=?", (acct_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE accounts SET name=?, org=?, currency=?, balance=?, available=?, balance_date=? WHERE id=?",
                (name, org, acct.get("currency", "USD"), balance, available, bal_date, acct_id),
            )
        else:
            conn.execute(
                "INSERT INTO accounts(id, name, org, currency, balance, available, balance_date, kind) VALUES (?,?,?,?,?,?,?,?)",
                (acct_id, name, org, acct.get("currency", "USD"), balance, available, bal_date, guess_kind(name)),
            )

        sfinvest.capture(conn, acct, acct_id, org, balance, is_new=not existing)

        # Pending items often come back with new ids once they post. Replace this window's pending items
        # wholesale, but remember their categories so the replacements don't go back through review.
        carried: dict[tuple, list] = {}
        for old in conn.execute(
            "SELECT id, description, payee, amount, category, category_source, confidence, needs_review, is_split FROM transactions "
            "WHERE account_id=? AND pending=1 AND posted>=?",
            (acct_id, window_start.isoformat()),
        ).fetchall():
            if old["category"] or old["is_split"]:
                carried.setdefault((old["description"], round(old["amount"], 2)), []).append(dict(old))
        conn.execute(
            "DELETE FROM transactions WHERE account_id=? AND pending=1 AND posted>=?",
            (acct_id, window_start.isoformat()),
        )

        for tx in acct.get("transactions", []) or []:
            pending = 1 if tx.get("pending") else 0
            posted = _ts_to_date(tx.get("posted")) or _ts_to_date(tx.get("transacted_at")) or date.today().isoformat()
            amount = _to_float(tx.get("amount")) or 0.0
            desc = (tx.get("description") or tx.get("payee") or tx.get("memo") or "").strip()
            payee = clean_payee(tx.get("payee") or desc)
            key = f"{acct_id}|{tx['id']}"
            row = conn.execute("SELECT id, pending FROM transactions WHERE id=?", (key,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE transactions SET posted=?, amount=?, description=?, pending=? WHERE id=?",
                    (posted, amount, desc, pending, key),
                )
            else:
                if since and posted >= since and plaidbank.duplicate(conn, acct_id, posted, amount, False, claimed):
                    continue
                prior = carried.get((desc, round(amount, 2)))
                if prior:
                    p = prior.pop(0)
                    conn.execute(
                        "INSERT INTO transactions(id, account_id, posted, amount, description, payee, pending, "
                        "category, category_source, confidence, needs_review) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (key, acct_id, posted, amount, desc, p["payee"] or payee, pending,   # a rule may have renamed it
                         p["category"], p["category_source"], p["confidence"], p["needs_review"]),
                    )
                    if p["is_split"]:
                        splits.carry_over(conn, p["id"], key, amount)
                else:
                    conn.execute(
                        "INSERT INTO transactions(id, account_id, posted, amount, description, payee, pending) VALUES (?,?,?,?,?,?,?)",
                        (key, acct_id, posted, amount, desc, payee, pending),
                    )
                    new_ids.append(key)
    splits.prune(conn)
    return new_ids


def sync(conn, access_url: str, today: date | None = None, fetch=fetch_accounts) -> dict:
    """Pull recent data. First run backfills BACKFILL_DAYS in CHUNK_DAYS windows."""
    today = today or date.today()
    first_run = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    span = BACKFILL_DAYS if first_run else REFRESH_DAYS
    start = today - timedelta(days=span)
    new_ids: list[str] = []
    errors: list[str] = []
    chunk_start = start
    while chunk_start <= today:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), today)
        payload = fetch(access_url, chunk_start, chunk_end)
        errors.extend(str(e) for e in payload.get("errors", []) or [])
        new_ids.extend(store_payload(conn, payload, chunk_start))
        conn.commit()  # release the write lock before the next network call
        chunk_start = chunk_end + timedelta(days=1)
        if chunk_start <= today:
            time.sleep(0.5)
    return {"new": new_ids, "errors": errors, "backfill": first_run}
